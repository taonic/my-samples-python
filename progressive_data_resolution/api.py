"""HTTP API and static UI.

The API holds no state of its own. Every read is a workflow query and every
write is a workflow update; the surface is rendered from the queried state by
the pure `render_surface`.

    uv run --group progressive-data-resolution python -m progressive_data_resolution.api
"""

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from .activities import SCHEMA_DIR
from .core.model import ResolutionState
from .core.surface import render_surface
from .source_view import STEPS, step_source
from .worker import TASK_QUEUE
from .workflow import (
    ProgressiveDataResolutionWorkflow,
    RetryActionInput,
    Submission,
    WorkflowInput,
)

STATIC = Path(__file__).parent / "static"


class StartRequest(BaseModel):
    schemaVersion: str = "1"
    inactivityTimeoutSeconds: Optional[float] = None


class SubmissionRequest(BaseModel):
    submissionId: str = Field(min_length=1, max_length=100)
    baseRevision: int
    values: dict[str, Any]


def create_app(client: Optional[Client] = None, task_queue: str = TASK_QUEUE) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.client = client or await Client.connect("localhost:7233")
        yield

    app = FastAPI(title="Progressive Data Resolution", lifespan=lifespan)
    app.state.client = client

    def handle_for(request: Request, workflow_id: str) -> WorkflowHandle:
        return request.app.state.client.get_workflow_handle(workflow_id)

    async def query_state(handle: WorkflowHandle) -> ResolutionState:
        try:
            return await handle.query(ProgressiveDataResolutionWorkflow.get_state)
        except RPCError as err:
            if err.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, "No such application")
            raise

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/scenarios.json")
    async def scenarios() -> FileResponse:
        """Demo scenarios the UI can fill in or play: test data, not process logic."""
        return FileResponse(STATIC / "scenarios.json")

    @app.get("/config.json")
    async def config() -> dict[str, str]:
        """Where the UI links to the Temporal UI: a dev server by default, a path
        on this origin when deployed (see deploy/README.md)."""
        return {"temporalUiUrl": os.environ.get("TEMPORAL_UI_URL", "http://localhost:8233")}

    @app.get("/schemas/{schema_id}/{version}")
    async def schema(schema_id: str, version: str) -> dict[str, Any]:
        """The schema file a form is loaded from, for the UI's schema viewer.

        A running form uses the copy load_schema pinned in its history; the
        two only differ if the file was edited after the form started.
        """
        path = SCHEMA_DIR / f"{schema_id}.v{version}.json"
        well_formed = re.fullmatch(r"[a-z_]+", schema_id) and re.fullmatch(r"[0-9]+", version)
        if not well_formed or not path.exists():
            raise HTTPException(404, "No such schema")
        return json.loads(path.read_text())

    @app.get("/source/{step}")
    async def source(step: str) -> list[dict[str, Any]]:
        """The code behind a step of the loop animation, for the UI's source viewer."""
        if step not in STEPS:
            raise HTTPException(404, "No such step")
        return step_source(step)

    @app.post("/applications")
    async def start(request: Request, body: StartRequest) -> dict[str, str]:
        params = WorkflowInput(schema_id="loan_application", schema_version=body.schemaVersion)
        if body.inactivityTimeoutSeconds:
            params.inactivity_timeout_seconds = body.inactivityTimeoutSeconds
        handle = await request.app.state.client.start_workflow(
            ProgressiveDataResolutionWorkflow.run,
            params,
            id=f"loan-application-{uuid.uuid4()}",
            task_queue=task_queue,
        )
        return {"workflowId": handle.id}

    @app.get("/applications/{workflow_id}/surface")
    async def surface(request: Request, workflow_id: str) -> dict[str, Any]:
        state = await query_state(handle_for(request, workflow_id))
        return asdict(render_surface(state))

    @app.get("/applications/{workflow_id}/state")
    async def state(request: Request, workflow_id: str) -> dict[str, Any]:
        return asdict(await query_state(handle_for(request, workflow_id)))

    @app.post("/applications/{workflow_id}/submissions")
    async def submit(request: Request, workflow_id: str, body: SubmissionRequest) -> dict[str, Any]:
        handle = handle_for(request, workflow_id)
        submission = Submission(
            submission_id=body.submissionId, base_revision=body.baseRevision, values=body.values
        )
        try:
            # The submission id doubles as the update id, so a client retrying
            # after a dropped response gets the original outcome back.
            result = await handle.execute_update(
                ProgressiveDataResolutionWorkflow.submit, submission, id=body.submissionId
            )
        except WorkflowUpdateFailedError as err:
            raise _update_error(err)
        return asdict(result)

    @app.post("/applications/{workflow_id}/actions/{action_id}/retry")
    async def retry(request: Request, workflow_id: str, action_id: str) -> dict[str, int]:
        handle = handle_for(request, workflow_id)
        try:
            revision = await handle.execute_update(
                ProgressiveDataResolutionWorkflow.retry_action, RetryActionInput(action_id)
            )
        except WorkflowUpdateFailedError as err:
            raise _update_error(err)
        return {"revision": revision}

    return app


def _update_error(err: WorkflowUpdateFailedError) -> HTTPException:
    cause = err.cause
    if not isinstance(cause, ApplicationError):
        return HTTPException(500, str(cause))
    details = cause.details[0] if cause.details else {}
    if cause.type == "InvalidSubmission":
        return HTTPException(422, {"message": cause.message, **details})
    if cause.type == "StaleSubmission":
        return HTTPException(409, {"message": cause.message, **details})
    return HTTPException(409, {"message": cause.message, "type": cause.type})


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
