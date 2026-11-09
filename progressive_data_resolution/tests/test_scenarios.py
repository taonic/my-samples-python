"""The UI's demo scenarios (static/scenarios.json) stay valid and reach what they promise.

Each scenario is played through the HTTP API against the real mock activities,
following the same rules as the page's Play button: fill the current step's
visible fields, submit, and move on once all of a step's fields are sent.
"""

import asyncio
import dataclasses
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from progressive_data_resolution import activities
from progressive_data_resolution.activities import AUTOMATED_ACTIVITIES, load_schema
from progressive_data_resolution.api import create_app
from progressive_data_resolution.core.schema import parse_schema, validate_value
from progressive_data_resolution.workflow import ProgressiveDataResolutionWorkflow

from .test_rules_and_schema import SCHEMAS

SCENARIOS = json.loads((Path(__file__).parent.parent / "static" / "scenarios.json").read_text())[
    "scenarios"
]
FORMS = {
    p.name.split(".")[0]: parse_schema(json.loads(p.read_text()), AUTOMATED_ACTIVITIES)
    for p in SCHEMAS.glob("*.v1.json")
}
TEMPLATE = re.compile(r"^\{\{\s*([\w.]+)\s*\}\}$")


def test_scenario_ids_are_unique():
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_steps_name_real_human_fields_with_valid_values(scenario):
    for step in scenario["steps"]:
        schema = FORMS[step["form"]]
        for field, value in step["values"].items():
            fdef = schema.fields[field]
            assert fdef.collection.strategy == "human", (step["form"], field)
            if isinstance(value, str) and TEMPLATE.match(value):
                continue
            # Bounds read from earlier forms are checked when the scenario is played.
            static = dataclasses.replace(fdef, min_from=None, max_from=None)
            assert validate_value(static, value) is None, (step["form"], field, value)
    for part in scenario["expect"].split(" > "):
        assert part.split(":")[0] in FORMS


# ---- Play every scenario end to end ----------------------------------------------


def resolve(value: Any, state: dict[str, Any]) -> Any:
    m = isinstance(value, str) and TEMPLATE.match(value)
    if not m:
        return value
    for source in (state["context"], state["values"]):
        found: Any = source
        for key in m.group(1).split("."):
            found = found.get(key) if isinstance(found, dict) else None
        if found is not None:
            return found
    return value


def actual_path(state: dict[str, Any]) -> str:
    parts = [f"{p['schema_id']}:{p['outcome']}" for p in state["phases"]]
    current = (
        state["outcome"]
        if state["status"] == "completed"
        else f"({state['progress'] or state['status']})"
    )
    return " > ".join(parts + [f"{state['schema_id']}:{current}"])


async def play(http: httpx.AsyncClient, scenario: dict[str, Any]) -> str:
    wf = (await http.post("/applications", json={})).json()["workflowId"]
    steps, index, sent, idle = scenario["steps"], 0, set(), 0
    for _ in range(400):
        surface = (await http.get(f"/applications/{wf}/surface")).json()
        state = (await http.get(f"/applications/{wf}/state")).json()
        if surface["status"] in ("completed", "cancelled", "failed"):
            break
        done_forms = [p["schema_id"] for p in state["phases"]]
        step = None
        while index < len(steps):
            candidate = steps[index]
            on_form = candidate["form"] == state["schema_id"]
            if (on_form and not set(candidate["values"]) - sent) or (
                not on_form and candidate["form"] in done_forms
            ):
                index, sent = index + 1, set()
                continue
            step = candidate
            break
        if step and step["form"] == state["schema_id"]:
            values = {}
            for c in surface["components"]:
                if c["field"] not in step["values"] or c["field"] in sent:
                    continue
                target = resolve(step["values"][c["field"]], state)
                if target == c["value"]:
                    sent.add(c["field"])
                else:
                    values[c["field"]] = target
            if values:
                res = await http.post(
                    f"/applications/{wf}/submissions",
                    json={
                        "submissionId": str(uuid.uuid4()),
                        "baseRevision": surface["revision"],
                        "values": values,
                    },
                )
                assert res.status_code == 200, (scenario["id"], values, res.text)
                sent |= set(values)
                idle = 0
                continue
        if step is None and surface["status"] != "running":
            break
        if surface["status"] != "running":
            idle += 1
            if idle > 8:
                break
        await asyncio.sleep(0.25)
    return actual_path((await http.get(f"/applications/{wf}/state")).json())


async def test_every_scenario_reaches_its_expected_path(env: WorkflowEnvironment, monkeypatch):
    monkeypatch.setattr(activities, "DELAY_SCALE", 0.02)
    task_queue = f"pdr-scenarios-{uuid.uuid4()}"
    app = create_app(client=env.client, task_queue=task_queue)
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ProgressiveDataResolutionWorkflow],
        activities=[load_schema, *AUTOMATED_ACTIVITIES.values()],
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            actual = await asyncio.gather(*(play(http, s) for s in SCENARIOS))
    mismatches = {
        s["id"]: {"expected": s["expect"], "actual": a}
        for s, a in zip(SCENARIOS, actual)
        if a != s["expect"]
    }
    assert mismatches == {}
