"""Tests for container integration."""

import datetime
from textwrap import dedent
from time import sleep
from typing import TYPE_CHECKING

import pytest
from pyleak import no_thread_leaks
from python_on_whales import docker

from pyk8sdev import CachedK8sCluster
from pyk8sdev.config import ConfigFile
from pyk8sdev.config import Container
from pyk8sdev.config import LocalManifest
from pyk8sdev.config import Providers
from pyk8sdev.container import build_container
from pyk8sdev.container import does_file_effect_container
from pyk8sdev.container import ensure_container
from pyk8sdev.k8s.utils import wait_for_created

if TYPE_CHECKING:
    from pathlib import Path


def _test_container_init(tmp_path: Path, image_tag: str) -> None:
    """Create files for a small test container."""
    (tmp_path / "container").mkdir()
    (tmp_path / "container" / "Containerfile").write_text(
        dedent("""\
                FROM busybox:uclibc
                COPY test.sh /test.sh
                ENTRYPOINT ["sh", "-c", "/test.sh"]
            """)
    )
    (tmp_path / "container" / "test.sh").write_text(
        dedent(f"""\
                #!/bin/sh
                echo "{image_tag}"
            """)
    )
    (tmp_path / "container" / "test.sh").chmod(0o755)


def test_container_build(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Ensure that a container can be built."""
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
    _test_container_init(tmp_path, now)
    pre_output = capsys.readouterr()
    assert pre_output.out == ""
    assert pre_output.err == ""
    build_container(
        docker_client=docker,
        name="pyk8sdev-test-container-build",
        tag=now,
        containerfile=tmp_path / "container" / "Containerfile",
        directory=tmp_path / "container",
    )
    post_output = capsys.readouterr()
    assert post_output.out == ""
    assert post_output.err == ""
    images = docker.image.list(repository_or_tag=f"localhost:5000/pyk8sdev-test-container-build:{now}")
    assert len(images) == 1
    assert f"localhost:5000/pyk8sdev-test-container-build:{now}" in images[0].repo_tags
    output = docker.container.run(
        image=images[0].id,
        remove=True,
    )
    assert output.splitlines()[0] == now
    for image in docker.image.list(repository_or_tag="localhost:5000/pyk8sdev-test-container-build"):
        for tag in image.repo_tags:
            docker.image.remove(tag)
    docker.image.prune()


def test_file_filtering(tmp_path: Path) -> None:
    """Test that files are filtered correctly."""
    (tmp_path / "container").mkdir()
    (tmp_path / "container" / ".dockerignore").write_text(".cache\n.git")
    (tmp_path / "container" / ".cache").mkdir()
    (tmp_path / "container" / ".cache" / "should_be_ignored").write_text("should_be_ignored")
    (tmp_path / "container" / "data").mkdir()
    (tmp_path / "container" / "data" / "file1").write_text("file1")
    (tmp_path / "container" / "data" / "file2").write_text("file2")
    (tmp_path / "container" / "data" / "file3").write_text("file3")
    (tmp_path / "container" / "data" / ".cache").mkdir()
    (tmp_path / "container" / "data" / ".cache" / "should_be_ignored").write_text("should_be_ignored")
    (tmp_path / "container" / "data" / ".containerignore").write_text("file3\n")
    (tmp_path / "not_container").mkdir()
    (tmp_path / "not_container" / "data").mkdir()
    (tmp_path / "not_container" / "data" / "file1").write_text("file1")

    assert does_file_effect_container(
        tmp_path / "container" / "data" / "file1",
        directory=tmp_path / "container",
    )
    assert does_file_effect_container(
        tmp_path / "container" / "data" / "file2",
        directory=tmp_path / "container",
    )
    assert not does_file_effect_container(
        tmp_path / "container" / "data" / "file3",
        directory=tmp_path / "container",
    )
    assert not does_file_effect_container(
        tmp_path / "container" / ".cache" / "should_be_ignored",
        directory=tmp_path / "container",
    )
    assert not does_file_effect_container(
        tmp_path / "container" / "data" / ".cache" / "should_be_ignored",
        directory=tmp_path / "container",
    )
    assert does_file_effect_container(
        tmp_path / "container" / ".dockerignore",
        directory=tmp_path / "container",
    )
    assert does_file_effect_container(
        tmp_path / "container" / "data" / ".containerignore",
        directory=tmp_path / "container",
    )
    assert not does_file_effect_container(
        tmp_path / "not_container" / "data" / "file1",
        directory=tmp_path / "container",
    )


@pytest.mark.parametrize("provider", ["kind", "talosctl"])
def test_image_deployment(tmp_path: Path, provider: Providers) -> None:
    """Ensure that an image can be deployed in the cluster."""
    name = "test-image-deployment"
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
    _test_container_init(tmp_path, now)
    (tmp_path / "pod.yaml").write_text(
        dedent(f"""\
        ---
        apiVersion: v1
        kind: Pod
        metadata:
          name: {name}
        spec:
          containers:
            - name: {name}
              image: localhost:5000/pyk8sdev-{name}:{now}
              command:
                - sleep
                - infinity
        """)
    )
    with no_thread_leaks(), CachedK8sCluster(ConfigFile(cluster_name=name, provider=provider)) as k8s:
        assert k8s.cluster is not None
        ensure_container(
            docker,
            f"pyk8sdev-{name}",
            now,
            tmp_path / "container" / "Containerfile",
            tmp_path / "container",
        )
        k8s.cluster.apply(tmp_path / "pod.yaml")
        wait_for_created(k8s.cluster, name)
        k8s.cluster.wait(f"pod/{name}", "condition=ready")
        result = k8s.cluster.kubectl(["exec", "-it", name, "--", "sh", "-c", "/test.sh"], as_dict=False)
        assert result.strip() == now.strip()


def test_image_reload(tmp_path: Path) -> None:
    """Ensure that an updated image is reloaded."""
    name = "test-image-reload"
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
    _test_container_init(tmp_path, now)
    (tmp_path / "deployment.yaml").write_text(
        dedent(f"""\
            ---
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {name}
              labels:
                app: {name}
            spec:
              replicas: 1
              selector:
                matchLabels:
                  app: {name}
              strategy:
                type: Recreate
              template:
                metadata:
                  labels:
                    app: {name}
                spec:
                  containers:
                    - name: {name}
                      image: localhost:5000/pyk8sdev-{name}:{now}
                      imagePullPolicy: Always
                      command:
                        - sleep
                        - infinity
            """)
    )
    with (
        no_thread_leaks(),
        CachedK8sCluster(
            ConfigFile(
                cluster_name=name,
                containers=[
                    Container(
                        name=f"pyk8sdev-{name}",
                        tag=now,
                        containerfile=tmp_path / "container" / "Containerfile",
                        directory=tmp_path / "container",
                    )
                ],
                resources=[
                    LocalManifest(source=tmp_path / "deployment.yaml"),
                ],
            ),
            watch=True,
        ) as k8s,
    ):
        assert k8s.cluster is not None
        wait_for_created(k8s.cluster, f"-l app={name}")
        k8s.cluster.wait(f"pod -l app={name}", "condition=ready")
        assert (
            k8s.cluster.kubectl(
                ["exec", "-it", f"deployment/{name}", "--", "sh", "-c", "/test.sh"],
                as_dict=False,
            ).strip()
            == now.strip()
        )

        # Modify the container, ensure it reloads
        initial_start_time = datetime.datetime.fromisoformat(
            k8s.cluster.kubectl(["get", "pod", "-l", f"app={name}"])["items"][0]["status"]["startTime"],
        )
        first_restart_time = datetime.datetime.now(tz=datetime.UTC)
        new_output = "updated"
        (tmp_path / "container" / "test.sh").write_text(
            dedent(f"""\
                #!/bin/sh
                echo "{new_output}"
                """)
        )
        while datetime.datetime.now(tz=datetime.UTC) < first_restart_time + datetime.timedelta(minutes=2):
            try:
                second_start_time = datetime.datetime.fromisoformat(
                    k8s.cluster.kubectl(["get", "pod", "-l", f"app={name}"])["items"][0]["status"]["startTime"],
                )
            except KeyError, IndexError:  # pragma: no cover
                continue
            if second_start_time > initial_start_time:
                # Pod has been restarted
                break
        first_restart_finished_at = datetime.datetime.now(tz=datetime.UTC)
        assert (
            k8s.cluster.kubectl(
                ["exec", "-it", f"deployment/{name}", "--", "sh", "-c", "/test.sh"],
                as_dict=False,
            ).strip()
            == new_output.strip()
        )

        # Ensure that restarting multiple pods is concurrent
        pod_count = 10
        k8s.cluster.kubectl(["scale", f"--replicas={pod_count}", f"deployment/{name}"], as_dict=False)
        while len(k8s.cluster.kubectl(["get", "pod", "-l", f"app={name}"])["items"]) < pod_count:  # pragma: no cover
            sleep(1)
        last_scaled_time = max(
            datetime.datetime.fromisoformat(
                pod["status"]["startTime"],
            )
            for pod in k8s.cluster.kubectl(["get", "pod", "-l", f"app={name}"])["items"]
        )
        second_restart_time = datetime.datetime.now(tz=datetime.UTC)
        final_output = "scaled!"
        (tmp_path / "container" / "test.sh").write_text(
            dedent(f"""\
                #!/bin/sh
                echo "{final_output}"
                """)
        )
        while datetime.datetime.now(tz=datetime.UTC) < second_restart_time + datetime.timedelta(minutes=2):
            try:
                if all(
                    datetime.datetime.fromisoformat(
                        pod["status"]["startTime"],
                    )
                    >= last_scaled_time
                    for pod in k8s.cluster.kubectl(["get", "pod", "-l", f"app={name}"])["items"]
                ):
                    break
            except KeyError, IndexError:  # pragma: no cover
                sleep(1)
        second_restart_finished_at = datetime.datetime.now(tz=datetime.UTC)
        assert (
            k8s.cluster.kubectl(
                ["exec", "-it", f"deployment/{name}", "--", "sh", "-c", "/test.sh"],
                as_dict=False,
            ).strip()
            == final_output.strip()
        )
        # It should take less than twice as long to restart all the pods than to restart just 1 pod
        assert (second_restart_finished_at - second_restart_time) < (first_restart_finished_at - first_restart_time) * 2
