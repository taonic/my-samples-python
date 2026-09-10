# Error Handling

Hello world, with the error handling that a real activity needs. One activity,
one workflow, four scenarios — enough to see how a failure travels from the
activity, through the retry policy, into the workflow, and out to the client.

## How it works

- [activities.py](activities.py) — `greet` fails in two deliberately different
  ways: an empty name raises a **non-retryable** `ApplicationError`, while a
  `fail_attempts` budget simulates a **transient** fault by raising a plain
  `RuntimeError` on early attempts.
- [workflow.py](workflow.py) — `ErrorHandlingWorkflow` sets a retry policy, catches
  `ActivityError`, inspects `err.cause`, and then either fails the workflow or
  degrades to a fallback greeting depending on what went wrong.
- [worker.py](worker.py) — runs both on the `error-handling` task queue.
- [starter.py](starter.py) — runs all four scenarios and catches
  `WorkflowFailureError` for the one that fails.

### Errors arrive wrapped

Whatever an activity raises is converted into an `ApplicationError` and
delivered to the workflow inside an `ActivityError`, so handling code looks at
two levels:

```python
except ActivityError as err:
    cause = err.cause            # the ApplicationError from the activity
    if isinstance(cause, ApplicationError) and cause.type == "InvalidGreeting":
```

The same nesting repeats at the client: a failed workflow raises
`WorkflowFailureError`, and `err.cause` is the `ApplicationError` the workflow
raised. Match on `cause.type` — the string you passed to `ApplicationError` —
rather than on the message, which is prose and will get reworded.

### Retryable vs non-retryable

Every exception from an activity is retryable by default, which is the right
default for network calls, timeouts, and dropped connections. `non_retryable=True`
is for failures that no amount of retrying will fix — bad input, a 4xx response,
a business rule violation:

```python
raise ApplicationError(
    "name must not be empty",
    type="InvalidGreeting",
    non_retryable=True,
)
```

Temporal stops immediately when it sees that flag: in the `invalid` scenario
below the activity runs exactly once, with no retry delay and no waiting around.

Note also that the workflow's `RetryPolicy` caps `maximum_attempts=3`. The
default policy retries **forever** with exponential backoff, which is often what
you want in production — but a workflow can only handle a failure that it is
eventually allowed to see, so an unbounded retry means the `except` block never
runs.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m error_handling.worker
```

In another, run the starter:

```bash
uv run python -m error_handling.starter
```

```
INFO:__main__:success -> Hello, Temporal!
INFO:__main__:retried -> Hello, Temporal!
INFO:__main__:fallback -> Hello, stranger!
ERROR:__main__:invalid -> workflow failed: InvalidGreeting: cannot greet: name must not be empty
```

The four scenarios, and what to watch in the worker's log:

| Scenario | Input | What happens |
| --- | --- | --- |
| `success` | `GreetInput("Temporal")` | Activity succeeds on attempt 1. |
| `retried` | `fail_attempts=2` | Fails twice, succeeds on attempt 3. The workflow never sees an error. |
| `fallback` | `fail_attempts=5` | Exhausts all 3 attempts; the workflow catches it and returns `Hello, stranger!`. |
| `invalid` | `GreetInput("")` | Non-retryable, so **one** attempt only; the workflow re-raises and the client fails. |

The retries are visible in the worker log as repeated attempts on the same
activity:

```
WARNING:temporalio.activity:Transient failure on attempt 1 ...
WARNING:temporalio.activity:Transient failure on attempt 2 ...
INFO:temporalio.activity:Greeting Temporal on attempt 3 ...
```

On the server, though, retries are almost invisible — and that is worth seeing
for yourself:

```bash
temporal workflow show --workflow-id <id>
```

```
    5  ActivityTaskScheduled
    6  ActivityTaskStarted
    7  ActivityTaskFailed
```

Three attempts, one `ActivityTaskStarted` and one `ActivityTaskFailed`.
Intermediate attempts are **not** written to history; instead the single
`ActivityTaskStarted` event carries `attempt: 3`, and the `ActivityTaskFailed`
event holds the last failure plus a `retryState` of
`MAXIMUM_ATTEMPTS_REACHED`. That keeps histories small no matter how long an
activity retries, but it also means the Web UI is not where you watch a retry
loop in progress — use the pending-activity section of:

```bash
temporal workflow describe --workflow-id <id>
```

which shows the live attempt count, the last failure, and when the next attempt
is scheduled while the activity is still retrying.

## Choosing what to do with a failure

The `except` block is where the interesting decision lives, and this sample
takes both branches on purpose:

- **Fail the workflow** (the `invalid` case) — the request was wrong and no
  recovery is meaningful. Re-raise as an `ApplicationError` with a `type` the
  caller can match on. Marking *that* error non-retryable matters too: without
  it, a failure raised out of workflow code is treated as a workflow task
  failure and retried indefinitely rather than failing the run.
- **Degrade** (the `fallback` case) — the step is not worth failing over, so
  return something usable. In a real workflow this is also where compensation
  goes: undo the earlier steps, then fail or return.

What *not* to do is catch the error and continue as if the activity had
succeeded. Swallowing a failure in workflow code hides it from history, from
the Web UI, and from whoever is on call.

## Beyond activity errors

The same `err.cause` pattern extends to the other failure sources:

- **Timeouts** — `TimeoutError` as the cause, from
  `start_to_close_timeout` or `schedule_to_close_timeout` elapsing.
- **Child workflows** — `ChildWorkflowError`, wrapping the child's own failure.
- **Cancellation** — `CancelledError`; use
  `workflow.CancellationScope` and `asyncio.shield` to run cleanup before it
  propagates.

See [Errors and failures](https://docs.temporal.io/references/failures) for the
full taxonomy.
