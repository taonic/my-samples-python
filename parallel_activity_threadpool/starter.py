import asyncio
import logging

from temporalio.client import Client

from .worker import TASK_QUEUE
from .workflow import ParallelActivityWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main():
    client = await Client.connect("localhost:7233")

    items = [f"item-{i}" for i in range(10)]
    result = await client.execute_workflow(
        ParallelActivityWorkflow.run,
        items,
        id="parallel-activity-threadpool-workflow",
        task_queue=TASK_QUEUE,
    )
    log.info("Workflow result: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
