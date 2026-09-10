import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import send_slack_reminder
from .workflow import ReminderWorkflow, ResponsiveReminderWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TASK_QUEUE = "workflow-sleep"


async def main():
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ReminderWorkflow, ResponsiveReminderWorkflow],
        activities=[send_slack_reminder],
    )
    log.info("Worker started on task queue %s", TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
