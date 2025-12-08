import random
import time
import logging
import base64
import psutil
import asyncio
from prometheus_client import start_http_server, Gauge
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from datetime import timedelta
from pypprof.net_http import start_pprof_server

# Create Prometheus gauges to track memory metrics
memory_usage_gauge = Gauge('memory_usage_bytes', 'Total memory usage in bytes')
memory_available_gauge = Gauge('memory_available_bytes', 'Available memory in bytes')
memory_used_percentage_gauge = Gauge('memory_used_percentage', 'Percentage of memory used')

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

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

# Constants
WORKFLOW_ID = "WorkerTuningWorkflow"
task_queue = None
activityPollers = 5
activityExecSlots = 200
workflows = 50
activitiesPerWF = 1

# Activity interface in Python
@activity.defn
async def slow_activity(seconds: int) -> bytes:
    start = time.time()
    try:
        await asyncio.sleep(seconds * 40 + random.randint(0, 100) / 1000)
    except Exception as e:
        raise RuntimeError(f"Activity failed: {e}")
    log.debug(f"Time elapsed: {time.time() - start}")
    return b'\x00'

@activity.defn
async def cpu_intensive_activity() -> None:
    def fib_recursion(count):
        if count == 0:
            return 0
        elif count == 1 or count == 2:
            return 1
        else:
            return fib_recursion(count - 1) + fib_recursion(count - 2)

    start = time.time()
    fib_recursion(42)  # Takes about 2-3 seconds
    log.debug(f"Time elapsed: {time.time() - start}")

@activity.defn
async def large_activity() -> bytes:
    await asyncio.sleep(0.2)  # Simulate delay
    return b'\x00' * 10 * 1024  # 10KiB

# Workflow interface in Python
@workflow.defn
class WorkerTuningWorkflow:
    @workflow.run
    async def do_work(self, concurrency: int):
        promises = []
        for i in range(concurrency):
            exec = workflow.execute_activity(
                    slow_activity,
                    i,
                    start_to_close_timeout=timedelta(seconds=10),
                    )
            promises.append(exec)

        results = await asyncio.gather(*promises)
        for result in results:
            base64_encoded_string = base64.b64encode(result).decode('utf-8')
            log.debug(base64_encoded_string)

# Worker and Client setup
async def run_worker(client: Client):
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[WorkerTuningWorkflow],
        activities=[slow_activity, cpu_intensive_activity, large_activity],
        max_concurrent_workflow_task_polls=activityPollers,
        max_concurrent_activity_task_polls=activityExecSlots
    ):
        log.info("Worker started.")
        await asyncio.sleep(1000)

def init_runtime_with_prometheus(port: int) -> Runtime:
    # Create runtime for use with Prometheus metrics
    return Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(bind_address=f"127.0.0.1:{port}")
        )
    )

async def main():
    runtime = init_runtime_with_prometheus(9000)

    # start_http_server(8000)
    # asyncio.create_task(collect_memory_metrics())

    global task_queue
    task_queue = f"Pollers{activityPollers}/Slots{activityExecSlots}"

    client = await Client.connect(
                "localhost:7233",
                runtime=runtime,
            )  # Connect to Temporal Server
    await run_worker(client)

if __name__ == "__main__":
    asyncio.run(main())
