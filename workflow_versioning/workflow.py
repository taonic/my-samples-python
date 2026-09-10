from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import OrderInput, charge_order


@workflow.defn
class OrderWorkflow:
    """
    Use await workflow.sleep(timedelta(seconds=10)) to sleep for time to stop worker.
    Add another activity to the workflow and trigger NDE.
    Use workflow.patched("") to mark the new activity as patched and not run it in replayer.
    Optionally, watch `workflow.unsafe.is_replaying()` to see if the workflow is replaying and skip the new activity.
    """

    @workflow.run
    async def run(self, order: OrderInput) -> None:
        await workflow.execute_activity(
            charge_order,
            order,
            start_to_close_timeout=timedelta(seconds=10),
        )

        return
