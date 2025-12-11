"""Tests for Helm integration."""

import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from time import sleep

import pytest
from pydantic import AnyUrl
from pyleak import no_thread_leaks

import pyk8sdev.watcher
from pyk8sdev import CachedK8sCluster
from pyk8sdev.config import ConfigFile
from pyk8sdev.config import LocalHelmChart
from pyk8sdev.config import RemoteHelmChart
from pyk8sdev.config import which
from pyk8sdev.helm import add_helm_repo
from pyk8sdev.k8s.utils import wait_for_created

pyk8sdev.watcher.TIMEOUT = 0.1


@pytest.fixture
def helm_chart(tmp_path: Path) -> LocalHelmChart:
    """Use Helm CLI to create a basic chart to test with."""
    subprocess.run(  # noqa: S603 no untrusted input
        [
            which("helm"),
            "create",
            "mychart",
        ],
        check=True,
        cwd=tmp_path,
    )
    shutil.copy(tmp_path / "mychart" / "values.yaml", tmp_path / "values.yaml")
    return LocalHelmChart(
        name="mychart",
        namespace="mychart",
        values_file=tmp_path / "values.yaml",
        directory=tmp_path / "mychart",
    )


def test_helm_bin_exists() -> None:
    """Ensure that Helm is in PATH."""
    assert which("helm") is not None


def test_oci_upstream() -> None:
    """Ensure we correctly handle OCI repositories."""
    chart = RemoteHelmChart(
        name="valkey-operator",
        namespace="valkey-operator",
        values_file=Path(),
        version=">=v0.0.1-chart",
        repository_url=AnyUrl("oci://ghcr.io/hyperspike/valkey-operator"),
    )
    assert chart.get_chart_ref() == "oci://ghcr.io/hyperspike/valkey-operator"
    assert chart.repository_name is None
    add_helm_repo(chart)  # Check that we don't raise any errors here


def test_local_helm_chart(helm_chart: LocalHelmChart) -> None:
    """Ensure that local Helm charts are handled correctly."""
    name = "test-local-helm-chart"
    with (
        no_thread_leaks(),
        CachedK8sCluster(
            ConfigFile(cluster_name=name, resources=[helm_chart]),
            watch=True,
        ) as k8s,
    ):
        assert helm_chart.values_file is not None
        wait_for_created(k8s.cluster, "mychart", kind="deployment", namespace="mychart")
        assert k8s.cluster.wait("deployment mychart", "condition=Available=True", namespace="mychart") is None
        initial_service: dict = k8s.cluster.kubectl(["get", "service", "--namespace", "mychart", "mychart"])
        assert initial_service["spec"]["ports"][0]["port"] == 80  # noqa: PLR2004 standard HTTP port from template
        # Change values, expect watch to reconcile
        helm_chart.values_file.write_text(helm_chart.values_file.read_text().replace("port: 80", "port: 8080"))
        sleep(pyk8sdev.watcher.TIMEOUT * 10)
        values_updated_service: dict = k8s.cluster.kubectl(["get", "service", "--namespace", "mychart", "mychart"])
        assert values_updated_service["spec"]["ports"][0]["port"] == 8080  # noqa: PLR2004 patched value from above
        # Change file, expect watch to reconcile
        service_file = helm_chart.directory / "templates" / "service.yaml"
        service_file.write_text(
            service_file.read_text().replace('name: {{ include "mychart.fullname" . }}', "name: new-service")
        )
        sleep(pyk8sdev.watcher.TIMEOUT * 10)
        file_updated_service: dict = k8s.cluster.kubectl(["get", "service", "--namespace", "mychart", "new-service"])
        assert file_updated_service["spec"]["ports"][0]["port"] == 8080  # noqa: PLR2004 patched value from above


def test_remote_helm_chart() -> None:
    """Ensure that remote Helm charts are handled correctly."""
    name = "test-remote-helm-chart"
    service_port = 9897  # default is 9898; validate that the values_override is parsed correctly
    with (
        no_thread_leaks(),
        CachedK8sCluster(
            ConfigFile(
                cluster_name=name,
                resources=[
                    RemoteHelmChart(
                        name="podinfo",
                        repository_url=AnyUrl("https://stefanprodan.github.io/podinfo"),
                        repository_name="podinfo",
                        values_override=dedent(f"""\
                        ---
                        service:
                          externalPort: {service_port}
                        """),
                    ),
                    RemoteHelmChart(
                        name="podinfo",
                        namespace="podinfo",
                        repository_url=AnyUrl("https://stefanprodan.github.io/podinfo"),
                        repository_name="podinfo",
                        version="6.8.0",
                    ),
                ],
            ),
        ) as k8s,
    ):
        service1: dict = k8s.cluster.kubectl(["get", "service", "podinfo"])
        assert service1["spec"]["ports"][0]["port"] == service_port
        service2: dict = k8s.cluster.kubectl(["get", "service", "podinfo", "--namespace", "podinfo"])
        assert service2["spec"]["ports"][0]["port"] == 9898  # noqa: PLR2004 taken from Helm chard
