import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import fetch_item
from .workflow import ParallelActivityWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TASK_QUEUE = "parallel-activity-threadpool"


async def main():
    client = await Client.connect("localhost:7233")

    # Synchronous activities require an `activity_executor`. A ThreadPoolExecutor
    # lets the worker run that many synchronous activities concurrently — true
    # parallelism for blocking IO. Size `max_workers` to match how many activities
    # you expect to run at once on this worker.
    with ThreadPoolExecutor(max_workers=10) as activity_executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[ParallelActivityWorkflow],
            activities=[fetch_item],
            activity_executor=activity_executor,
            workflow_task_executor=ThreadPoolExecutor(max_workers=10)
        )
        log.info("Worker started on task queue %s", TASK_QUEUE)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
