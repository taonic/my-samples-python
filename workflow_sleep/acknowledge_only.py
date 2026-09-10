"""Acknowledge a reminder workflow that is mid-sleep, from another process.

Mirrors the equivalent CLI call:

    temporal workflow signal \
        --workflow-id="reminder-workflow-oncall" \
        --name="acknowledge"
"""

import asyncio

from temporalio.client import Client

from .workflow import ReminderWorkflow, reminder_workflow_id

CHANNEL = "#oncall"


async def main():
    client = await Client.connect("localhost:7233")

    # A sleeping workflow is still an open workflow: signals are accepted while
    # the timer is pending, and no worker needs to be holding it in memory.
    handle = client.get_workflow_handle_for(
        ReminderWorkflow.run, reminder_workflow_id(CHANNEL)
    )
    await handle.signal(ReminderWorkflow.acknowledge)
    print(f"Acknowledged {handle.id}")


if __name__ == "__main__":
    asyncio.run(main())
