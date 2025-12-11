"""Utils for managing Helm releases."""

import subprocess
from logging import getLogger
from typing import TYPE_CHECKING

from pyk8sdev.config import RemoteHelmChart
from pyk8sdev.config import which

if TYPE_CHECKING:
    from pathlib import Path

    from pyk8sdev.config import HelmChart

logger = getLogger(__name__)


def add_helm_repo(chart: RemoteHelmChart) -> None:
    """Ensure that the Helm repository is installed and up to date."""
    if chart.repository_name is None:
        return
    subprocess.run(  # noqa: S603 untrusted input restricted to loaded config
        [
            which("helm"),
            "repo",
            "add",
            chart.repository_name,
            str(chart.repository_url),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603 untrusted input restricted to loaded config
        [
            which("helm"),
            "repo",
            "update",
            chart.repository_name,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def helm_upgrade(chart: HelmChart, kubeconfig: Path) -> None:
    """Run helm upgrade."""
    extra_args = []
    if isinstance(chart, RemoteHelmChart) and chart.version is not None:
        extra_args.extend(["--version", chart.version])
    if chart.values_file is not None:
        extra_args.extend(["--values", str(chart.values_file)])
    if chart.values_override:
        extra_args.extend(["--values", "-"])
    subprocess.run(  # noqa: S603 untrusted input restricted to loaded config
        [
            which("helm"),
            "upgrade",
            chart.name,
            chart.get_chart_ref(),
            "--install",
            "--output",
            "json",
            "--create-namespace",
            "--namespace",
            chart.namespace,
            "--kubeconfig",
            str(kubeconfig),
            *extra_args,
        ],
        check=True,
        # Only make stdin a pipe if we have input to feed it
        input=chart.values_override or None,
        capture_output=True,
        text=True,
    )


def ensure_helm_released(chart: HelmChart, kubeconfig: Path) -> None:
    """Ensure a Helm chart is installed and updated."""
    if isinstance(chart, RemoteHelmChart):
        add_helm_repo(chart)
    helm_upgrade(chart, kubeconfig)


def does_file_affect_helm(path: Path, *, directory: Path, values_file: Path | None) -> bool:
    """Ignore files that are not part of a Helm chart."""
    # If it isn't in the chart directory, it isn't included, so ignore it
    # If the values file was updated, then we want to update
    return path.is_relative_to(directory) or (values_file is not None and path == values_file)
