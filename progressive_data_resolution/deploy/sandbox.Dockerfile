# The Daytona sandbox: Python, the sample's dependencies and the Temporal CLI (for its dev
# server), baked in once. Built by snapshot.py from a staged context holding this file,
# pyproject.toml and uv.lock. The runner uploads the sample's code when it creates the
# sandbox, so only a dependency change (those three files) needs a new snapshot.
FROM python:3.13-slim-bookworm

ARG TEMPORAL_CLI_VERSION=1.8.3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl procps \
 && rm -rf /var/lib/apt/lists/* \
 && arch=$(uname -m | sed "s/x86_64/amd64/;s/aarch64/arm64/") \
 && curl -fsSL "https://temporal.download/cli/archive/v${TEMPORAL_CLI_VERSION}?platform=linux&arch=${arch}" -o /tmp/t.tgz \
 && tar -xzf /tmp/t.tgz -C /usr/local/bin temporal \
 && rm /tmp/t.tgz \
 && pip install --no-cache-dir uv==0.13.0

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group progressive-data-resolution \
 && mkdir -p .data .logs \
 && chmod -R a+rwX /app

ENTRYPOINT ["sleep", "infinity"]
