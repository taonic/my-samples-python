#!/usr/bin/env bash
# Starts the sample inside the Daytona sandbox: a Temporal dev server, the worker and the
# API. The Temporal UI is served under /temporal, so the Fly app can proxy it on the same
# origin as the sample's page. Whatever is already running is left alone; `start.sh app`
# restarts the worker and the API, after the runner uploaded new code.
#
# Each process is started with setsid -f: Daytona ends a command's process group when the
# command returns, which would take a plain `&` job with it.
set -euo pipefail
cd /app
mkdir -p .data .logs

if ! pgrep -f "[t]emporal server start-dev" >/dev/null; then
  setsid -f temporal server start-dev \
    --ui-ip 0.0.0.0 --ui-public-path /temporal \
    --db-filename .data/temporal.db --log-level warn \
    >.logs/temporal.log 2>&1 </dev/null
fi
for _ in $(seq 60); do
  temporal operator cluster health >/dev/null 2>&1 && break
  sleep 1
done

APP="[p]rogressive_data_resolution\.(worker|api)"
if [ "${1:-}" = app ]; then
  pkill -f "$APP" || true
  for _ in $(seq 50); do pgrep -f "$APP" >/dev/null || break; sleep 0.2; done
fi

if ! pgrep -f "[p]rogressive_data_resolution\.worker" >/dev/null; then
  setsid -f .venv/bin/python -m progressive_data_resolution.worker \
    >.logs/worker.log 2>&1 </dev/null
fi
if ! pgrep -f "[p]rogressive_data_resolution\.api" >/dev/null; then
  TEMPORAL_UI_URL=/temporal setsid -f .venv/bin/python -m uvicorn progressive_data_resolution.api:app \
    --host 0.0.0.0 --port 8000 \
    >.logs/api.log 2>&1 </dev/null
fi

for _ in $(seq 60); do
  if curl -sf -o /dev/null localhost:8000/ && curl -sf -o /dev/null localhost:8233/temporal/; then
    echo up
    exit 0
  fi
  sleep 1
done
echo "The sample didn't come up. The logs, in /app/.logs:" >&2
tail -n 20 .logs/*.log >&2
exit 1
