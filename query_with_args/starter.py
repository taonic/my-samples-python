import asyncio
import logging

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from .worker import TASK_QUEUE
from .workflow import GroupWorkflow, JoinInput

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WORKFLOW_ID = "state-id-0"


async def main():
    client = await Client.connect("localhost:7233")

    # The workflow runs forever, so reuse it if it is already running instead of
    # failing on a duplicate Workflow ID.
    handle = await client.start_workflow(
        GroupWorkflow.run,
        id=WORKFLOW_ID,
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    # Populate some state via signals.
    await handle.signal(GroupWorkflow.join, JoinInput("alice", "group-1"))
    await handle.signal(GroupWorkflow.join, JoinInput("bob", "group-2"))
    await handle.signal(GroupWorkflow.join, JoinInput("carol", "group-1"))

    # Query with an argument. Passing the query handler method gives type-safe
    # argument and return types; `handle.query("members", "group-1")` by name
    # works too, but is untyped.
    members = await handle.query(GroupWorkflow.members_for_group, "group-1")
    log.info("Members of group-1: %s", members)

    members = await handle.query(GroupWorkflow.members_for_group, "group-2")
    log.info("Members of group-2: %s", members)

    # Unknown group -> empty list, not an error.
    members = await handle.query(GroupWorkflow.members_for_group, "group-3")
    log.info("Members of group-3: %s", members)


if __name__ == "__main__":
    asyncio.run(main())
