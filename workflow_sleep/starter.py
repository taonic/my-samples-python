import asyncio
from datetime import timedelta
import logging

from temporalio.client import Client

from .worker import TASK_QUEUE
from .workflow import (
    ReminderParams,
    ReminderWorkflow,
    ResponsiveReminderWorkflow,
    reminder_workflow_id,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

CHANNEL = "#oncall"
INTERVAL_SECONDS = 5


async def main():
    client = await Client.connect("localhost:7233")

    params = ReminderParams(
        channel=CHANNEL,
        message="Please ack the deploy checklist",
        reminder_interval_seconds=INTERVAL_SECONDS,
    )

    handle = await client.start_workflow(
        ReminderWorkflow.run,
        params,
        id=reminder_workflow_id(CHANNEL),
        task_queue=TASK_QUEUE,
    )
    # log.info("Started %s", handle.id)
    # await handle.signal(ReminderWorkflow.acknowledge)
    # log.info("Acknowledged immediately: %s", await handle.result())

    # # 2. The `wait_condition` version, which returns as soon as the signal
    # #    lands. Wait past two intervals first so a couple of reminders go out.
    # responsive = await client.start_workflow(
    #     ResponsiveReminderWorkflow.run,
    #     params,
    #     id=reminder_workflow_id(CHANNEL) + "-responsive",
    #     task_queue=TASK_QUEUE,
    # )
    # log.info("Started %s", responsive.id)
    # await asyncio.sleep(INTERVAL_SECONDS * 2 + 1)
    # await responsive.signal(ResponsiveReminderWorkflow.acknowledge)
    # log.info("Acknowledged after two intervals: %s", await responsive.result())


if __name__ == "__main__":
    asyncio.run(main())
