import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .workflow import GroupWorkflow, JoinGroupWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TASK_QUEUE = "signal-workflow"


async def main():
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GroupWorkflow, JoinGroupWorkflow],
    )
    log.info("Worker started on task queue %s", TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
