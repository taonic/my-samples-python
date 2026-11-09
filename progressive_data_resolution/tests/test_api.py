import uuid

import httpx
import pytest
from temporalio.testing import WorkflowEnvironment

from progressive_data_resolution.api import create_app

from .conftest import FakeChecks, worker


@pytest.fixture
async def http(env: WorkflowEnvironment):
    task_queue = f"pdr-api-{uuid.uuid4()}"
    app = create_app(client=env.client, task_queue=task_queue)
    async with worker(env.client, task_queue, FakeChecks()):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def new_application(http: httpx.AsyncClient) -> str:
    res = await http.post("/applications", json={"schemaVersion": "1"})
    assert res.status_code == 200
    return res.json()["workflowId"]


async def submit(http, wf_id, base_revision, values, submission_id=None) -> httpx.Response:
    return await http.post(
        f"/applications/{wf_id}/submissions",
        json={
            "submissionId": submission_id or str(uuid.uuid4()),
            "baseRevision": base_revision,
            "values": values,
        },
    )


async def test_surface_changes_after_submission(http):
    wf_id = await new_application(http)
    surface = (await http.get(f"/applications/{wf_id}/surface")).json()
    fields = [c["field"] for c in surface["components"]]
    assert "business_registration" not in fields

    res = await submit(http, wf_id, surface["revision"], {"employment_type": "self_employed"})
    assert res.status_code == 200 and res.json()["revision"] == 1

    surface = (await http.get(f"/applications/{wf_id}/surface")).json()
    by_field = {c["field"]: c for c in surface["components"]}
    assert by_field["business_registration"]["type"] == "text_input"
    assert by_field["employment_type"]["value"] == "self_employed"
    assert surface["revision"] == 1


async def test_validation_errors_are_422(http):
    wf_id = await new_application(http)
    res = await submit(http, wf_id, 0, {"annual_income": -1})
    assert res.status_code == 422
    assert res.json()["detail"]["errors"] == {"annual_income": "must be at least 0"}


async def test_stale_submission_is_409_with_current_revision(http):
    wf_id = await new_application(http)
    assert (await submit(http, wf_id, 0, {"employment_type": "employed"})).status_code == 200
    res = await submit(http, wf_id, 0, {"employment_type": "self_employed"})
    assert res.status_code == 409
    assert res.json()["detail"]["revision"] == 1
    assert "employment_type" in res.json()["detail"]["conflicts"]


async def test_resubmitting_same_id_is_idempotent(http):
    wf_id = await new_application(http)
    first = await submit(http, wf_id, 0, {"applicant_name": "Ada"}, submission_id="same")
    again = await submit(http, wf_id, 0, {"applicant_name": "Ada"}, submission_id="same")
    assert first.status_code == again.status_code == 200
    assert first.json()["revision"] == again.json()["revision"] == 1
    state = (await http.get(f"/applications/{wf_id}/state")).json()
    assert state["revision"] == 1


async def test_unknown_application_is_404(http):
    assert (await http.get("/applications/nope/surface")).status_code == 404


async def test_index_is_served(http):
    res = await http.get("/")
    assert res.status_code == 200 and "Progressive Data Resolution" in res.text


async def test_schema_files_are_served_for_the_viewer(http):
    res = await http.get("/schemas/loan_offer/1")
    assert res.status_code == 200
    assert res.json()["id"] == "loan_offer" and "loan_amount" in res.json()["fields"]
    for path in ("/schemas/loan_offer/2", "/schemas/..%2Fapi/1", "/schemas/LOAN/1"):
        assert (await http.get(path)).status_code == 404, path


async def test_step_source_is_highlighted_python(http):
    res = await http.get("/source/apply")
    assert res.status_code == 200
    parts = res.json()
    assert [p["name"] for p in parts] == [
        "ProgressiveDataResolutionWorkflow._apply",
        "ProgressiveDataResolutionWorkflow._apply_submission",
    ]
    assert parts[0]["file"] == "workflow.py" and parts[0]["line"] > 1
    code = "".join(text for _, text in parts[0]["runs"])
    assert code.startswith("def _apply(self, event: _Event)")
    assert ["fn", "_apply"] in parts[0]["runs"] and ["kw", "def"] in parts[0]["runs"]
    loop = (await http.get("/source/loop")).json()
    assert loop[0]["name"] == "ProgressiveDataResolutionWorkflow._run_form"
    inbox = (await http.get("/source/inbox")).json()
    assert [p["name"] for p in inbox][1:] == [
        "ProgressiveDataResolutionWorkflow._apply",
        "inbox events",
    ]
    events = "".join(text for _, text in inbox[2]["runs"])
    begin = (await http.get("/source/begin")).json()
    assert [p["name"] for p in begin] == ["POST /applications", "WorkflowInput"]
    route = "".join(text for _, text in begin[0]["runs"])
    assert (
        route.startswith('@app.post("/applications")') and 'schema_id="loan_application"' in route
    )
    assert events.startswith("# Events on the inbox.") and events.rstrip().endswith(
        "_ActivityFailed]"
    )
    assert (await http.get("/source/nope")).status_code == 404
