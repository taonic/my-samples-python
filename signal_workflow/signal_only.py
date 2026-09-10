"""Signal an already-running group workflow from a separate process.

Mirrors the equivalent CLI call:

    temporal workflow signal \
        --workflow-id="group-workflow-group-1" \
        --name="join" \
        --input='{"user_id": "user-9", "group_id": "group-1"}'
"""

import asyncio

from temporalio.client import Client

from .workflow import GroupWorkflow, JoinInput, group_workflow_id

GROUP_ID = "group-1"


async def main():
    client = await Client.connect("localhost:7233")

    # `get_workflow_handle_for` binds the handle to the workflow type so the
    # signal handler reference below type-checks. Use `get_workflow_handle` if
    # you only have the ID and want to signal by string name.
    handle = client.get_workflow_handle_for(
        GroupWorkflow.run, group_workflow_id(GROUP_ID)
    )
    await handle.signal(
        GroupWorkflow.join,
        JoinInput(user_id="user-9", group_id=GROUP_ID),
    )
    print(f"Signalled {handle.id}")


if __name__ == "__main__":
    asyncio.run(main())
