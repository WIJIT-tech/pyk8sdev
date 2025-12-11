"""Tests for custom Talos Kubernetes provider."""

import pytest
from pytest_kubernetes.options import ClusterOptions

from pyk8sdev.exceptions import TooOldError
from pyk8sdev.exceptions import UnsupportedError
from pyk8sdev.k8s.talos import TalosManagerBase


def test_talos() -> None:
    """Ensure that cluster can be created and destroyed."""
    cluster = TalosManagerBase()
    with pytest.raises(TooOldError, match="You must specify a newer api_version for Talos to work"):
        cluster.create()
    try:
        cluster.create(cluster_options=ClusterOptions(api_version="1.34.1"))
        proc = cluster._exec(["cluster", "show", "--name", cluster.cluster_name])
        assert proc.returncode == 0, proc.stderr.decode()
        assert f"{cluster.cluster_name}-controlplane-1" in proc.stdout.decode()
        nodes = cluster.kubectl(["get", "nodes"])
        assert len(nodes.get("items", [])) > 0
        pods = cluster.kubectl(["get", "pods", "--all-namespaces=true"])
        assert len(pods.get("items", [])) > 0
        with pytest.raises(UnsupportedError, match="load_image not supported for talos clusters"):
            cluster.load_image("foo")
    finally:
        cluster.delete()
        proc = cluster._exec(["cluster", "show", "--name", cluster.cluster_name])
        assert proc.returncode == 0, proc.stderr.decode()
        assert f"{cluster.cluster_name}-controlplane-1" not in proc.stdout.decode()


# TODO(MR): test with provider_config included
# https://github.com/WIJIT-tech/pyk8sdev/issues/2
