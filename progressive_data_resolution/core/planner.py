"""Action planning: turn unresolved requirements into work.

`plan_actions` is pure. It takes the current resolution and the actions that
already exist and returns the reconciled action set. Identities are derived, not
generated, which is what makes re-planning idempotent:

- human input:  `collect:<field>`, one per human requirement. It is pending
  while the field is missing, completed once satisfied, and cancelled when the
  field stops being applicable.
- activities:   `<activity>:<input fingerprint>`. The same inputs always
  produce the same id, so an action that is already running, done or failed is
  never scheduled twice. New inputs produce a new id, and the old action is
  left to finish; its result is stored but no longer matches the requirement.

Failed actions stay failed. They are re-armed only by an explicit retry, or
superseded by new inputs (a new fingerprint means a new action).
"""

from dataclasses import dataclass, replace
from typing import Mapping

from .model import PlannedAction, ResolutionResult, Schema
from .resolver import action_id_for


@dataclass
class Plan:
    actions: dict[str, PlannedAction]
    # Ids of actions created or re-opened by this plan, for the decision record.
    new_action_ids: list[str]


def collect_action_id(field_id: str) -> str:
    return f"collect:{field_id}"


def plan_actions(
    schema: Schema, resolution: ResolutionResult, actions: Mapping[str, PlannedAction]
) -> Plan:
    planned = {aid: replace(a) for aid, a in actions.items()}
    new_ids: list[str] = []

    for req in resolution.requirements:
        fdef = schema.fields[req.id]
        if req.kind == "human":
            aid = collect_action_id(req.id)
            existing = planned.get(aid)
            if req.status == "missing":
                if existing is None or existing.status != "pending":
                    planned[aid] = PlannedAction(
                        id=aid,
                        type="collect_human_input",
                        requirement_ids=[req.id],
                        status="pending",
                    )
                    new_ids.append(aid)
            elif existing is not None and existing.status == "pending":
                existing.status = "completed" if req.status == "satisfied" else "cancelled"
            continue

        # Activity requirement: "missing" means ready to run with no result and
        # no live action for the current fingerprint.
        if req.status != "missing" or req.fingerprint is None:
            continue
        aid = action_id_for(fdef, req.fingerprint)
        if aid in planned:
            continue
        planned[aid] = PlannedAction(
            id=aid,
            type="execute_activity",
            requirement_ids=[req.id],
            status="pending",
            parameters={
                "activityType": fdef.collection.activity,
                "fingerprint": req.fingerprint,
                "inputs": dict(req.inputs or {}),
            },
        )
        new_ids.append(aid)

    return Plan(actions=planned, new_action_ids=new_ids)
