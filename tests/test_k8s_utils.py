"""Tests for k8s utils."""

import datetime

import pytest
from pyleak import no_thread_leaks

from pyk8sdev import CachedK8sCluster
from pyk8sdev.config import ConfigFile
from pyk8sdev.exceptions import ApplyResourceTimedOutError
from pyk8sdev.k8s.utils import wait_for_created


def test_wait_for_created() -> None:
    """Ensure wait_for_created is fully exercised."""
    name = "test-wait-for-created"
    with no_thread_leaks(), CachedK8sCluster(ConfigFile(cluster_name=name)) as cached_k8s_cluster:
        # True positive (list)
        wait_for_created(cached_k8s_cluster.cluster, "-l k8s-app=kube-dns", namespace="kube-system")

        # True positive (single)
        wait_for_created(cached_k8s_cluster.cluster, "kube-dns", kind="service", namespace="kube-system")

        # True negative
        timeout = 3
        start_time = datetime.datetime.now(tz=datetime.UTC)
        with pytest.raises(ApplyResourceTimedOutError):
            wait_for_created(cached_k8s_cluster.cluster, "pod", timeout=timeout)
        assert datetime.datetime.now(tz=datetime.UTC) - start_time < datetime.timedelta(seconds=timeout + 1)
