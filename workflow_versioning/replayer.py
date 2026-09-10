import argparse
import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from .workflow import OrderWorkflow

async def main():
    client = await Client.connect("localhost:7233")
    replayer = Replayer(workflows=[OrderWorkflow])
    executions = client.list_workflows(f"WorkflowType = '{OrderWorkflow.__name__}'")
    results = await replayer.replay_workflows(
        executions.map_histories(),
        raise_on_replay_failure=False,
    )
    print(results)


if __name__ == "__main__":
    asyncio.run(main())
