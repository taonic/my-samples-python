import asyncio
from datetime import timedelta

from temporalio import workflow


@workflow.defn
class OrderWorkflow:
    """A trivial long-ish running workflow.

    It just sleeps so that, once started, it stays in the ``Running`` state long
    enough to be discoverable by the visibility queries in ``starter.py``. A real
    order workflow would do actual work here.
    """

    @workflow.run
    async def run(self, customer_id: str, order_id: str) -> str:
        workflow.logger.info("Processing order %s for customer %s", order_id, customer_id)
        await asyncio.sleep(timedelta(minutes=5).total_seconds())
        return f"order {order_id} for customer {customer_id} complete"
