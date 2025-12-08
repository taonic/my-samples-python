import random
import time
import logging
import base64
import psutil
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from prometheus.worker import WorkerTuningWorkflow, init_runtime_with_prometheus
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from prometheus_client import start_http_server, Gauge

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

# Constants
WORKFLOW_ID = "WorkerTuningWorkflow"
activityPollers = 5
activityExecSlots = 200
workflows = 50
activitiesPerWF = 1

# Running concurrent workflows
async def run_concurrent_workflows(client: Client):
    task_queue = f"Pollers{activityPollers}/Slots{activityExecSlots}"
    log.info(f"Running {workflows} workflows concurrently with {activitiesPerWF} activities each.")
    futures = []
    for i in range(workflows):
        futures.append(client.start_workflow(
            WorkerTuningWorkflow.do_work,
            activitiesPerWF,
            id=f"{WORKFLOW_ID}_{random.randint(0, 10000)}",
            task_queue=task_queue,
            ))
    await asyncio.gather(*futures)

async def collect_memory_metrics():
    """Asynchronously collect memory metrics."""
    while True:
        mem_info = psutil.virtual_memory()
        # Update gauge values
        memory_usage_gauge.set(mem_info.used)
        memory_available_gauge.set(mem_info.available)
        memory_used_percentage_gauge.set(mem_info.percent)
        # Collect every 5 seconds
        await asyncio.sleep(5)

async def main():
    runtime = init_runtime_with_prometheus(9001)

    client = await Client.connect(
                "localhost:7233",
                runtime=runtime,
            )  # Connect to Temporal Server
    await run_concurrent_workflows(client)

if __name__ == "__main__":
    import asyncio
    from datetime import timedelta

    asyncio.run(main())
