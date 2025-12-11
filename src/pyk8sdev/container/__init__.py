"""Handling different container runtimes and build tools."""

from logging import getLogger
from typing import TYPE_CHECKING

import httpx
from pathspec import PathSpec

if TYPE_CHECKING:
    from pathlib import Path

    from python_on_whales import DockerClient
    from python_on_whales import Image as DockerImage

logger = getLogger(__name__)


def build_container(
    docker_client: DockerClient,
    name: str,
    tag: str,
    containerfile: Path,
    directory: Path,
) -> DockerImage:
    """Build the container using docker CLI."""
    return docker_client.build(
        context_path=directory,
        file=containerfile,
        tags=f"localhost:5000/{name}:{tag}",
        load=True,
        progress=False,
        stream_logs=False,
    )


def ensure_container(
    docker_client: DockerClient,
    name: str,
    tag: str,
    containerfile: Path,
    directory: Path,
) -> DockerImage:
    """Ensure the container is built and pushed."""
    image = build_container(docker_client, name, tag, containerfile, directory)
    for t in image.repo_tags:
        while True:
            req = httpx.get(
                f"http://localhost:5000/v2/{name}/manifests/{t.split(':')[-1]}",
                headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            )
            if req.status_code == 200 and req.json()["config"]["digest"] == image.id:  # noqa: PLR2004 standard HTTP code
                break
            docker_client.push(t, quiet=True)
    return image


def does_file_effect_container(path: Path, *, directory: Path) -> bool:
    """Ignore files that are excluded by the container build process."""
    # If it isn't in the build directory, it isn't included, so ignore it
    if not path.is_relative_to(directory):
        return False

    # Check all parent directories for .ignore files that may exclude the file
    parent = path.parent
    while parent.is_relative_to(directory):
        for ignore_file in (".containerignore", ".dockerignore"):
            if not (parent / ignore_file).exists():
                continue
            ignored = PathSpec.from_lines("gitignore", (parent / ignore_file).read_text().splitlines())
            if ignored.match_file(path.relative_to(parent)):
                return False

        parent = parent.parent

    # Otherwise it's going to require a rebuild
    return True
