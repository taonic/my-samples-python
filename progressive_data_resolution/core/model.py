"""Domain types shared by the resolver, planner, surface renderer and workflow.

Everything here is a plain dataclass so it round-trips through Temporal's default
data converter unchanged: the workflow returns these from its query, and the API
renders the surface straight from them.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

RequirementStatus = Literal[
    "not_applicable", "blocked", "missing", "in_progress", "satisfied", "failed"
]
ActionStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ActionType = Literal["collect_human_input", "execute_activity"]
WorkflowStatus = Literal["running", "waiting_for_input", "completed", "failed", "cancelled"]
Progress = Literal["waiting_for_human", "waiting_for_automation", "action_failed", "impossible"]


@dataclass
class Collection:
    strategy: Literal["human", "activity"]
    activity: Optional[str] = None


@dataclass
class FieldDef:
    type: Literal["string", "number", "boolean", "object"]
    collection: Collection
    label: str = ""
    required: bool = False
    required_when: Optional[dict[str, Any]] = None
    depends_on: list[str] = field(default_factory=list)
    ready_when: Optional[dict[str, Any]] = None
    enum: Optional[list[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    # Dynamic bounds: a context path such as
    # "application.affordability_assessment.max_loan", resolved at evaluation time.
    min_from: Optional[str] = None
    max_from: Optional[str] = None


@dataclass
class OutcomeRule:
    result: str
    when: Optional[dict[str, Any]] = None


@dataclass
class NextSchema:
    """A follow-up form to continue into once this one completes."""

    schema_id: str
    version: str
    # Namespace under which this form's final values are handed to the next one.
    as_namespace: str
    when: Optional[dict[str, Any]] = None


@dataclass
class Schema:
    id: str
    version: str
    title: str
    fields: dict[str, FieldDef]
    outcome: list[OutcomeRule]
    # Declaration order, which is also display order. Kept explicitly because
    # dict key order does not survive every serializer (Temporal's JSON
    # converter sorts keys).
    field_order: list[str] = field(default_factory=list)
    # Read-only namespaces of carried-over data that rules and bounds may read.
    context: list[str] = field(default_factory=list)
    next: list[NextSchema] = field(default_factory=list)


@dataclass
class Requirement:
    id: str
    status: RequirementStatus
    kind: Literal["human", "activity"]
    dependencies: list[str] = field(default_factory=list)
    collection_strategy: Optional[str] = None
    reason: str = ""
    # True when readiness is definitively false: nothing will move this
    # requirement forward until the data it depends on is corrected.
    impossible: bool = False
    # Activity requirements only: the inputs the activity would run with and
    # their fingerprint, which is what results and actions are keyed by.
    fingerprint: Optional[str] = None
    inputs: Optional[dict[str, Any]] = None


@dataclass
class ResolutionResult:
    requirements: list[Requirement]
    complete: bool
    outcome: Optional[str]
    progress: Optional[Progress]
    # Values of satisfied, applicable requirements: human values and current
    # activity results. Never contains anything from a non-applicable field.
    values: dict[str, Any]
    # Human fields that are not applicable but still hold a value. The workflow
    # moves these to `retained` so they cannot satisfy anything later.
    retire: list[str]

    def by_id(self) -> dict[str, Requirement]:
        return {r.id: r for r in self.requirements}


@dataclass
class PlannedAction:
    id: str
    type: ActionType
    requirement_ids: list[str]
    status: ActionStatus
    parameters: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    error: Optional[str] = None
    # Set when an activity finished after its inputs had already changed.
    stale: bool = False


@dataclass
class ActivityResult:
    fingerprint: str
    action_id: str
    revision: int
    value: dict[str, Any]


@dataclass
class RetainedValue:
    value: Any
    retired_at_revision: int


@dataclass
class DecisionRecord:
    revision: int
    trigger: str
    satisfied: list[str]
    missing: list[str]
    blocked: list[str]
    in_progress: list[str]
    failed: list[str]
    not_applicable: list[str]
    new_actions: list[str]
    reason: str


@dataclass
class PhaseSummary:
    """A completed earlier form in the same chain."""

    schema_id: str
    schema_version: str
    outcome: Optional[str]
    revision: int


@dataclass
class ResolutionState:
    """Authoritative state of one execution, as returned by the `get_state` query."""

    schema_id: str
    schema_version: str
    status: WorkflowStatus
    revision: int
    schema: Optional[Schema] = None
    status_reason: Optional[str] = None
    progress: Optional[Progress] = None
    outcome: Optional[str] = None
    # Source data: raw human input, keyed by field.
    data: dict[str, Any] = field(default_factory=dict)
    field_revisions: dict[str, int] = field(default_factory=dict)
    retained: dict[str, list[RetainedValue]] = field(default_factory=dict)
    # Activity results keyed by field, then by input fingerprint.
    results: dict[str, dict[str, ActivityResult]] = field(default_factory=dict)
    # Derived state, recalculated on every change.
    values: dict[str, Any] = field(default_factory=dict)
    requirements: list[Requirement] = field(default_factory=list)
    # Execution state.
    actions: list[PlannedAction] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    # Data carried from earlier forms, by namespace, and those forms' summaries.
    context: dict[str, dict[str, Any]] = field(default_factory=dict)
    phases: list[PhaseSummary] = field(default_factory=list)
