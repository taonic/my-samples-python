"""Requirement resolution: what is needed next, and why.

`resolve_requirements` is a pure function of (schema, data, results, actions).
It never runs activities, reads the clock, or generates ids, so the workflow can
call it as often as it likes and replay will always reach the same answer.

For each field it decides, in order:

1. Applicability: `required`, or `requiredWhen` evaluated against the other
   fields. False -> not_applicable; unknown -> blocked.
2. Readiness (activity fields): every `dependsOn` field is satisfied or not
   applicable, and `readyWhen` holds. A definitively false `readyWhen` marks the
   requirement impossible until the data is corrected.
3. Satisfaction: a valid human value, or an activity result whose input
   fingerprint matches the current inputs. A result computed from older inputs
   is stale and satisfies nothing.

`context` holds read-only data carried over from earlier forms (see
`select_next`). Rules read it as `<namespace>.<field>`, and number bounds can
come from it. It is always fully known.
"""

import hashlib
import json
from typing import Any, Callable, Mapping, Optional

from .model import (
    ActivityResult,
    DecisionRecord,
    FieldDef,
    NextSchema,
    PlannedAction,
    Progress,
    Requirement,
    ResolutionResult,
    Schema,
)
from .rules import ABSENT, UNKNOWN, Lookup, describe, evaluate, lookup_path, referenced_fields
from .schema import validate_value


def fingerprint(field_id: str, inputs: Mapping[str, Any]) -> str:
    """Stable digest of the inputs an activity requirement would run with."""
    canonical = json.dumps([field_id, inputs], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def action_id_for(fdef: FieldDef, fp: str) -> str:
    return f"{fdef.collection.activity}:{fp}"


def resolve_requirements(
    schema: Schema,
    data: Mapping[str, Any],
    results: Mapping[str, Mapping[str, ActivityResult]],
    actions: Mapping[str, PlannedAction],
    context: Optional[Mapping[str, Any]] = None,
) -> ResolutionResult:
    reqs: dict[str, Requirement] = {}
    values: dict[str, Any] = {}
    context = context or {}

    def lookup(path: str) -> Any:
        fid, _, key = path.partition(".")
        if fid in schema.context:
            return lookup_path(context, path)
        req = resolve(fid)
        if req.status == "not_applicable":
            return ABSENT
        if req.status != "satisfied":
            return UNKNOWN
        value = values[fid]
        if not key:
            return value
        return value.get(key, ABSENT) if isinstance(value, dict) else ABSENT

    def resolve(fid: str) -> Requirement:
        # The schema graph is validated acyclic, so plain memoised recursion is safe.
        if fid not in reqs:
            reqs[fid] = _resolve_field(
                fid, schema.fields[fid], data, results, actions, lookup, status_of, values, context
            )
        return reqs[fid]

    def status_of(dep: str) -> str:
        # An activity may depend on carried-over context, e.g. "application.annual_income".
        # Context is fully known: present counts as satisfied, absent as not applicable.
        if dep.split(".", 1)[0] in schema.context:
            return "not_applicable" if lookup_path(context, dep) is ABSENT else "satisfied"
        return resolve(dep).status

    requirements = [resolve(fid) for fid in schema.field_order or schema.fields]
    complete = all(r.status in ("satisfied", "not_applicable") for r in requirements)
    outcome = _outcome(schema, lookup) if complete else None
    retire = [
        r.id
        for r in requirements
        if r.kind == "human" and r.status == "not_applicable" and r.id in data
    ]
    return ResolutionResult(
        requirements=requirements,
        complete=complete,
        outcome=outcome,
        progress=None if complete else _progress(requirements),
        values=values,
        retire=retire,
    )


def _resolve_field(
    fid: str,
    fdef: FieldDef,
    data: Mapping[str, Any],
    results: Mapping[str, Mapping[str, ActivityResult]],
    actions: Mapping[str, PlannedAction],
    lookup: Lookup,
    status_of: Callable[[str], str],
    values: dict[str, Any],
    context: Mapping[str, Any],
) -> Requirement:
    kind = "human" if fdef.collection.strategy == "human" else "activity"
    deps = fdef.depends_on if kind == "activity" else referenced_fields(fdef.required_when)
    req = Requirement(
        id=fid,
        status="missing",
        kind=kind,
        dependencies=list(deps),
        collection_strategy=fdef.collection.activity or "human",
    )

    if not fdef.required:
        applicable = evaluate(fdef.required_when or {}, lookup)
        if applicable is False:
            req.status = "not_applicable"
            req.reason = f"only required when {describe(fdef.required_when)}"
            return req
        if applicable is None:
            req.status = "blocked"
            refs = ", ".join(referenced_fields(fdef.required_when))
            req.reason = f"applicability depends on {refs}"
            return req

    if kind == "human":
        if fid not in data:
            req.reason = "awaiting input"
            return req
        error = validate_value(fdef, data[fid], context)
        if error:
            req.reason = f"invalid value: {error}"
            return req
        req.status = "satisfied"
        values[fid] = data[fid]
        return req

    # Activity requirement: dependencies first, then the readiness gate.
    dep_reqs = {d: status_of(d) for d in fdef.depends_on}
    unmet = [d for d, status in dep_reqs.items() if status not in ("satisfied", "not_applicable")]
    if unmet:
        req.status = "blocked"
        req.reason = f"waiting for {', '.join(unmet)}"
        return req
    if fdef.ready_when is not None:
        ready = evaluate(fdef.ready_when, lookup)
        if ready is not True:
            req.status = "blocked"
            if ready is False:
                req.impossible = True
                req.reason = f"cannot run unless {describe(fdef.ready_when)}"
            else:
                req.reason = f"waiting for {', '.join(referenced_fields(fdef.ready_when))}"
            return req

    inputs = {d: lookup(d) for d, status in dep_reqs.items() if status == "satisfied"}
    fp = fingerprint(fid, inputs)
    req.fingerprint = fp
    req.inputs = inputs

    result = results.get(fid, {}).get(fp)
    if result is not None:
        req.status = "satisfied"
        req.reason = f"result from {result.action_id}"
        values[fid] = result.value
        return req
    action = actions.get(action_id_for(fdef, fp))
    if action is not None and action.status in ("pending", "running"):
        req.status = "in_progress"
        req.reason = f"{action.id} is {action.status}"
    elif action is not None and action.status == "failed":
        req.status = "failed"
        req.reason = f"{action.id} failed: {action.error}"
    else:
        stale = bool(results.get(fid))
        req.reason = (
            "inputs changed since the last result; needs a fresh run"
            if stale
            else f"ready to run {fdef.collection.activity}"
        )
    return req


def _outcome(schema: Schema, lookup: Lookup) -> str:
    for rule in schema.outcome:
        if rule.when is None or evaluate(rule.when, lookup) is True:
            return rule.result
    return schema.outcome[-1].result


def _progress(requirements: list[Requirement]) -> Progress:
    """Why the process is not complete, from the most to the least actionable."""
    statuses = {r.status for r in requirements}
    if "failed" in statuses:
        return "action_failed"
    if any(r.impossible for r in requirements):
        return "impossible"
    if any(r.kind == "human" and r.status == "missing" for r in requirements):
        return "waiting_for_human"
    if any(r.kind == "activity" and r.status in ("missing", "in_progress") for r in requirements):
        return "waiting_for_automation"
    # Only blocked requirements remain and nothing can unblock them.
    return "impossible"


def explain(
    revision: int, trigger: str, resolution: ResolutionResult, new_actions: list[str]
) -> DecisionRecord:
    """Summarise one evaluation as a structured decision record."""
    by_status: dict[str, list[str]] = {}
    for r in resolution.requirements:
        by_status.setdefault(r.status, []).append(r.id)
    if resolution.complete:
        reason = f"all applicable requirements satisfied; outcome {resolution.outcome}"
    else:
        reqs = resolution.requirements
        parts = []
        needs = [r.id for r in reqs if r.kind == "human" and r.status == "missing"]
        if needs:
            parts.append(f"needs input: {', '.join(needs)}")
        running = [r.id for r in reqs if r.status == "in_progress"]
        if running:
            parts.append(f"running: {', '.join(running)}")
        parts += [
            f"{r.id} {r.status}: {r.reason}"
            for r in reqs
            if r.kind == "activity" and (r.status in ("blocked", "failed"))
        ]
        reason = "; ".join(parts) or f"progress: {resolution.progress}"
    return DecisionRecord(
        revision=revision,
        trigger=trigger,
        satisfied=by_status.get("satisfied", []),
        missing=by_status.get("missing", []),
        blocked=by_status.get("blocked", []),
        in_progress=by_status.get("in_progress", []),
        failed=by_status.get("failed", []),
        not_applicable=by_status.get("not_applicable", []),
        new_actions=new_actions,
        reason=reason,
    )


def current_fingerprint(resolution: ResolutionResult, fid: str) -> Optional[str]:
    req = resolution.by_id().get(fid)
    return req.fingerprint if req else None


def select_next(
    schema: Schema, resolution: ResolutionResult, context: Optional[Mapping[str, Any]] = None
) -> Optional[NextSchema]:
    """The follow-up form to continue into, if any `next` entry matches the completed form.

    `when` rules see this form's final values, its derived `outcome`, and any
    context it was given itself.
    """
    if not resolution.complete:
        return None
    context = context or {}

    def lookup(path: str) -> Any:
        fid = path.split(".", 1)[0]
        if path == "outcome":
            return resolution.outcome
        if fid in schema.context:
            return lookup_path(context, path)
        return lookup_path(resolution.values, path)

    for entry in schema.next:
        if entry.when is None or evaluate(entry.when, lookup) is True:
            return entry
    return None
