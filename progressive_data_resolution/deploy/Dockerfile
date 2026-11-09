# The Fly app: the runner (runner.py), which proxies to the sample running in a Daytona
# sandbox. Build it from the repo root: the image carries the sample's files, which the
# runner uploads into the sandbox (deploy/.dockerignore keeps the rest out).
#
#   fly deploy . --config progressive_data_resolution/deploy/fly.toml \
#     --dockerfile progressive_data_resolution/deploy/Dockerfile \
#     --ignorefile progressive_data_resolution/deploy/.dockerignore --ha=false
FROM python:3.13-slim-bookworm

RUN pip install --no-cache-dir uv==0.13.0
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /repo
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --only-group progressive-data-resolution-deploy
COPY progressive_data_resolution progressive_data_resolution

USER nobody
EXPOSE 8080
CMD ["/opt/venv/bin/python", "-m", "progressive_data_resolution.deploy.runner"]
