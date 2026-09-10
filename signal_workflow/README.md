# Signals

Shows the three ways to get a signal into a running workflow: **signal-with-start**
from a client, a **plain client signal** on a handle, and a **workflow-to-workflow
signal** through an external workflow handle.

The example is a group that fills up: `GroupWorkflow` collects members from `join`
signals and completes as soon as it hits its target size — or when the
registration window closes, whichever comes first.

## How it works

- [workflow.py](workflow.py) — `GroupWorkflow.run` sits in a `wait_condition` on
  `len(self.members) >= target_size` with a timeout; the `join` signal handler
  appends to `self.members`, which is what re-evaluates that condition.
  `JoinGroupWorkflow` is a separate workflow that signals `GroupWorkflow` via
  `workflow.get_external_workflow_handle_for`.
- [worker.py](worker.py) — runs both workflows on the `signal-workflow` task queue.
- [starter.py](starter.py) — exercises all three signalling paths against one group.
- [signal_only.py](signal_only.py) — signals an already-running workflow from a
  separate process, the Python equivalent of the CLI call below.

### Signals vs. queries

A signal is **write-only and asynchronous**: it returns nothing to the caller,
is durably recorded in workflow history, and is delivered exactly once. Awaiting
`handle.signal(...)` resolves when the *server accepts* the signal, not when the
workflow has processed it. Handlers should mutate state and return promptly —
let the `run` method react to the change.

### Signal-with-start

`start_workflow(..., start_signal=..., start_signal_args=[...])` is a single
call that starts the workflow if it is not running and delivers the signal
either way. It is the idiomatic entry point for entity-style workflows: two
racing callers can't produce a duplicate-ID failure or a lost signal. When the
workflow already exists, the run arguments are ignored — only the signal lands.

### Signalling from another workflow

`workflow.get_external_workflow_handle_for(GroupWorkflow.run, workflow_id)` gives
a typed handle to a workflow that already exists (the workflow type is used for
typing only, it is not validated). The `await handle.signal(...)` is a durable
command — it appears in the sender's history as
`SignalExternalWorkflowExecutionInitiated` and is retried by the server — and it
raises if the target workflow isn't running, rather than silently doing nothing.

### The `wait_condition` timeout

`wait_condition(..., timeout=...)` raises `asyncio.TimeoutError`, not a Temporal
timeout error. Catching it is how you express "the window closed" as a normal
outcome instead of a workflow failure.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m signal_workflow.worker
```

In another, run the starter:

```bash
uv run python -m signal_workflow.starter
```

Output (the two external-workflow joins race, so their order varies):

```
INFO:__main__:Started (or signalled) group-workflow-group-1
INFO:__main__:Group group-1: members=['user-1', 'user-2', 'user-4', 'user-3'] filled=True
```

Four members join a group with a target size of four, so the workflow completes
immediately with `filled=True`. Lower the signal count (or raise `TARGET_SIZE`)
and it instead returns `filled=False` after the 30-second window closes.

## Same signal from the CLI

While a group workflow is still open:

```bash
temporal workflow signal \
    --workflow-id="group-workflow-group-1" \
    --name="join" \
    --input='{"user_id": "user-9", "group_id": "group-1"}'
```

`--name` is the signal name (`@workflow.signal(name="join")`), and `--input` is
JSON — an object here because the handler takes a dataclass. Pass repeated
`--input` flags for a handler taking multiple arguments.

[signal_only.py](signal_only.py) does the same thing from Python:

```bash
uv run python -m signal_workflow.signal_only
```

Signalling a workflow that has already completed fails with a
"workflow execution already completed" error — signals only reach open workflows.
