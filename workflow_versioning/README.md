# Workflow Versioning and Replay

A deliberately minimal workflow — **one activity, nothing else** — plus a
replayer that checks new workflow code against histories that already exist.
This is the sample to edit live when demonstrating versioning: the workflow is
small enough that every event in the history is accounted for.

## How it works

- [activities.py](activities.py) — `charge_order` takes an `OrderInput` dataclass
  and returns a confirmation string.
- [workflow.py](workflow.py) — `OrderWorkflow` runs that one activity and returns
  its result. Its history is just `ScheduleActivityTask` then
  `CompleteWorkflowExecution`.
- [worker.py](worker.py) — runs both on the `workflow-versioning` task queue.
- [starter.py](starter.py) — executes one workflow under a unique Workflow ID
  (`order-workflow-<uuid>`), so runs stack up instead of colliding.
- [replayer.py](replayer.py) — re-runs a past history against the code in this
  directory and reports whether it is still compatible. With no arguments it
  picks the most recent `OrderWorkflow` run; `--all` replays every run it can
  find, and `--debug` makes the code debugger-friendly (see below).

The `Replayer` is not a worker: it never polls a task queue and never runs your
activities. It feeds recorded history events back into the workflow code and
checks that the code issues the same commands, in the same order, that the
history already contains. That makes it the cheapest possible pre-deploy check —
and something you can run in CI over histories pulled from production.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m workflow_versioning.worker
```

In another, run the starter, then the replayer:

```bash
uv run python -m workflow_versioning.starter
uv run python -m workflow_versioning.replayer
```

```
INFO:__main__:order-workflow -> Order order-1234 charged: charge-order-1234
INFO:__main__:Replayed order-workflow (run 01a08b1f-8d77-74e2-a1dc-3289f2e667b4)
INFO:__main__:Replay passed: the current workflow code is compatible
```

By default the replayer picks the most recent `OrderWorkflow` run and fetches
that one history by Workflow ID (`handle.fetch_history()`); pass
`--workflow-id` to choose a specific one. `--all` instead streams every
`OrderWorkflow` history the Visibility API can find and replays them as a
batch:

```bash
uv run python -m workflow_versioning.replayer --all
```

The replayer exits non-zero when any history fails, so it drops straight into a
CI step.

## Workshop: breaking and then versioning the workflow

### 1. Make an incompatible change

The workflow above has already run to completion, so its history is fixed. Add a
wait before the charge in [workflow.py](workflow.py):

```python
    @workflow.run
    async def run(self, order: OrderInput) -> str:
        workflow.logger.info("Starting order %s", order.order_id)

        await workflow.sleep(timedelta(seconds=1), summary="fraud hold")

        confirmation = await workflow.execute_activity(
            ...
```

Run the replayer again — no need to restart anything, the replayer uses whatever
code is on disk:

```bash
uv run python -m workflow_versioning.replayer
```

```
ERROR:__main__:Run 01a08b1f-8d77-74e2-a1dc-3289f2e667b4 is INCOMPATIBLE with the current code:
Workflow activation completion failed: Failure { failure: Some(Failure { message:
"[TMPRL1100] Nondeterminism error: Timer machine does not handle this event:
HistoryEvent(id: 5, ActivityTaskScheduled)", ...
```

Read the message literally: the new code started a **timer**, but at that point
in history the next thing that happened was an **activity being scheduled**. The
SDK has no way to reconcile the two, so it refuses. Deploying this would wedge
every open workflow that had already passed that point.

Note that only the *command sequence* is checked. Changes that do not touch it
are safe by construction — renaming a local variable, changing the log message,
editing activity code, adding a field to `OrderInput`, or adjusting an activity
timeout. Try one and watch replay stay green.

### 2. Guard the change with a patch

`workflow.patched(id)` returns `True` for new runs and `False` for any run whose
history has no marker for that patch, so old and new executions each keep their
own behavior:

```python
        if workflow.patched("fraud-hold"):
            await workflow.sleep(timedelta(seconds=1), summary="fraud hold")
```

Replay again — it passes. The old history has no `fraud-hold` marker, so
`patched` returns `False` and the code takes exactly the path history recorded.

Now restart the worker and start a fresh run:

```bash
uv run python -m workflow_versioning.starter
```

Its history shows what the patch cost: a `MarkerRecorded` event holding the
patch ID, an `UpsertWorkflowSearchAttributes` that adds `fraud-hold` to the
`TemporalChangeVersion` search attribute, and then the timer.

```bash
temporal workflow show --workflow-id <the new id>
```

That search attribute is how you find which runs took the new path:

```bash
temporal workflow list --query 'TemporalChangeVersion = "fraud-hold"'
```

### 3. Retire the patch

Once no open workflow predates the change, the patch is dead weight. Removing it
happens in two deploys:

1. Replace `workflow.patched("fraud-hold")` with
   `workflow.deprecate_patch("fraud-hold")` and keep only the new code path.
   Deprecated patches stop writing markers but still tolerate histories that
   contain them, so runs from step 2 keep replaying.
2. Once those runs are gone too, delete the `deprecate_patch` call.

Run the replayer after each step — that is the whole point of having it.

## Debugging in VS Code (with uv)

`uv sync` creates `.venv` at the repo root, and
[.vscode/settings.json](../.vscode/settings.json) points VS Code at
`${workspaceFolder}/.venv/bin/python`. That is the whole uv integration: the
debugger launches that interpreter directly, so nothing has to run through
`uv run`. [.vscode/launch.json](../.vscode/launch.json) has ready-made
configurations for the worker, the starter, and the replayer.

**Debug the replayer, not a live worker.** Replay has no server connection and
no live Workflow Task, so nothing expires while you sit on a breakpoint. Pick
*Replay: workflow_versioning (debuggable)* and set breakpoints in
[workflow.py](workflow.py). That config passes `--debug`, which changes two
things in [replayer.py](replayer.py):

- `debug_mode=True` — turns off the SDK's deadlock detector, which otherwise
  fails the workflow task if it does not yield within 2 seconds.
- `workflow_runner=UnsandboxedWorkflowRunner()` — the workflow sandbox
  re-imports workflow code in a restricted environment, which stops the IDE
  from binding breakpoints to it.

Setting `TEMPORAL_DEBUG=1` in the environment is equivalent to `debug_mode=True`
and is what the worker configuration uses; it does *not* disable the sandbox.

### Extending the Workflow Task Timeout on the dev server

To step through a **live** workflow anyway, the server-side Workflow Task
Timeout is the constraint: the default is 10 seconds, after which the server
assumes the worker died, reschedules the task, and your workflow replays from
the start. Raise the default with dynamic config:

```bash
temporal server start-dev \
    --dynamic-config-value history.defaultWorkflowTaskTimeout=120
```

Values are JSON, hence the quoted duration string. Three things to know:

- **120 seconds is a hard ceiling.** It is a compile-time constant in the
  server, not a dynamic config value, so nothing raises it. Ask for 300s and
  the started workflow silently gets 120s.
- **The server default only applies when the client sends no timeout.** The
  Python SDK sends none unless you pass `task_timeout`, so it picks up the
  120s. `temporal workflow start` sends its own 10s default, so pass
  `--task-timeout 120s` there.
- **Per-run alternative, no server restart:** pass
  `task_timeout=timedelta(seconds=120)` to `start_workflow` in
  [starter.py](starter.py).

Two minutes is still two minutes. It is enough to look around one workflow task,
which is why replay debugging remains the better default.

## Versioning strategies, briefly

Patching is the *code-level* answer: one workflow definition handles both old
and new histories. It suits small, surgical changes and needs no deploy
choreography, but every live patch is a branch someone has to remove later.

The alternatives worth naming in a workshop:

- **A new workflow type** (`OrderWorkflowV2`) — no branching at all, but the
  starter has to choose, and both workers run until the old runs drain. Good for
  changes so large that patching would be unreadable.
- **Worker Versioning** with pinned or auto-upgrade behaviors — the deployment
  decides which build serves which run, instead of the workflow code deciding.
  This scales better than patching for frequent releases; see
  [Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning).

Whichever you use, replay is the check that tells you whether you needed one.
