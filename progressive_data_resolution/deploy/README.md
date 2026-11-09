# Deploying the sample

One [Fly.io](https://fly.io) app serves the sample. The sample itself runs in one
[Daytona](https://daytona.io) sandbox: a Temporal dev server, the worker and the API.
Fly only proxies to it, so its machine stays small.

```
browser ──► Fly app (runner.py) ──signed preview URLs──► Daytona sandbox
             /temporal/...  ───────────────────────────►  :8233  temporal server start-dev (UI under /temporal)
             everything else ──────────────────────────►  :8000  api.py ──► :7233 ◄── worker.py
```

- [runner.py](runner.py) is the Fly app. On a request it finds the sandbox by its
  label (`app=progressive-data-resolution`), and creates or starts it if needed. If
  the sample's code changed since the sandbox last got it, it uploads the code and
  restarts the worker and the API. Then it forwards the request. While the sandbox
  is coming up, pages get a "starting" page that reloads itself and API calls get
  a 503. The runner keeps no state that matters, so a restart finds the same
  sandbox.
- [start.sh](start.sh) starts the processes inside the sandbox. It leaves running
  processes alone. Temporal persists to `/app/.data/temporal.db`, so workflows
  survive a sandbox stop and start.
- [sandbox.Dockerfile](sandbox.Dockerfile) is the sandbox's image: Python, the
  sample's dependencies and the Temporal CLI. [snapshot.py](snapshot.py) builds it
  into a Daytona snapshot.
- [files.py](files.py) lists what goes into the sandbox. It names the snapshot
  after a hash of the snapshot's inputs (`sandbox.Dockerfile`, `pyproject.toml`,
  `uv.lock`), and the code after a hash of the sample's files.
- [Dockerfile](Dockerfile), [.dockerignore](.dockerignore) and
  [fly.toml](fly.toml) describe the Fly app. The build context is the repo root:
  the image carries the sample's files, so the runner can upload them.

The Temporal UI is served under `/temporal` on the same origin
(`--ui-public-path`), so it needs no port or proxy of its own. The page's workflow
links point at it: the API's `/config.json` returns `TEMPORAL_UI_URL`, which
start.sh sets to `/temporal`.

## What it needs

| Secret | What it's for |
|---|---|
| `DAYTONA_API_KEY` | finding, creating, starting and deleting the sandbox. Get one from [Daytona's dashboard](https://app.daytona.io/dashboard/keys) |

Settings in [fly.toml](fly.toml) (`[env]`): `SANDBOX_AUTO_STOP_MINUTES` (30). The
sandbox stops after this many idle minutes. Requests through the proxy count as
activity, so it stays up while anyone uses the sample, and the next visit after a
stop starts it again.

The sandbox is 1 vCPU, 2 GiB and 3 GiB of disk (`RESOURCES` in snapshot.py). It
needs no outbound network once it's running.

## First deploy

From the repo root:

```bash
# 1. The sandbox snapshot. Rebuild it whenever its name changes, after any
#    change to sandbox.Dockerfile, pyproject.toml or uv.lock.
uv run --group progressive-data-resolution-deploy \
  python -m progressive_data_resolution.deploy.snapshot

# 2. The app and its secret. The app name must be unique on Fly: change `app` in
#    fly.toml if it's taken.
fly apps create progressive-data-resolution
fly secrets set --app progressive-data-resolution DAYTONA_API_KEY=...

# 3. Deploy
fly deploy . --config progressive_data_resolution/deploy/fly.toml \
  --dockerfile progressive_data_resolution/deploy/Dockerfile \
  --ignorefile progressive_data_resolution/deploy/.dockerignore --ha=false
```

Open `https://progressive-data-resolution.fly.dev`. The first visit creates the
sandbox, which takes about a minute. `GET /healthz` says whether the runner sees
the sandbox as ready, without starting it.

Keep the app to **one machine** (`--ha=false`; run `fly scale count 1` if a second
appears). Two machines could each create a sandbox at the same moment.

## Later deploys

- **Code changes** (the sample's Python, schemas, page, start.sh) need only step 3.
  On the next request the runner sees the new code version, uploads the files and
  restarts the worker and the API. Temporal and its workflows stay. A running
  workflow whose code changed incompatibly fails with a non-determinism error. It
  is a demo with no versioning, so start a new application.
- **Dependency changes** (pyproject.toml, uv.lock, sandbox.Dockerfile) need steps
  1 and 3. The runner finds a sandbox built from another snapshot, deletes it with
  its workflows, and creates a new one.

## Trying it without Fly

```bash
# The sandbox image, with local Docker
uv run --group progressive-data-resolution-deploy \
  python -m progressive_data_resolution.deploy.snapshot --local

# The runner, on http://localhost:8080, against a real Daytona sandbox
DAYTONA_API_KEY=... uv run --group progressive-data-resolution-deploy \
  python -m progressive_data_resolution.deploy.runner
```

## Caveats

- **Anyone with the URL can use it.** The sample has no authentication, and every
  visitor shares one sandbox and one Temporal namespace. Workflow ids are random,
  so visitors don't run into each other, but each can see the others' workflows in
  the Temporal UI.
- **The dev server is not production Temporal.** It is one process with a SQLite
  file. For anything beyond a demo, point the worker and the API at Temporal Cloud
  and run them on Fly directly, without the sandbox.
- **To clean up**, delete the sandbox from Daytona's dashboard (label
  `app=progressive-data-resolution`) and run `fly apps destroy progressive-data-resolution`.
