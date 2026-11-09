from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import GreetInput, greet


@workflow.defn
class ErrorHandlingWorkflow:
    """Hello world, plus the three decisions error handling actually involves.

    Optionally catch `ActivityError` to handle failures from activities, e.g. except ActivityError as err:
    """

    @workflow.run
    async def run(self, params: GreetInput) -> str:
        try:
            await workflow.execute_activity(
                greet,
                params,
                start_to_close_timeout=timedelta(seconds=5),
            )
        except ActivityError as err:
            # The activity failed, so we can inspect the cause to see what happened.
            # If it was an `ApplicationError`, we can check the type and handle it accordingly.
            if isinstance(err.cause, ApplicationError):
                if err.cause.type == "InvalidGreeting":
                    return f"Invalid greeting: {err.cause.message}"
            # If we don't know how to handle it, re-raise the error to fail the workflow.
            raise
