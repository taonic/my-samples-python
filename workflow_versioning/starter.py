import asyncio
import logging
import uuid

from temporalio.client import Client

from .activities import OrderInput
from .worker import TASK_QUEUE
from .workflow import OrderWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WORKFLOW_ID = "order-workflow"

async def main():
    client = await Client.connect("localhost:7233")

    result = await client.execute_workflow(
        OrderWorkflow.run,
        OrderInput(order_id="order-1234", amount_cents=4999),
        id=f"{WORKFLOW_ID}-{uuid.uuid4()}",
        task_queue=TASK_QUEUE,
    )
    log.info(result)


if __name__ == "__main__":
    asyncio.run(main())
