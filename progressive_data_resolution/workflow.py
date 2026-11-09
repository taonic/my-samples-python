import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Optional, Union

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import LoadSchemaInput, load_schema
    from .core.model import (
        ActivityResult,
        DecisionRecord,
        NextSchema,
        PhaseSummary,
        PlannedAction,
        ResolutionResult,
        ResolutionState,
        RetainedValue,
        Schema,
    )
    from .core.planner import plan_actions
    from .core.resolver import explain, resolve_requirements, select_next
    from .core.schema import check_context
    from .core.submission import check_submission

MAX_DECISIONS = 50
ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_attempts=3
)


@dataclass
class WorkflowInput:
    schema_id: str
    schema_version: str
    initial_data: dict[str, Any] = field(default_factory=dict)
    inactivity_timeout_seconds: float = 7 * 24 * 60 * 60
    # Set when continuing from an earlier form: its final values by namespace,
    # and a summary of each form completed so far.
    context: dict[str, dict[str, Any]] = field(default_factory=dict)
    phases: list[PhaseSummary] = field(default_factory=list)


@dataclass
class Submission:
    submission_id: str
    base_revision: int
    values: dict[str, Any]


@dataclass
class SubmissionResult:
    revision: int
    changed_fields: list[str]
    duplicate: bool = False


@dataclass
class RetryActionInput:
    action_id: str


@dataclass
class ResolutionOutcome:
    status: str
    reason: Optional[str]
    outcome: Optional[str]
    revision: int
    data: dict[str, Any]
    results: dict[str, ActivityResult]
    decisions: list[str]
    phases: list[PhaseSummary] = field(default_factory=list)


# Events on the inbox. Handlers and activity tasks only ever append to the inbox;
# the main loop is the one place state changes, so there are no interleaved
# mutations to reason about.
@dataclass
class _SubmissionEvent:
    submission: Submission
    seq: int = -1


@dataclass
class _RetryEvent:
    action_id: str
    seq: int = -1


@dataclass
class _ActivityDone:
    action_id: str
    value: dict[str, Any]


@dataclass
class _ActivityFailed:
    action_id: str
    error: str


_Event = Union[_SubmissionEvent, _RetryEvent, _ActivityDone, _ActivityFailed]


@workflow.defn
class ProgressiveDataResolutionWorkflow:
    """A declarative reconciliation loop.

    Desired state: every applicable requirement in the schema is satisfied.
    Each event (a submission, an activity result, a retry) changes the source
    data; the loop then re-resolves requirements, reconciles actions against
    them, starts any newly runnable activities and waits for the next event.
    Nothing here knows about loan applications: the path is the schema's.

    When a form completes and its schema names a matching `next` form, the
    workflow continues-as-new into it, handing over its final values as
    context. The workflow id stays the same, so clients simply follow along,
    and each form gets a fresh event history.
    """

    @workflow.init
    def __init__(self, params: WorkflowInput) -> None:
        self._schema: Optional[Schema] = None
        self._status = "running"
        self._status_reason: Optional[str] = None
        self._revision = 0
        self._data: dict[str, Any] = dict(params.initial_data)
        self._field_revisions: dict[str, int] = {}
        self._retained: dict[str, list[RetainedValue]] = {}
        self._results: dict[str, dict[str, ActivityResult]] = {}
        self._actions: dict[str, PlannedAction] = {}
        self._resolution: Optional[ResolutionResult] = None
        self._decisions: list[DecisionRecord] = []
        self._applied: dict[str, SubmissionResult] = {}
        self._inbox: deque[_Event] = deque()
        self._replies: dict[int, Union[SubmissionResult, int, ApplicationError]] = {}
        self._next_seq = 0
        self._closed = False
        self._tasks: set[asyncio.Task] = set()
        self._context = params.context
        self._phases = list(params.phases)
        self._next: Optional[NextSchema] = None

    @workflow.run
    async def run(self, params: WorkflowInput) -> ResolutionOutcome:
        await self._load_schema(params)
        self._reconcile("workflow started")
        try:
            await self._run_form(timedelta(seconds=params.inactivity_timeout_seconds))
        except asyncio.CancelledError:
            self._finish("cancelled", "cancellation requested")
            raise
        finally:
            self._drain()

        await workflow.wait_condition(workflow.all_handlers_finished)
        if self._next is not None:
            workflow.continue_as_new(self._next_input(self._next, params))
        return self._outcome()

    async def _load_schema(self, params: WorkflowInput) -> None:
        """Load the schema once and keep it: replay reads it back from history,
        so this execution stays pinned to the version it started with."""
        try:
            self._schema = await workflow.execute_activity(
                load_schema,
                LoadSchemaInput(params.schema_id, params.schema_version),
                start_to_close_timeout=timedelta(seconds=10),
            )
        except ActivityError as err:
            self._finish("failed", f"schema could not be loaded: {err.cause}")
            raise ApplicationError(self._status_reason or "", type="SchemaLoadFailed") from err
        if errors := check_context(self._schema, self._context):
            self._finish("failed", f"context does not fit the schema: {'; '.join(errors)}")
            raise ApplicationError(self._status_reason or "", type="InvalidContext")

    async def _run_form(self, inactivity: timedelta) -> None:
        """Apply events until the form completes or goes idle, then close it.

        One event per pass, blocking only when nothing is queued. Events still
        queued once the form completes are rejected by _drain.
        """
        self._idle_since = workflow.now()
        while not self._resolution_or_raise().complete:
            if not self._inbox:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._inbox),
                        timeout=max(self._idle_since + inactivity - workflow.now(), timedelta(0)),
                        timeout_summary="inactivity timeout",
                    )
                except asyncio.TimeoutError:
                    self._finish("cancelled", "inactivity_timeout")
                    return
            trigger = self._apply(self._inbox.popleft())
            if trigger:
                self._reconcile(trigger)

        res = self._resolution_or_raise()
        self._next = select_next(self._schema_or_raise(), res, self._context)
        if self._next is None:
            self._finish("completed", f"outcome: {res.outcome}")
        else:
            # Closed for input, but still running until it continues.
            nxt = f"{self._next.schema_id} v{self._next.version}"
            self._finish("running", f"outcome: {res.outcome}; continuing to {nxt}")

    # ---- External inputs ------------------------------------------------------

    @workflow.update
    async def submit(self, submission: Submission) -> SubmissionResult:
        """Apply human input. Returns once the loop has applied it and re-evaluated."""
        if submission.submission_id in self._applied:
            return replace(self._applied[submission.submission_id], duplicate=True)
        return await self._request(_SubmissionEvent(submission))

    @submit.validator
    def _validate_submit(self, submission: Submission) -> None:
        # A cheap, read-only pre-check. Rejections here never reach history. The
        # loop re-checks authoritatively, because another update may be applied
        # between this validator and that one.
        if submission.submission_id in self._applied:
            return
        if self._closed:
            raise ApplicationError("Application is closed", type="ApplicationClosed")
        if self._schema is not None:
            self._raise_if_rejected(submission)

    @workflow.update
    async def retry_action(self, params: RetryActionInput) -> int:
        """Re-arm an action whose retries were exhausted. Returns the new revision."""
        return await self._request(_RetryEvent(params.action_id))

    @retry_action.validator
    def _validate_retry(self, params: RetryActionInput) -> None:
        if self._closed:
            raise ApplicationError("Application is closed", type="ApplicationClosed")
        action = self._actions.get(params.action_id)
        if action is None or action.status != "failed":
            raise ApplicationError(
                f"{params.action_id} is not a failed action", type="NotRetryable"
            )

    @workflow.query
    def get_state(self) -> ResolutionState:
        res = self._resolution
        return ResolutionState(
            schema_id=self._schema.id if self._schema else "",
            schema_version=self._schema.version if self._schema else "",
            status=self._status,  # type: ignore[arg-type]
            revision=self._revision,
            schema=self._schema,
            status_reason=self._status_reason,
            progress=res.progress if res and not self._closed else None,
            outcome=res.outcome if res else None,
            data=self._data,
            field_revisions=self._field_revisions,
            retained=self._retained,
            results=self._results,
            values=res.values if res else {},
            requirements=res.requirements if res else [],
            actions=list(self._actions.values()),
            decisions=self._decisions,
            context=self._context,
            phases=self._phases,
        )

    async def _request(self, event: Union[_SubmissionEvent, _RetryEvent]) -> Any:
        event.seq = self._next_seq
        self._next_seq += 1
        self._inbox.append(event)
        await workflow.wait_condition(lambda: event.seq in self._replies)
        reply = self._replies.pop(event.seq)
        if isinstance(reply, ApplicationError):
            raise reply
        return reply

    # ---- The reconciliation loop -------------------------------------------------

    def _apply(self, event: _Event) -> Optional[str]:
        """Apply one event to source state. Returns a trigger description if anything changed."""
        if isinstance(event, _SubmissionEvent):
            return self._apply_submission(event)
        if isinstance(event, _RetryEvent):
            action = self._actions.get(event.action_id)
            if action is None or action.status != "failed":
                self._replies[event.seq] = ApplicationError(
                    f"{event.action_id} is not a failed action", type="NotRetryable"
                )
                return None
            self._revision += 1
            action.status, action.error = "pending", None
            self._replies[event.seq] = self._revision
            return f"retry requested for {action.id}"

        action = self._actions[event.action_id]
        fid = action.requirement_ids[0]
        fp = action.parameters["fingerprint"]
        stale = self._resolution_or_raise().by_id()[fid].fingerprint != fp
        action.stale = stale
        self._revision += 1
        if isinstance(event, _ActivityFailed):
            action.status, action.error = "failed", event.error
            return f"{action.id} failed after retries: {event.error}"
        action.status = "completed"
        self._results.setdefault(fid, {})[fp] = ActivityResult(
            fingerprint=fp, action_id=action.id, revision=self._revision, value=event.value
        )
        if stale:
            return f"{action.id} completed with stale inputs; kept for audit, not used"
        return f"{action.id} completed"

    def _apply_submission(self, event: _SubmissionEvent) -> Optional[str]:
        sub = event.submission
        if sub.submission_id in self._applied:
            self._replies[event.seq] = replace(self._applied[sub.submission_id], duplicate=True)
            return None
        try:
            self._raise_if_rejected(sub)
        except ApplicationError as err:
            self._replies[event.seq] = err
            return None

        changed = sorted(
            f for f, v in sub.values.items() if f not in self._data or self._data[f] != v
        )
        if changed:
            self._revision += 1
            for f in changed:
                self._data[f] = sub.values[f]
                self._field_revisions[f] = self._revision
        self._idle_since = workflow.now()
        result = SubmissionResult(revision=self._revision, changed_fields=changed)
        self._applied[sub.submission_id] = result
        self._replies[event.seq] = result
        return f"submission {sub.submission_id} set {', '.join(changed)}" if changed else None

    def _raise_if_rejected(self, sub: Submission) -> None:
        check = check_submission(
            self._schema_or_raise(),
            self._data,
            self._results,
            self._actions,
            self._field_revisions,
            sub.base_revision,
            sub.values,
            self._context,
        )
        if check.invalid:
            raise ApplicationError(
                "Invalid submission", {"errors": check.invalid}, type="InvalidSubmission"
            )
        if check.conflicts:
            raise ApplicationError(
                "Submission is based on outdated state",
                {"conflicts": check.conflicts, "revision": self._revision},
                type="StaleSubmission",
            )

    def _reconcile(self, trigger: str) -> None:
        """Re-resolve requirements, reconcile actions, start runnable activities."""
        schema = self._schema_or_raise()
        res = resolve_requirements(schema, self._data, self._results, self._actions, self._context)

        # Values for fields that no longer apply are kept for audit but taken out
        # of the source data, so they cannot satisfy the field if it applies again.
        for fid in res.retire:
            self._retained.setdefault(fid, []).append(
                RetainedValue(value=self._data.pop(fid), retired_at_revision=self._revision)
            )
            self._field_revisions[fid] = self._revision

        plan = plan_actions(schema, res, self._actions)
        self._actions = plan.actions
        started = False
        for action in self._actions.values():
            if action.type == "execute_activity" and action.status == "pending":
                self._start_activity(action)
                started = True
        if started:
            # Re-resolve so the requirements we just started show as in progress.
            res = resolve_requirements(
                schema, self._data, self._results, self._actions, self._context
            )

        self._resolution = res
        running = any(a.status == "running" for a in self._actions.values())
        self._status = "running" if running else "waiting_for_input"
        decision = explain(self._revision, trigger, res, plan.new_action_ids)
        self._decisions = (self._decisions + [decision])[-MAX_DECISIONS:]
        workflow.logger.info("rev %d [%s] %s", self._revision, trigger, decision.reason)

    def _start_activity(self, action: PlannedAction) -> None:
        action.status = "running"
        action.attempts += 1
        task = asyncio.create_task(self._run_activity(action))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_activity(self, action: PlannedAction) -> None:
        # The result is posted to the inbox rather than applied here; the loop
        # decides whether it still matches the current inputs.
        try:
            value = await workflow.execute_activity(
                action.parameters["activityType"],
                action.parameters["inputs"],
                activity_id=f"{action.id}#{action.attempts}",
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=ACTIVITY_RETRY,
                result_type=dict,
                summary=action.id,
            )
        except ActivityError as err:
            cause = err.cause
            message = cause.message if isinstance(cause, ApplicationError) else str(cause or err)
            self._inbox.append(_ActivityFailed(action.id, message))
            return
        self._inbox.append(_ActivityDone(action.id, value))

    # ---- Helpers --------------------------------------------------------------

    def _finish(self, status: str, reason: str) -> None:
        self._status = status
        self._status_reason = reason
        self._closed = True

    def _drain(self) -> None:
        """Answer requests that arrived after the process closed."""
        for event in self._inbox:
            if isinstance(event, (_SubmissionEvent, _RetryEvent)):
                self._replies[event.seq] = ApplicationError(
                    "Application is closed", type="ApplicationClosed"
                )
        self._inbox.clear()

    def _outcome(self) -> ResolutionOutcome:
        res = self._resolution_or_raise()
        by_id = res.by_id()
        return ResolutionOutcome(
            status=self._status,
            reason=self._status_reason,
            outcome=res.outcome,
            revision=self._revision,
            data={f: v for f, v in res.values.items() if by_id[f].kind == "human"},
            results={
                f: self._results[f][by_id[f].fingerprint or ""]
                for f in res.values
                if by_id[f].kind == "activity"
            },
            decisions=[f"rev {d.revision} [{d.trigger}] {d.reason}" for d in self._decisions],
            phases=self._phases,
        )

    def _next_input(self, nxt: NextSchema, params: WorkflowInput) -> WorkflowInput:
        """Input for the follow-up form: this form's final values become its context."""
        schema, res = self._schema_or_raise(), self._resolution_or_raise()
        return WorkflowInput(
            schema_id=nxt.schema_id,
            schema_version=nxt.version,
            inactivity_timeout_seconds=params.inactivity_timeout_seconds,
            context={**self._context, nxt.as_namespace: dict(res.values)},
            phases=self._phases
            + [PhaseSummary(schema.id, schema.version, res.outcome, self._revision)],
        )

    def _schema_or_raise(self) -> Schema:
        if self._schema is None:
            raise ApplicationError("Schema not loaded yet", type="NotReady")
        return self._schema

    def _resolution_or_raise(self) -> ResolutionResult:
        if self._resolution is None:
            raise ApplicationError("Not evaluated yet", type="NotReady")
        return self._resolution
