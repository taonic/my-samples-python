import asyncio
import logging
import os
from datetime import timedelta

from temporalio import activity
from temporalio.client import Client
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerRequest,
    ListWorkersRequest,
)
from temporalio.runtime import Runtime, TelemetryConfig
from temporalio.worker import Worker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TASK_QUEUE = "describe-worker-sample"

# Connection config. Defaults target a local dev server; set the env vars to
# point at Temporal Cloud instead (see the "Temporal Cloud" section in the
# README). ListWorkers/DescribeWorker must be called with the SAME namespace the
# worker runs in — on Cloud that's the fully-qualified name, e.g. "my-ns.a1b2c".
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
API_KEY = os.environ.get("TEMPORAL_API_KEY")  # set -> Temporal Cloud (API key auth)


async def connect(runtime: Runtime) -> Client:
    # Temporal Cloud with an API key: pass the key and enable TLS. The address
    # is your namespace's gRPC endpoint, e.g. "my-ns.a1b2c.tmprl.cloud:7233".
    if API_KEY:
        log.info("Connecting to Temporal Cloud at %s (namespace %s)", ADDRESS, NAMESPACE)
        return await Client.connect(
            ADDRESS,
            namespace=NAMESPACE,
            api_key=API_KEY,
            tls=True,
            runtime=runtime,
        )

    # Local dev server (no TLS).
    log.info("Connecting to %s (namespace %s)", ADDRESS, NAMESPACE)
    return await Client.connect(ADDRESS, namespace=NAMESPACE, runtime=runtime)


# There is no high-level `client.describe_worker(...)` helper in the Python SDK
# (as of temporalio 1.20.0). Both ListWorkers and DescribeWorker are only
# available as raw gRPC calls via `client.workflow_service`, using the protobuf
# request/response types directly.
#
# Flow:
#   1. ListWorkers  -> discover running workers + their `worker_instance_key`
#   2. DescribeWorker(worker_instance_key) -> full detail for one worker
#
# A worker only appears here if it is heartbeating. In the Python SDK, worker
# heartbeat is configured at the *Runtime* level (not on Worker) and is ON by
# default at a 60s interval. Both RPCs require a recent Temporal Server / dev
# server that supports worker heartbeat; older servers return UNIMPLEMENTED.


# A worker must register at least one activity/workflow, so we register a
# trivial no-op activity just to have a valid, heartbeating worker to describe.
@activity.defn
async def noop() -> None:
    return None


async def describe_workers(client: Client) -> None:
    # ListWorkers: `query` uses the same list-filter grammar as visibility
    # (pass query="" to list every worker).
    resp = await client.workflow_service.list_workers(
        ListWorkersRequest(
            namespace=NAMESPACE,
            query=f"TaskQueue = '{TASK_QUEUE}'",
            page_size=100,
        )
    )

    if not resp.workers_info:
        log.warning(
            "No workers found. Give the worker a heartbeat interval or two to "
            "appear, and make sure your server supports worker heartbeat."
        )
        return

    for info in resp.workers_info:
        key = info.worker_heartbeat.worker_instance_key

        # DescribeWorker: full detail for one worker, keyed by instance key.
        detail = await client.workflow_service.describe_worker(
            DescribeWorkerRequest(namespace=NAMESPACE, worker_instance_key=key)
        )
        hb = detail.worker_info.worker_heartbeat

        log.info("=== DescribeWorker: %s ===", key)
        log.info("  identity:    %s", hb.worker_identity)
        log.info("  sdk:         %s %s", hb.sdk_name, hb.sdk_version)


async def main():
    # Worker heartbeat lives on the Runtime. It's on by default (60s); we
    # shorten it so the worker shows up quickly. None disables it (1s-60s range).
    runtime = Runtime(
        telemetry=TelemetryConfig(),
        worker_heartbeat_interval=timedelta(seconds=3),
    )

    # The client must be created with this runtime so the worker inherits it.
    client = await connect(runtime)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        activities=[noop],
        identity="describe-worker-sample-worker",
    )

    # Run the worker in the background, wait for it to heartbeat, then describe
    # it from the same script.
    async with worker:
        log.info("Worker started; waiting for it to heartbeat...")
        await asyncio.sleep(5)
        await describe_workers(client)


if __name__ == "__main__":
    asyncio.run(main())
