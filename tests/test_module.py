"""Tests for k8s components, other than Talos."""

import pytest
from pyleak import no_thread_leaks
from python_on_whales import docker

from pyk8sdev import CachedK8sCluster
from pyk8sdev.config import ConfigFile


def _check_cluster_working(k8s: CachedK8sCluster) -> None:
    assert k8s.cluster is not None
    nodes = k8s.cluster.kubectl(["get", "nodes"])
    assert len(nodes.get("items", [])) > 0
    pods = k8s.cluster.kubectl(["get", "pods", "--all-namespaces=true"])
    assert len(pods.get("items", [])) > 0


def test_init() -> None:
    """Ensure class initialises."""
    k8s = CachedK8sCluster(
        ConfigFile(),
    )
    assert not k8s.watch
    assert k8s.config.cluster_name == "test"


def test_context_manager() -> None:
    """Ensure a cached cluster is created when used as a context manager."""
    with no_thread_leaks(), CachedK8sCluster(ConfigFile(cluster_name="test-context-manager")) as k8s:
        _check_cluster_working(k8s)
        cluster = k8s.cluster
    with pytest.raises(RuntimeError, match="The kubeconfig is not set"):
        cluster.kubectl(["version"])
    for container in docker.container.list():  # pragma: no cover
        assert not container.name.startswith(f"{cluster.cluster_name}-proxy-")
        assert container.name != f"{cluster.cluster_name}-local-registry"


def test_fixture(cached_k8s_cluster: CachedK8sCluster) -> None:
    """Ensure we can get a working cluster using the pytest fixture.

    This must be the only fixture use in the tests otherwise multiple containers will fight for the same ports!
    """
    _check_cluster_working(cached_k8s_cluster)
