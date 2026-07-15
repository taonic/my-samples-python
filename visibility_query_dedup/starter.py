import asyncio
import logging

from temporalio.client import Client

from .worker import TASK_QUEUE
from .workflow import OrderWorkflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# This sample assumes workflow IDs follow a structured naming convention:
#
#     order-{customer_id}-{order_id}
#
# We want to enforce a business rule: a customer may only have ONE open order
# workflow at a time, regardless of the trailing order_id. In other words we
# need to match the "order-{customer_id}-" section of the ID and treat the
# order_id portion as a wildcard.
#
# Temporal's visibility list filters support a `STARTS_WITH` operator on string
# fields such as the built-in `WorkflowId` attribute. A prefix match is exactly
# a "wildcard for the rest of the ID", so `STARTS_WITH "order-{customer_id}-"`
# matches every order workflow for that customer no matter the order_id.


def workflow_id_for(customer_id: str, order_id: str) -> str:
    return f"order-{customer_id}-{order_id}"


def open_orders_query(customer_id: str) -> str:
    """A visibility filter matching every *open* order workflow for a customer.

    - `WorkflowId STARTS_WITH "order-<customer>-"` is the wildcard prefix match.
    - `ExecutionStatus = "Running"` limits it to currently-open workflows
      (drop this clause to also count completed/failed/terminated ones).

    String literals are single-quoted in the list filter grammar.
    """
    prefix = f"order-{customer_id}-"
    return f"WorkflowId STARTS_WITH '{prefix}' AND ExecutionStatus = 'Running'"


async def has_open_order(client: Client, customer_id: str) -> bool:
    """Return True if the customer already has an open order workflow.

    Uses `count_workflows`, which is cheaper than listing when you only need to
    know whether any match exists.
    """
    query = open_orders_query(customer_id)
    count = await client.count_workflows(query)
    log.info("Query [%s] matched %d open workflow(s)", query, count.count)
    return count.count > 0


async def start_order_if_none_open(
    client: Client, customer_id: str, order_id: str
) -> None:
    """Decide whether to reject or start a new order workflow for the customer."""
    if await has_open_order(client, customer_id):
        # An open order already exists for this customer -> reject.
        log.warning(
            "Rejecting new order %s: customer %s already has an open order",
            order_id,
            customer_id,
        )
        return

    wf_id = workflow_id_for(customer_id, order_id)
    handle = await client.start_workflow(
        OrderWorkflow.run,
        args=[customer_id, order_id],
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    log.info("Started new order workflow %s (run %s)", wf_id, handle.result_run_id)


async def main():
    client = await Client.connect("localhost:7233")

    customer_id = "cust-42"

    # First order for the customer: no open workflow exists yet -> starts.
    await start_order_if_none_open(client, customer_id, order_id="A100")

    # Visibility is eventually consistent. After a write it can take a short
    # moment for the new workflow to become searchable, so give it a beat before
    # relying on the query for the second decision.
    await asyncio.sleep(2)

    # Second order, different order_id but same customer. The prefix wildcard
    # matches the still-running first order -> this one is rejected.
    await start_order_if_none_open(client, customer_id, order_id="B200")


if __name__ == "__main__":
    asyncio.run(main())
