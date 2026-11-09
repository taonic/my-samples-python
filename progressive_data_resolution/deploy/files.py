"""What goes into the sandbox, and the names that say which version of it is running.

The snapshot bakes in the slow part, the dependencies and the Temporal CLI. The
sample's own code is uploaded into the sandbox by the runner, so a code change
needs only a deploy, not a new snapshot.
"""

import hashlib
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
PACKAGE = DEPLOY.parent
REPO = PACKAGE.parent

# Where the repo's files land in the sandbox. sandbox.Dockerfile installs the
# dependencies here, and start.sh runs from here.
SANDBOX_ROOT = "/app"

# A change to any of these needs a new snapshot: they're its name.
SNAPSHOT_INPUTS = [DEPLOY / "sandbox.Dockerfile", REPO / "pyproject.toml", REPO / "uv.lock"]

# Left out of the upload: the sandbox runs the sample, not its tests or docs.
_SKIP = {"__pycache__", "tests", "doc", "deploy"}


def code_files() -> list[Path]:
    """The sample's files the sandbox runs: the package, and start.sh to run it."""
    files = [
        path
        for path in sorted(PACKAGE.rglob("*"))
        if path.is_file()
        and not _SKIP.intersection(path.relative_to(PACKAGE).parts)
        and not path.name.startswith(".")
    ]
    return files + [DEPLOY / "start.sh"]


def sandbox_path(path: Path) -> str:
    return f"{SANDBOX_ROOT}/{path.relative_to(REPO).as_posix()}"


def _digest(paths: list[Path]) -> str:
    sha = hashlib.sha256()
    for path in paths:
        sha.update(path.relative_to(REPO).as_posix().encode())
        sha.update(b"\0")
        sha.update(path.read_bytes())
        sha.update(b"\0")
    return sha.hexdigest()[:12]


def snapshot_name() -> str:
    return f"progressive-data-resolution-{_digest(SNAPSHOT_INPUTS)}"


def code_version() -> str:
    return _digest(code_files())
