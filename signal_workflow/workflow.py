import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow


@dataclass
class GroupParams:
    """Parameters of the group being formed."""
    group_id: str
    target_size: int
    registration_window_seconds: int


@dataclass
class JoinInput:
    """Signal payload: a user asking to join a group."""
    user_id: str
    group_id: str


@dataclass
class GroupResult:
    group_id: str
    members: list[str]
    filled: bool


def group_workflow_id(group_id: str) -> str:
    """Deterministic Workflow ID for a group, so anyone can address it."""
    return f"group-workflow-{group_id}"


@workflow.defn
class GroupWorkflow:
    """Collects members via signals until the group fills or the window closes."""

    def __init__(self) -> None:
        self.members: list[JoinInput] = []

    @workflow.run
    async def run(self, params: GroupParams) -> GroupResult:
        # Take joins until the group fills up or the registration window closes.
        # `wait_condition` re-evaluates the predicate every time the workflow
        # state changes -- which is exactly what a signal does.
        filled = True
        try:
            await workflow.wait_condition(
                lambda: len(self.members) >= params.target_size,
                timeout=timedelta(seconds=params.registration_window_seconds),
            )
            workflow.logger.info("Group %s filled", params.group_id)
        except asyncio.TimeoutError:
            # A `wait_condition` timeout raises asyncio.TimeoutError, not a
            # Temporal timeout error. Catching it means "window closed", not
            # "workflow failed".
            filled = False
            workflow.logger.info(
                "Registration window closed for %s with %d/%d members",
                params.group_id,
                len(self.members),
                params.target_size,
            )

        return GroupResult(
            group_id=params.group_id,
            members=[member.user_id for member in self.members],
            filled=filled,
        )

    @workflow.signal(name="join")
    def join(self, join_input: JoinInput) -> None:
        """Handle a join signal.
        """
        self.members.append(join_input)


@workflow.defn
class JoinGroupWorkflow:
    """A workflow that signals another (already running) workflow.

    This is the workflow-to-workflow path: no client involved, and the signal is
    recorded in this workflow's history as a
    SignalExternalWorkflowExecutionInitiated event, so it is retried and
    deduplicated by the server like any other durable command.
    """

    @workflow.run
    async def run(self, join_input: JoinInput) -> None:
        handle = workflow.get_external_workflow_handle_for(
            GroupWorkflow.run,
            group_workflow_id(join_input.group_id),
        )
        # Awaiting resolves once the server accepts the signal. If the target
        # workflow is not running, this raises instead of silently no-op'ing.
        await handle.signal(
            GroupWorkflow.join,
            join_input,
        )
        workflow.logger.info("Signalled join for %s", join_input.user_id)
