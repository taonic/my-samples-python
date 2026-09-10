from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError


@dataclass
class GreetInput:
    name: str

@activity.defn
async def greet(params: GreetInput) -> str:
    """Optionally raise an `ApplicationError` to simulate a failure in the activity:
        if not params.name:
            raise ApplicationError("name must not be empty", type="InvalidGreeting", non_retryable=True)
    """
    return f"Hello, {params.name}!"
