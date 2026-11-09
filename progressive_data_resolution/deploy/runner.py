"""The Fly app: one Daytona sandbox runs the sample, and this proxies to it.

The sandbox, created from the snapshot (see snapshot.py), runs a Temporal dev
server, the worker and the API (see start.sh). This app finds the sandbox by its
label, creates or starts it when needed, uploads the sample's code when it changed
since the sandbox last got it, and forwards every request through Daytona's
signed preview URLs:

    /temporal/...    the Temporal UI, which the dev server serves under /temporal
    everything else  the sample's page and API

It keeps nothing that matters: a restart finds the same sandbox by its label.

    DAYTONA_API_KEY=... uv run --group progressive-data-resolution-deploy \
        python -m progressive_data_resolution.deploy.runner
"""

import asyncio
import html
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import httpx
import uvicorn
from daytona import (
    AsyncDaytona,
    AsyncSandbox,
    CreateSandboxFromSnapshotParams,
    FileUpload,
    ListSandboxesQuery,
    SandboxState,
)
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .files import SANDBOX_ROOT, code_files, code_version, sandbox_path, snapshot_name

log = logging.getLogger(__name__)

APP_LABEL = "progressive-data-resolution"
CODE_LABEL = "code"
API_PORT = 8000
TEMPORAL_UI_PORT = 8233
START = f"bash {SANDBOX_ROOT}/progressive_data_resolution/deploy/start.sh"

# The sandbox stops after this many idle minutes. Requests through its preview URLs
# count as activity, so it stays up while anyone is using the sample.
AUTO_STOP_MINUTES = int(os.environ.get("SANDBOX_AUTO_STOP_MINUTES", "30"))
# How long a sandbox seen running is trusted before its state is read again.
RECHECK_SECONDS = 60
# How long a request waits for the sandbox before it gets the "starting" page.
WAIT_SECONDS = 3

GONE = {
    SandboxState.DESTROYED,
    SandboxState.DESTROYING,
    SandboxState.ERROR,
    SandboxState.BUILD_FAILED,
}
SETTLING = {SandboxState.STOPPING, SandboxState.ARCHIVING, SandboxState.PAUSING}

DROP_REQUEST = {"host", "connection", "keep-alive", "upgrade", "te", "trailer", "transfer-encoding"}
DROP_RESPONSE = {"connection", "keep-alive", "transfer-encoding"}


class SandboxManager:
    """Gets the one sandbox to "ready" and hands out preview URLs for its ports."""

    def __init__(self, daytona: AsyncDaytona) -> None:
        self.daytona = daytona
        self.snapshot = snapshot_name()
        self.code = code_version()
        self.sandbox: Optional[AsyncSandbox] = None
        self.ready_until = 0.0
        self.status = "Looking for the sandbox…"
        self.failed = False
        self.task: Optional[asyncio.Task[None]] = None
        self.previews: dict[tuple[str, int], tuple[str, float]] = {}

    @property
    def ready(self) -> bool:
        return self.sandbox is not None and time.monotonic() < self.ready_until

    def recheck(self) -> None:
        self.ready_until = 0.0

    async def wait_ready(self, seconds: float) -> bool:
        """Starts getting the sandbox ready, unless that's under way, and waits a while for it."""
        if self.ready:
            return True
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._ensure())
        try:
            await asyncio.wait_for(asyncio.shield(self.task), seconds)
        except asyncio.TimeoutError:
            pass
        return self.ready

    async def _ensure(self) -> None:
        self.failed = False
        try:
            await self._bring_up()
        except Exception as err:
            log.exception("Getting the sandbox ready failed")
            self.status = f"Getting the sandbox ready failed: {err}"
            self.failed = True
            return
        self.status = "Ready."
        self.ready_until = time.monotonic() + RECHECK_SECONDS

    async def _bring_up(self) -> None:
        sandbox = self.sandbox
        if sandbox is not None:
            try:
                await sandbox.refresh_data()
            except Exception:
                sandbox = None
        if sandbox is None or sandbox.state in GONE:
            sandbox = await self._find()

        if sandbox is not None and sandbox.snapshot != self.snapshot:
            # Its dependencies are out of date. Its workflows go with it.
            self.status = "Replacing the sandbox: its dependencies changed…"
            log.info("Deleting sandbox %s, built from %s", sandbox.id, sandbox.snapshot)
            await sandbox.delete(timeout=120)
            sandbox = None

        if sandbox is None:
            sandbox = await self._create()
        elif sandbox.state != SandboxState.STARTED:
            for _ in range(60):
                if sandbox.state not in SETTLING:
                    break
                self.status = "The sandbox is stopping; waiting to start it again…"
                await asyncio.sleep(2)
                await sandbox.refresh_data()
            self.status = "Waking the sandbox…"
            await sandbox.start(timeout=180)
        self.sandbox = sandbox

        if sandbox.labels.get(CODE_LABEL) != self.code:
            self.status = "Uploading the sample…"
            await sandbox.fs.upload_files(
                [
                    FileUpload(source=path.read_bytes(), destination=sandbox_path(path))
                    for path in code_files()
                ]
            )
            await self._start(sandbox, f"{START} app")
            await sandbox.set_labels({"app": APP_LABEL, CODE_LABEL: self.code})
        elif not await self._up(sandbox):
            await self._start(sandbox, START)

    async def _find(self) -> Optional[AsyncSandbox]:
        async for sandbox in self.daytona.list(ListSandboxesQuery(labels={"app": APP_LABEL})):
            if sandbox.state not in GONE:
                return sandbox
        return None

    async def _create(self) -> AsyncSandbox:
        self.status = "Creating the sandbox (about a minute)…"
        try:
            return await self.daytona.create(
                CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    labels={"app": APP_LABEL},
                    auto_stop_interval=AUTO_STOP_MINUTES,
                ),
                timeout=180,
            )
        except Exception as err:
            if "snapshot" in str(err).lower():
                raise RuntimeError(
                    f"{err} (Does the snapshot {self.snapshot} exist? "
                    "Build it: python -m progressive_data_resolution.deploy.snapshot)"
                ) from err
            raise

    async def _up(self, sandbox: AsyncSandbox) -> bool:
        result = await sandbox.process.exec(
            f"curl -sf -o /dev/null localhost:{API_PORT}/ && "
            f"curl -sf -o /dev/null localhost:{TEMPORAL_UI_PORT}/temporal/ && echo up || echo down",
            timeout=20,
        )
        return result.result.strip() == "up"

    async def _start(self, sandbox: AsyncSandbox, command: str) -> None:
        self.status = "Starting Temporal, the worker and the API…"
        result = await sandbox.process.exec(f"{command} 2>&1", cwd=SANDBOX_ROOT, timeout=180)
        if result.exit_code != 0:
            tail = "\n".join(result.result.strip().splitlines()[-15:])
            raise RuntimeError(f"Starting the sample failed:\n{tail}")

    async def preview_url(self, port: int) -> str:
        """A signed preview URL for a port of the sandbox: valid for an hour, reused for 50 minutes."""
        assert self.sandbox is not None
        key = (self.sandbox.id, port)
        cached = self.previews.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        signed = await self.sandbox.create_signed_preview_url(port, expires_in_seconds=3600)
        url = signed.url.rstrip("/")
        self.previews[key] = (url, time.monotonic() + 50 * 60)
        return url


def starting_page(status: str, failed: bool) -> str:
    refresh = "" if failed else '<meta http-equiv="refresh" content="3">'
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Progressive Data Resolution</title>{refresh}
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:15vh auto;padding:0 1em;color:#333}}
pre{{white-space:pre-wrap;background:#f4f4f4;padding:1em}}</style></head>
<body><h1>Progressive Data Resolution</h1>
<p>{"Something went wrong:" if failed else "The sample's sandbox is starting. This page reloads on its own."}</p>
<pre>{html.escape(status)}</pre></body></html>"""


def create_app() -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with (
            AsyncDaytona() as daytona,
            httpx.AsyncClient(
                timeout=httpx.Timeout(60, connect=10), follow_redirects=False
            ) as http,
        ):
            app.state.manager = SandboxManager(daytona)
            app.state.http = http
            log.info("Snapshot %s, code %s", app.state.manager.snapshot, app.state.manager.code)
            yield

    async def health(request: Request) -> JSONResponse:
        manager: SandboxManager = request.app.state.manager
        return JSONResponse({"ok": True, "sandboxReady": manager.ready, "status": manager.status})

    async def proxy(request: Request) -> Response:
        manager: SandboxManager = request.app.state.manager
        if not await manager.wait_ready(WAIT_SECONDS):
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(starting_page(manager.status, manager.failed), status_code=503)
            return JSONResponse(
                {"detail": manager.status}, status_code=503, headers={"retry-after": "3"}
            )

        path = request.url.path
        port = (
            TEMPORAL_UI_PORT if path == "/temporal" or path.startswith("/temporal/") else API_PORT
        )
        base = await manager.preview_url(port)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST}
        headers["x-daytona-skip-preview-warning"] = "true"
        url = f"{base}{path}" + (f"?{request.url.query}" if request.url.query else "")
        http: httpx.AsyncClient = request.app.state.http
        upstream_request = http.build_request(
            request.method, url, headers=headers, content=await request.body()
        )
        try:
            upstream = await http.send(upstream_request, stream=True)
        except httpx.TransportError as err:
            log.warning("The sandbox didn't answer %s %s: %s", request.method, path, err)
            manager.recheck()
            return JSONResponse(
                {"detail": "The sandbox didn't answer; try again."}, status_code=502
            )
        if upstream.status_code in (502, 503, 504):
            manager.recheck()

        raw_headers = []
        for key, value in upstream.headers.multi_items():
            lower = key.lower()
            if lower in DROP_RESPONSE:
                continue
            if lower == "location" and value.startswith(base):
                value = value[len(base) :] or "/"
            elif lower == "set-cookie":
                value = re.sub(r";\s*Domain=[^;]*", "", value, flags=re.I)
            raw_headers.append((lower.encode("latin-1"), value.encode("latin-1")))
        response = StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            background=BackgroundTask(upstream.aclose),
        )
        response.raw_headers = raw_headers
        return response

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    return Starlette(
        routes=[
            Route("/healthz", health),
            Route("/{path:path}", proxy, methods=methods),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
