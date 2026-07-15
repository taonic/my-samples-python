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

## Resources

- [Temporal Documentation](https://docs.temporal.io/docs/python)
- [Python SDK Repository](https://github.com/temporalio/sdk-python)
- [Temporal Samples](https://github.com/temporalio/samples-python)

## License

MIT
