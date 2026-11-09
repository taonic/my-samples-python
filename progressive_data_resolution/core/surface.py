"""Surface rendering: project workflow state into UI components.

The surface is derived entirely from the authoritative `ResolutionState`
returned by the workflow query. It holds no process knowledge of its own: which
fields appear, and in what state, comes from requirement statuses; how they
appear comes from field types in the pinned schema.

Every applicable human field is rendered: missing ones empty, satisfied ones
prefilled so they can be corrected. Blocked fields (applicability not yet known)
and retained values of fields that no longer apply are left out.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal, Optional

from .model import FieldDef, PhaseSummary, Requirement, ResolutionState
from .schema import effective_bounds

ComponentType = Literal["text_input", "number_input", "dropdown", "boolean_input"]


@dataclass
class Component:
    type: ComponentType
    field: str
    label: str
    required: bool
    value: Any = None
    error: Optional[str] = None
    options: Optional[list[str]] = None
    constraints: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass
class AutomatedCheck:
    field: str
    label: str
    status: str
    reason: str
    result: Optional[dict[str, Any]] = None


@dataclass
class Surface:
    revision: int
    title: str
    # Which form this is, e.g. "loan_offer v1", and its position in the chain.
    phase: str
    step: int
    # Forms completed before this one, and how each ended: the branch taken so far.
    journey: list[PhaseSummary]
    status: str
    progress: Optional[str]
    outcome: Optional[str]
    components: list[Component]
    automated: list[AutomatedCheck]
    can_submit: bool
    message: str


_MESSAGES = {
    "waiting_for_human": "Please provide the information below.",
    "waiting_for_automation": "Running checks. This page refreshes automatically.",
    "action_failed": "An automated check failed. It can be retried from the debug panel.",
    "impossible": "We can't continue with the current answers. Please review them.",
}


def render_surface(state: ResolutionState) -> Surface:
    schema = state.schema
    terminal = state.status in ("completed", "failed", "cancelled")
    if schema is None:
        return Surface(
            revision=state.revision,
            title="Loading",
            phase="",
            step=len(state.phases) + 1,
            journey=list(state.phases),
            status=state.status,
            progress=None,
            outcome=None,
            components=[],
            automated=[],
            can_submit=False,
            message=state.status_reason or "Loading schema...",
        )

    components: list[Component] = []
    automated: list[AutomatedCheck] = []
    for req in state.requirements:
        fdef = schema.fields[req.id]
        if req.kind == "activity":
            if req.status != "not_applicable":
                automated.append(
                    AutomatedCheck(
                        field=req.id,
                        label=fdef.label,
                        status=req.status,
                        reason=req.reason,
                        result=state.values.get(req.id),
                    )
                )
            continue
        if req.status not in ("missing", "satisfied"):
            continue
        components.append(_component(req.id, fdef, state.data.get(req.id), req, state.context))

    if state.status == "completed":
        message = "All required information has been collected and checked."
    elif terminal:
        message = f"Application {state.status} ({state.status_reason})."
    else:
        message = _MESSAGES.get(state.progress or "", "")
    return Surface(
        revision=state.revision,
        title=schema.title,
        phase=f"{schema.id} v{schema.version}",
        step=len(state.phases) + 1,
        journey=list(state.phases),
        status=state.status,
        progress=state.progress,
        outcome=state.outcome,
        components=components,
        automated=automated,
        can_submit=not terminal and bool(components),
        message=message,
    )


def _component(
    fid: str, fdef: FieldDef, value: Any, req: Requirement, context: dict[str, Any]
) -> Component:
    error = req.reason.removeprefix("invalid value: ") if req.reason.startswith("invalid") else None
    # Bounds read from context (e.g. a loan amount capped by an earlier
    # affordability result) reach the UI as plain numbers.
    lo, hi = effective_bounds(fdef, context)
    constraints: dict[str, Any] = {
        k: v
        for k, v in {
            "min": lo,
            "max": hi,
            "minLength": fdef.min_length,
            "maxLength": fdef.max_length,
            "pattern": fdef.pattern,
        }.items()
        if v is not None
    }
    if fdef.type == "boolean":
        ctype: ComponentType = "boolean_input"
    elif fdef.type == "number":
        ctype = "number_input"
    elif fdef.enum:
        ctype = "dropdown"
    else:
        ctype = "text_input"
    return Component(
        type=ctype,
        field=fid,
        label=fdef.label,
        required=True,
        value=value,
        error=error,
        options=fdef.enum,
        constraints=constraints,
    )
