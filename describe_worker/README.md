# DescribeWorker (Worker Heartbeat)

Uses the **DescribeWorker** API to fetch live details about a running worker —
its identity, SDK, host, task-slot usage, and sticky-cache stats.

## What's available in the Python SDK (temporalio 1.20.0)

There is **no** high-level `client.describe_worker(...)` helper. `DescribeWorker`
and its companion `ListWorkers` exist only as **raw gRPC methods** on
`client.workflow_service`, called with protobuf request/response types:

- `client.workflow_service.list_workers(ListWorkersRequest(...))`
- `client.workflow_service.describe_worker(DescribeWorkerRequest(...))`

`DescribeWorkerRequest` needs a `worker_instance_key`, which you don't get from
the high-level `Worker` object — so the flow is:

1. **ListWorkers** → discover running workers and their `worker_instance_key`
2. **DescribeWorker(worker_instance_key)** → full detail for one worker

## Worker heartbeat is what makes this work

A worker is only discoverable/describable while it is **heartbeating**. In the
Python SDK heartbeat is configured at the **`Runtime`** level (not on `Worker`)
and is **ON by default at a 60s interval**:

```python
Runtime(
    telemetry=TelemetryConfig(),
    worker_heartbeat_interval=timedelta(seconds=3),  # 1s–60s; None disables it
)
```

The client must be created with that runtime (`Client.connect(..., runtime=...)`)
so the worker inherits it. This sample uses a 3s interval so the worker appears
quickly.

## Run

Start a [dev server](https://docs.temporal.io/cli#start-dev-server) (needs a
recent version — worker heartbeat requires Server ≥ ~1.28; older servers return
`UNIMPLEMENTED`):

```bash
temporal server start-dev
```

Then run the single self-contained script — it starts a heartbeating worker,
waits for it to appear, and describes it, all in one process:

```bash
uv run python describe_worker/main.py
```

The worker registers a trivial no-op activity (a worker must register at least
one activity/workflow). Example output:

```
=== DescribeWorker: cbd10e87-... ===
  identity:    describe-worker-sample-worker
  task_queue:  describe-worker-sample
  sdk:         temporal-python 1.20.0
  host:        MacBook-Pro.local (pid 42133)
  wf slots:    0 used / 0 available
  act slots:   0 used / 100 available
```

## Against Temporal Cloud

The same script works against Temporal Cloud — it reads connection settings from
environment variables (falling back to the local dev server). The key difference
is that TLS is required and the namespace is the **fully-qualified** name (e.g.
`my-ns.a1b2c`), which must be used for both the worker *and* the
ListWorkers/DescribeWorker requests.

### API key auth (simplest)

```bash
export TEMPORAL_ADDRESS="my-ns.a1b2c.tmprl.cloud:7233"   # your namespace gRPC endpoint
export TEMPORAL_NAMESPACE="my-ns.a1b2c"                  # fully-qualified namespace
export TEMPORAL_API_KEY="$(cat ~/temporal-cloud-api.key)"

uv run python describe_worker/main.py
```

The script enables TLS automatically whenever `TEMPORAL_API_KEY` is set
(`Client.connect(..., api_key=..., tls=True)`).

### mTLS (client certificate) auth

If you use client certificates instead of an API key, connect with a
`TLSConfig` carrying the cert/key. Swap the `connect()` helper's Cloud branch
for:

```python
from temporalio.service import TLSConfig

return await Client.connect(
    ADDRESS,
    namespace=NAMESPACE,
    tls=TLSConfig(
        client_cert=open(os.environ["TEMPORAL_TLS_CERT"], "rb").read(),
        client_private_key=open(os.environ["TEMPORAL_TLS_KEY"], "rb").read(),
    ),
    runtime=runtime,
)
```

### Cloud caveats

- **Namespace must match.** Passing the short name (`my-ns`) instead of
  `my-ns.a1b2c` will connect but return no workers.
- **Feature availability.** Worker heartbeat / DescribeWorker must be enabled for
  your Cloud namespace; if it isn't, the RPC returns `UNIMPLEMENTED` or
  `PermissionDenied`. Check with your Temporal Cloud account team if so.
- The worker and the describe calls in this sample share one client, so they
  automatically use the same namespace and credentials.

## Notes

- **No worker found?** Confirm the worker is running, heartbeat isn't disabled
  (`worker_heartbeat_interval=None`), and your server supports worker heartbeat.
  Freshly started workers take a heartbeat interval or two to appear.
- **`ListWorkersRequest.query`** uses the same list-filter grammar as visibility
  (e.g. `TaskQueue = '...'`). Pass `query=""` to list every worker.
- The response is the raw `WorkerHeartbeat` protobuf — it also carries poller
  info, per-slot-type processed/failed task counts, deployment version, and host
  CPU/mem usage if you want to surface more.
