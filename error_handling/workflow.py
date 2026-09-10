from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import GreetInput, greet


@workflow.defn
class GreetingWorkflow:
    """Hello world, plus the three decisions error handling actually involves.

    Optionally catch `ActivityError` to handle failures from activities, e.g. except ActivityError as err:
    """

    @workflow.run
    async def run(self, params: GreetInput) -> str:
        return await workflow.execute_activity(
            greet,
            params,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
