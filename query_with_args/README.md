# Query with Arguments

Shows a **query handler that takes input parameters**. The workflow holds group
membership state built up by signals, and callers query it with the group they
care about — the filtering happens inside the workflow rather than in the client.

## How it works

- [workflow.py](workflow.py) — `GroupWorkflow` waits forever (`wait_condition(lambda: False)`),
  a `join` signal appends `JoinInput(user_id, group_id)` to its state, and the
  `members` query takes a `group_id` argument and returns only that group's users.
- [worker.py](worker.py) — runs the workflow on the `query-with-args` task queue.
- [starter.py](starter.py) — starts (or reuses) the workflow, sends a few `join`
  signals, then queries for several groups.
- [query_only.py](query_only.py) — queries an already-running workflow from a
  separate process, the Python equivalent of the CLI call below.

### Rules for query handlers

A query handler must be **read-only**: no mutating workflow state, no scheduling
activities or timers, no blocking. Query results are not recorded in event
history, so any side effect would be silently lost on replay. Building and
returning a new list, as `members_for_group` does, is fine.

Queries can take multiple arguments, and are named independently of the method
(`@workflow.query(name="members")`) so the wire name stays stable if you rename
the Python method.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server):

```bash
temporal server start-dev
```

In one terminal, run the worker:

```bash
uv run python -m query_with_args.worker
```

In another, run the starter:

```bash
uv run python -m query_with_args.starter
```

Output:

```
Members of group-1: ['alice', 'carol']
Members of group-2: ['bob']
Members of group-3: []
```

The workflow keeps running, so you can query it again at any time:

```bash
uv run python -m query_with_args.query_only
```

## Same query from the CLI

```bash
temporal workflow query \
    --workflow-id="state-id-0" \
    --type="members" \
    --input='"group-1"'
```

`--type` is the query name, and `--input` is JSON — hence the quoted string
`'"group-1"'` for a single string argument. Pass repeated `--input` flags for a
handler taking multiple arguments.

The workflow never completes, so terminate it when you're done:

```bash
temporal workflow terminate --workflow-id="state-id-0"
```
