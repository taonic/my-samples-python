# Parallel Activities with a Thread Pool

Runs multiple activities in parallel, where the activities are **synchronous**
(plain `def`) functions executed in a `ThreadPoolExecutor` rather than `async def`
activities on the asyncio event loop.

Use synchronous, thread-pool activities when your work blocks the thread — for
example, blocking database drivers or HTTP clients that have no async API.

## How it works

- [activities.py](activities.py) — synchronous `@activity.defn def fetch_item(...)`.
  Each invocation blocks its own thread.
- [worker.py](worker.py) — passes a `ThreadPoolExecutor` as the worker's
  `activity_executor`. This is **required** for synchronous activities and bounds
  how many run concurrently (`max_workers`).
- [workflow.py](workflow.py) — dispatches one activity per item and awaits them
  with `asyncio.gather`. The `gather` only coordinates the activity *handles*;
  the actual parallel execution happens in the worker's thread pool.

## Run

Start a local Temporal server (e.g. `temporal server start-dev`), then from the
repo root:

```bash
# Terminal 1 — worker
uv run python -m parallel_activity_threadpool.worker

# Terminal 2 — starter
uv run python -m parallel_activity_threadpool.starter
```

Ten activities each sleep ~1s. With `max_workers=10` they finish in roughly one
second total instead of ten, demonstrating real thread-level parallelism.
