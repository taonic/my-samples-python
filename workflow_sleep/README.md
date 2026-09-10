# Workflow Sleep (Durable Timers)

Shows `workflow.sleep` as a **durable timer**: a workflow can be idle for five
seconds or five months without holding a worker, a thread, or a connection open.

The example is a reminder loop — post a nag to Slack, wait, and stop early once
someone acknowledges. It ships in two flavours so the trade-off is visible:
`ReminderWorkflow` sleeps a fixed interval and checks afterwards, while
`ResponsiveReminderWorkflow` races the wait against the signal.

## How it works

- [workflow.py](workflow.py) — `ReminderWorkflow.run` loops up to ten times,
  running the reminder activity and then `await workflow.sleep(...)`. The
  `acknowledge` signal flips a flag that the loop checks after each sleep.
  `ResponsiveReminderWorkflow` swaps the sleep for
  `wait_condition(lambda: self._acknowledged, timeout=...)`.
- [activities.py](activities.py) — `send_slack_reminder`, a short retryable side
  effect that knows nothing about timing.
- [worker.py](worker.py) — runs both workflows and the activity on the
  `workflow-sleep` task queue.
- [starter.py](starter.py) — runs both flavours and acknowledges each, so the
  difference in behaviour shows up in the output.
- [acknowledge_only.py](acknowledge_only.py) — signals a mid-sleep workflow from
  a separate process, the Python equivalent of the CLI call below.

### What makes the sleep durable

`await workflow.sleep(...)` is not a thread sleep and not an in-memory task. It
issues a `StartTimer` command, which the server records as `TimerStarted`; when
the deadline passes the server writes `TimerFired` and schedules a workflow task.
Between those two events there is **nothing running** — kill every worker in the
fleet and the timer still fires, with whichever worker is up at that moment
picking the workflow back up mid-loop.

On replay the timer is satisfied straight from history, so a workflow that slept
for a week re-executes its loop in milliseconds. That is why workflow code must
never call `time.sleep` (it blocks the workflow thread and stalls the worker) and
never branch on `datetime.now()` — use `workflow.now()`, which replays the
recorded time.

`workflow.sleep` takes either a `float` of seconds or a `timedelta`. The optional
`summary=` string is attached to the `TimerStarted` event and rendered on the
timeline in the Web UI, which is worth setting when a workflow has several timers
that would otherwise be indistinguishable.

### Sleeping vs. waking early

A plain sleep always runs to completion. In `ReminderWorkflow` an acknowledgement
that lands one second into a five-second wait is not noticed until the timer
fires — the loop only reads the flag afterwards:

```python
await workflow.sleep(params.reminder_interval_seconds)
if self._acknowledged:
    return f"Acknowledged after {attempt} reminder(s)"
```

When "wait up to N seconds, but return as soon as X happens" is what you actually
mean, use a timed `wait_condition` instead. It is still backed by a durable
timer, it just also completes when the predicate turns true:

```python
try:
    await workflow.wait_condition(
        lambda: self._acknowledged,
        timeout=timedelta(seconds=params.reminder_interval_seconds),
    )
    return f"Acknowledged after {attempt} reminder(s)"
except asyncio.TimeoutError:
    continue
```

The timeout raises `asyncio.TimeoutError`, not a Temporal error — catching it is
how you express "nobody replied in time" as a normal outcome rather than a
workflow failure.

### Long sleeps and history size

Each timer costs two history events, so an hourly poll that runs for a year is
about 17,500 events plus whatever the loop does — past the point where
[continue-as-new](https://docs.temporal.io/workflows#continue-as-new) belongs in
the design. A handful of long sleeps costs nothing; it is the tight loop with a
short interval and no bound that gets expensive. This sample caps itself at ten
iterations.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m workflow_sleep.worker
```

In another, run the starter:

```bash
uv run python -m workflow_sleep.starter
```

Output:

```
INFO:__main__:Started reminder-workflow-oncall
INFO:__main__:Acknowledged immediately: Acknowledged after 1 reminder(s)
INFO:__main__:Started reminder-workflow-oncall-responsive
INFO:__main__:Acknowledged after two intervals: Acknowledged after 3 reminder(s)
```

The first workflow is acknowledged before its timer even starts, yet still takes
a full five-second interval to return. The second is acknowledged after two
intervals and returns immediately, having sent three reminders.

## Signalling a sleeping workflow

Start one with a long interval so it stays parked on a timer:

```bash
temporal workflow start \
    --workflow-id="reminder-workflow-oncall" \
    --type="ReminderWorkflow" \
    --task-queue="workflow-sleep" \
    --input='{"channel":"#oncall","message":"cli started","reminder_interval_seconds":3600}'
```

A sleeping workflow is an open workflow, so it accepts signals normally:

```bash
temporal workflow signal \
    --workflow-id="reminder-workflow-oncall" \
    --name="acknowledge"
```

[acknowledge_only.py](acknowledge_only.py) does the same from Python:

```bash
uv run python -m workflow_sleep.acknowledge_only
```

Either way the flag is set immediately, but `ReminderWorkflow` will not act on it
until the hour is up — the signal is durable, the sleep is not interruptible.
`temporal workflow show --workflow-id="reminder-workflow-oncall"` shows the
`TimerStarted` event sitting there with its summary, waiting.

## See the restart survive

With a workflow mid-sleep, stop the worker entirely, wait for the interval to
pass, then start it again. The next reminder goes out on the restarted worker:
the timer fired server-side while nothing was running.
