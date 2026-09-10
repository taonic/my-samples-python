import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import send_slack_reminder

MAX_REMINDERS = 10


@dataclass
class ReminderParams:
    """Who to nag, with what, and how long to wait between nags."""
    channel: str
    message: str
    reminder_interval_seconds: int


def reminder_workflow_id(channel: str) -> str:
    """Deterministic Workflow ID so anyone can acknowledge without a handle."""
    return f"reminder-workflow-{channel.lstrip('#')}"


@workflow.defn
class ReminderWorkflow:
    def __init__(self) -> None:
        self._acknowledged = False

    @workflow.run
    async def run(self, params: ReminderParams) -> str:
        for attempt in range(1, MAX_REMINDERS + 1):
            await workflow.execute_activity(
                send_slack_reminder,
                args=[params.channel, params.message, attempt],
                start_to_close_timeout=timedelta(seconds=5),
            )

            await workflow.sleep(
                params.reminder_interval_seconds,
                summary=f"wait before reminder {attempt + 1}",
            )

        return f"No acknowledgement after {MAX_REMINDERS} reminders"


@workflow.defn
class ResponsiveReminderWorkflow:
    """Same nagging loop, but the wait races the acknowledgement signal.

    `wait_condition(pred, timeout=...)` is the idiomatic "sleep, but wake up
    early if something happens" primitive. Under the hood it is still a durable
    timer -- it just also completes as soon as the predicate turns true.
    """

    def __init__(self) -> None:
        self._acknowledged = False

    @workflow.run
    async def run(self, params: ReminderParams) -> str:
        for attempt in range(1, MAX_REMINDERS + 1):
            await workflow.execute_activity(
                send_slack_reminder,
                args=[params.channel, params.message, attempt],
                start_to_close_timeout=timedelta(seconds=5),
            )

            try:
                await workflow.wait_condition(
                    lambda: self._acknowledged,
                    timeout=timedelta(seconds=params.reminder_interval_seconds),
                    timeout_summary=f"wait before reminder {attempt + 1}",
                )
                # Predicate is true: the signal landed during the wait.
                return f"Acknowledged after {attempt} reminder(s)"
            except asyncio.TimeoutError:
                # The timeout of a `wait_condition` raises asyncio.TimeoutError,
                # not a Temporal error. Here it just means "nobody replied in
                # time", so keep nagging.
                continue

        return f"No acknowledgement after {MAX_REMINDERS} reminders"

    @workflow.signal
    def acknowledge(self) -> None:
        self._acknowledged = True
