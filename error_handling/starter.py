import asyncio
import logging
import uuid

from temporalio.client import Client, WorkflowFailureError

from .activities import GreetInput
from .worker import TASK_QUEUE
from .workflow import ErrorHandlingWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WORKFLOW_ID = "error-handling-workflow"


async def main():
    client = await Client.connect("localhost:7233")
    try:
        result = await client.execute_workflow(
            ErrorHandlingWorkflow.run,
            GreetInput(name=""),
            id=f"{WORKFLOW_ID}-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        log.info(result)
    except WorkflowFailureError as err:
        log.error("Workflow failed: %s", err.cause)


if __name__ == "__main__":
    asyncio.run(main())
