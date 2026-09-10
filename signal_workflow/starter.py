import asyncio
import logging

from temporalio.client import Client

from .worker import TASK_QUEUE
from .workflow import (
    GroupParams,
    GroupWorkflow,
    JoinGroupWorkflow,
    JoinInput,
    group_workflow_id,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

GROUP_ID = "group-1"
TARGET_SIZE = 4


async def main():
    client = await Client.connect("localhost:7233")

    # 1. Signal-with-start: one call that starts the workflow if it is not
    #    running and delivers the signal either way. Nothing is lost if two
    #    callers race -- the first starts it, the second just signals it. Note
    #    the run args (GroupParams) are ignored when the workflow already exists.
    handle = await client.start_workflow(
        GroupWorkflow.run,
        GroupParams(
            group_id=GROUP_ID,
            target_size=TARGET_SIZE,
            registration_window_seconds=20,
        ),
        id=group_workflow_id(GROUP_ID),
        task_queue=TASK_QUEUE,
        start_signal="join",
        start_signal_args=[JoinInput(user_id="user-1", group_id=GROUP_ID)],
    )
    log.info("Started (or signalled) %s", handle.id)

    # # 2. Plain client signal on a handle. Passing the handler method keeps the
    # #    payload type-checked; `handle.signal("join", ...)` by name works too.
    # await handle.signal(
    #     GroupWorkflow.join,
    #     JoinInput(user_id="user-2", group_id=GROUP_ID),
    # )

    # # 3. Workflow-to-workflow signal: each JoinGroupWorkflow signals the group
    # #    workflow through an external workflow handle.
    # await asyncio.gather(
    #     *[
    #         client.execute_workflow(
    #             JoinGroupWorkflow.run,
    #             JoinInput(user_id=user_id, group_id=GROUP_ID),
    #             id=f"join-{user_id}-{GROUP_ID}",
    #             task_queue=TASK_QUEUE,
    #         )
    #         for user_id in ("user-3", "user-4")
    #     ]
    # )

    # # The group workflow returns as soon as the target size is reached; if fewer
    # # members had joined it would return once the 30s window closed.
    # result = await handle.result()
    # log.info(
    #     "Group %s: members=%s filled=%s",
    #     result.group_id,
    #     result.members,
    #     result.filled,
    # )


if __name__ == "__main__":
    asyncio.run(main())
