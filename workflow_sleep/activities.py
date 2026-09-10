from temporalio import activity


@activity.defn
async def send_slack_reminder(channel: str, message: str, attempt: int) -> None:
    """Pretend to post a reminder to Slack.

    Nothing here knows about timers -- the waiting all happens in the workflow,
    which is the point: the activity stays a short, retryable side effect.
    """
    activity.logger.info(
        "Reminder #%d to %s: %s", attempt, channel, message
    )
