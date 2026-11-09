"""Builds the Daytona snapshot the sample's sandbox is created from.

    uv run --group progressive-data-resolution-deploy \
        python -m progressive_data_resolution.deploy.snapshot            # build it on Daytona
    ... python -m progressive_data_resolution.deploy.snapshot --local    # build it with local Docker
    ... python -m progressive_data_resolution.deploy.snapshot --name     # print its name

The name carries a hash of the snapshot's inputs (see files.py), so a deploy whose
dependencies changed asks for a snapshot that doesn't exist yet, and the runner says
so, instead of quietly running the sample on stale dependencies.
"""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from daytona import (
    CreateSnapshotParams,
    Daytona,
    DaytonaError,
    DaytonaNotFoundError,
    Image,
    Resources,
)

from .files import DEPLOY, REPO, snapshot_name

# A Temporal dev server, a worker and the API: small.
RESOURCES = Resources(cpu=1, memory=2, disk=3)


def stage_context() -> Path:
    context = Path(tempfile.mkdtemp(prefix="pdr-snapshot-"))
    shutil.copy(DEPLOY / "sandbox.Dockerfile", context / "Dockerfile")
    shutil.copy(REPO / "pyproject.toml", context / "pyproject.toml")
    shutil.copy(REPO / "uv.lock", context / "uv.lock")
    return context


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", action="store_true", help="print the snapshot's name and exit")
    parser.add_argument(
        "--local", action="store_true", help="build the image with local Docker instead"
    )
    parser.add_argument(
        "--force", action="store_true", help="rebuild a snapshot that already exists"
    )
    args = parser.parse_args()

    name = snapshot_name()
    if args.name:
        print(name)
        return

    context = stage_context()
    print(f"Staged the build context in {context}")
    if args.local:
        subprocess.run(["docker", "build", "-t", name, str(context)], check=True)
        print(f"\n✓ Built {name} locally. Try it: docker run --rm -it --entrypoint bash {name}")
        return

    daytona = Daytona()
    try:
        existing = daytona.snapshot.get(name)
    except DaytonaError as err:
        if not isinstance(err, DaytonaNotFoundError) and "not found" not in str(err).lower():
            raise
        existing = None
    if existing is not None:
        if not args.force:
            print(f"✓ Snapshot {name} already exists ({existing.state}). --force rebuilds it.")
            return
        print(f"Deleting the existing {name}...")
        daytona.snapshot.delete(existing)

    print(f"Building snapshot {name} on Daytona...")
    daytona.snapshot.create(
        CreateSnapshotParams(
            name=name,
            image=Image.from_dockerfile(context / "Dockerfile"),
            resources=RESOURCES,
            entrypoint=["sleep", "infinity"],
        ),
        on_logs=lambda chunk: print(chunk, end="" if chunk.endswith("\n") else "\n"),
        timeout=0,
    )
    print(f"\n✓ Snapshot {name} is ready.")


if __name__ == "__main__":
    main()
