# Temporal Python SDK Samples

Python samples for [Temporal.io](https://temporal.io) workflows and activities.

## Prerequisites

- Python 3.10 or higher
- [uv](https://github.com/astral-sh/uv) package manager

## Installation

```bash
uv sync
```

## Samples

### Cloud Operations
Demonstrates using the Temporal Cloud Operations API to manage namespaces.

See [cloud-ops/README.md](cloud-ops/README.md) for details.

### Prometheus
Shows how to expose SDK metrics via HTTP for Prometheus scraping.

See [prometheus/README.md](prometheus/README.md) for details.

### Parallel Activities with a Thread Pool
Runs activities in parallel using synchronous (`def`) activities executed in a
`ThreadPoolExecutor`, instead of `async def` activities on the event loop.

See [parallel_activity_threadpool/README.md](parallel_activity_threadpool/README.md) for details.

### Visibility Query Dedup (Wildcard Workflow ID Match)
Uses the Visibility API to find existing workflows via a wildcard (prefix) match
on part of the Workflow ID, then decides whether to reject the request or start a
new workflow.

See [visibility_query_dedup/README.md](visibility_query_dedup/README.md) for details.

### Query with Arguments
Demonstrates a query handler that takes input parameters: signals build up group
membership state and callers query for the members of a specific group.

See [query_with_args/README.md](query_with_args/README.md) for details.

### Signals
Covers signal-with-start, client signals, and workflow-to-workflow signals via an
external workflow handle, driving a group workflow that fills up or times out.

See [signal_workflow/README.md](signal_workflow/README.md) for details.

### Workflow Sleep (Durable Timers)
A reminder loop that waits between attempts with `workflow.sleep`, contrasted
with a timed `wait_condition` that wakes early when an acknowledgement signal
arrives.

See [workflow_sleep/README.md](workflow_sleep/README.md) for details.

### DescribeWorker (Worker Heartbeat)
Uses the raw `ListWorkers` / `DescribeWorker` gRPC APIs to discover a running
worker and fetch its live details (identity, host, slot usage, cache stats),
relying on worker heartbeat.

See [describe_worker/README.md](describe_worker/README.md) for details.

### Workflow Versioning and Replay
A minimal workflow (one activity) plus a replayer that checks new workflow code
against existing histories. Includes a walkthrough of breaking determinism and
then fixing it with `workflow.patched`.

See [workflow_versioning/README.md](workflow_versioning/README.md) for details.

### Error Handling
A hello-world greeting activity that fails on purpose, showing retryable vs
non-retryable `ApplicationError`, retry policies, unwrapping `err.cause` in the
workflow and at the client, and choosing between failing and degrading.

See [error_handling/README.md](error_handling/README.md) for details.

### Progressive Data Resolution (Declarative Reconciliation)
A long-running workflow whose path is decided by a versioned JSON schema and the
data collected so far, not by a hard-coded sequence of steps. Collects human input
through Updates, runs automated checks as activities once their inputs are ready,
handles corrections and stale results, and ships with a dynamic web form and debug
panel. *An experiment, not a recommended pattern.*

See [progressive_data_resolution/README.md](progressive_data_resolution/README.md) for details.

## Resources

- [Temporal Documentation](https://docs.temporal.io/docs/python)
- [Python SDK Repository](https://github.com/temporalio/sdk-python)
- [Temporal Samples](https://github.com/temporalio/samples-python)

## License

MIT
