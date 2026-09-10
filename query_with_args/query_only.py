"""Query an already-running workflow from a separate process.

Mirrors the equivalent CLI call:

    temporal workflow query \
        --workflow-id="state-id-0" \
        --type="members" \
        --input='"group-1"'
"""

import asyncio

from temporalio.client import Client

from .workflow import GroupWorkflow


async def main():
    client = await Client.connect("localhost:7233")

    # `get_workflow_handle_for` binds the handle to the workflow's type, so the
    # query handler reference below type-checks. Use `get_workflow_handle` if you
    # only have the ID and want to query by string name.
    handle = client.get_workflow_handle_for(GroupWorkflow.run, "state-id-0")
    members = await handle.query(GroupWorkflow.members_for_group, "group-1")

    print(members)


if __name__ == "__main__":
    asyncio.run(main())
