"""Checking a human submission against the current state.

Two kinds of problem, reported separately because callers react differently:

- invalid:   the values themselves are wrong (unknown field, wrong type, fails
             a validation keyword). The user must fix the input.
- conflicts: the values were valid against the state the user saw, but that
             state has moved on (a field they are setting was changed after
             their base revision, or is no longer applicable). The UI should
             refresh and let the user try again.

A conflict is per field: an activity completing between page load and submit
bumps the revision but touches no human field, so it is not a conflict.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .model import ActivityResult, PlannedAction, Schema
from .resolver import resolve_requirements
from .schema import validate_value


@dataclass
class SubmissionCheck:
    invalid: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.invalid and not self.conflicts


def check_submission(
    schema: Schema,
    data: Mapping[str, Any],
    results: Mapping[str, Mapping[str, ActivityResult]],
    actions: Mapping[str, PlannedAction],
    field_revisions: Mapping[str, int],
    base_revision: int,
    values: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
) -> SubmissionCheck:
    check = SubmissionCheck()
    if not values:
        check.invalid["_"] = "submission contains no values"
        return check
    for fid, value in values.items():
        fdef = schema.fields.get(fid)
        if fdef is None:
            check.invalid[fid] = "unknown field"
        elif fdef.collection.strategy != "human":
            check.invalid[fid] = "this field is collected automatically"
        elif error := validate_value(fdef, value, context):
            check.invalid[fid] = error
    if check.invalid:
        return check

    for fid in values:
        changed_at = field_revisions.get(fid, 0)
        if changed_at > base_revision:
            check.conflicts[fid] = f"changed at revision {changed_at}, after {base_revision}"

    # Applicability is judged on the state *after* the submission, so a user can
    # pick "self_employed" and supply the business details in one go.
    tentative = resolve_requirements(schema, {**data, **values}, results, actions, context).by_id()
    for fid in values:
        status = tentative[fid].status
        if status in ("not_applicable", "blocked"):
            check.conflicts.setdefault(fid, f"not currently applicable ({tentative[fid].reason})")
    return check
