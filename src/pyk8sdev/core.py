"""Core for bringing everything together."""

import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from enum import auto
from enum import IntEnum
from functools import partial
from itertools import chain
from logging import getLogger
from pathlib import Path
from subprocess import CalledProcessError
from subprocess import run
from textwrap import dedent
from typing import Self
from typing import TYPE_CHECKING

from pytest_kubernetes.options import ClusterOptions
from pytest_kubernetes.providers import ExternalManagerBase
from pytest_kubernetes.providers import K3dManagerBase
from pytest_kubernetes.providers import KindManagerBase
from pytest_kubernetes.providers import MinikubeDockerManagerBase
from pytest_kubernetes.providers import MinikubeKVM2ManagerBase
from python_on_whales import docker

from pyk8sdev.config import Command
from pyk8sdev.config import Container as PyK8sDevContainer
from pyk8sdev.config import HelmChart
from pyk8sdev.config import LocalHelmChart
from pyk8sdev.config import LocalManifest
from pyk8sdev.config import Manifest
from pyk8sdev.container import does_file_effect_container
from pyk8sdev.container import ensure_container
from pyk8sdev.exceptions import ProviderNotAvailableError
from pyk8sdev.exceptions import RegistriesNotInConfigError
from pyk8sdev.exceptions import UnknownResourceError
from pyk8sdev.helm import does_file_affect_helm
from pyk8sdev.helm import ensure_helm_released
from pyk8sdev.helm import helm_upgrade
from pyk8sdev.k8s.talos import TalosManagerBase
from pyk8sdev.watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from pytest_kubernetes.providers import AClusterManager
    from python_on_whales import Container as DockerContainer

    from pyk8sdev.config import ConfigFile


logger = getLogger(__name__)
cluster_providers = {
    "external": ExternalManagerBase,
    "k3d": K3dManagerBase,
    "kind": KindManagerBase,
    "minikube": MinikubeDockerManagerBase,
    "minikube-docker": MinikubeDockerManagerBase,
    "minikube-kvm2": MinikubeKVM2ManagerBase,
    "talosctl": TalosManagerBase,
}


class ClusterEvent(IntEnum):
    """States that are emitted to track progress."""

    STARTING_REGISTRIES = auto()
    STARTING_CLUSTER = auto()
    STARTING_WATCHER = auto()
    BUILDING_CONTAINERS = auto()
    APPLYING_RESOURCES = auto()
    IDLE = auto()
    STOPPING = auto()
    STOPPED = auto()


class CachedK8sCluster:
    """Handler for all management of the kubernetes cluster and registries."""

    def __init__(
        self,
        config: ConfigFile,
        *,
        watch: bool = False,
        state_change_callback: Callable[[ClusterEvent], None] | None = None,
    ):
        """Initialize a cached Kubernetes cluster."""
        self.watch = watch
        self.config = config
        self._registries: list[DockerContainer] = []
        self._dc = docker
        self._lock = threading.Lock()
        self.watcher = Watcher()
        self._state_change_callback = self._noop if state_change_callback is None else state_change_callback

        with self._lock:
            self.cluster = cluster_providers[self.config.provider]()

    def _noop(self, event: ClusterEvent) -> None:
        """Do nothing if state_change_callback isn't explicitly set."""

    def _create_registry_containers(self) -> None:
        """Cache upstream containers to save time and bandwidth."""
        self.config.cache_dir.mkdir(exist_ok=True)

        # Create a local registry for our development images
        (self.config.cache_dir / "local").mkdir(exist_ok=True)
        with self._lock:
            existing_container = self._dc.container.list(
                filters=[("name", f"{self.config.cluster_name}-local-registry")],
            )
            if existing_container:
                self._registries.append(existing_container[0])
            else:
                self._registries.append(
                    self._dc.container.run(
                        image="registry:2",
                        name=f"{self.config.cluster_name}-local-registry",
                        detach=True,
                        publish=[
                            (5000, 5000, "tcp"),
                        ],
                        volumes=[
                            (f"{self.config.cache_dir / 'local'}", "/var/lib/registry", "rw"),
                        ],
                        remove=True,
                    )
                )
            # Create pull-through proxy caches for other images
            for i, cache_provider in enumerate(self.config.cache_providers, start=1):
                (self.config.cache_dir / cache_provider.description).mkdir(exist_ok=True)
                existing_container = self._dc.container.list(
                    filters=[("name", f"{self.config.cluster_name}-proxy-{cache_provider.description}")],
                )
                if existing_container:
                    self._registries.append(existing_container[0])
                else:
                    self._registries.append(
                        self._dc.container.run(
                            image=f"registry:{cache_provider.registry_image_version}",
                            name=f"{self.config.cluster_name}-proxy-{cache_provider.description}",
                            detach=True,
                            envs={
                                "REGISTRY_PROXY_REMOTEURL": cache_provider.url,
                            },
                            publish=[
                                (5000 + i, 5000, "tcp"),
                            ],
                            volumes=[
                                (f"{self.config.cache_dir / cache_provider.description}", "/var/lib/registry", "rw"),
                            ],
                            remove=True,
                        )
                    )

    @staticmethod
    def _ensure_kind_containerd_config_present(provider_config: str) -> None:
        if (
            "containerdConfigPatches:" not in provider_config
            or '[plugins."io.containerd.grpc.v1.cri".registry]' not in provider_config
            or 'config_path = "/etc/containerd/certs.d"' not in provider_config
        ):
            raise RegistriesNotInConfigError

    def pre_configure_kind_registry(self) -> list[str]:
        """Ensure containerd config is in kind config."""
        if self.config.provider_config is not None:
            provider_config = self.config.provider_config.read_text()
            self._ensure_kind_containerd_config_present(provider_config)
            return []
        if "--config" in self.config.extra_cluster_options:
            provider_config = Path(
                self.config.extra_cluster_options[self.config.extra_cluster_options.index("--config") + 1]
            ).read_text()
            self._ensure_kind_containerd_config_present(provider_config)
            return []

        (self.config.cache_dir / "kind-config.yaml").write_text(
            dedent("""\
            kind: Cluster
            apiVersion: kind.x-k8s.io/v1alpha4
            containerdConfigPatches:
            - |-
              [plugins."io.containerd.grpc.v1.cri".registry]
                config_path = "/etc/containerd/certs.d"
            """)
        )
        return [
            "--config",
            str(self.config.cache_dir / "kind-config.yaml"),
        ]

    def post_configure_kind_registry(self) -> None:
        """Iterate through all kind nodes and configure them to use container proxies."""
        for network in self._dc.network.list():
            if network.name != "kind":
                continue
            for net_node in network.containers.values():
                node = self._dc.container.inspect(net_node.name)
                if not node.name.startswith(self.config.cluster_name):
                    continue
                # Local registry
                self._dc.execute(container=node, command=["mkdir", "-p", "/etc/containerd/certs.d/localhost:5000"])
                self._dc.execute(
                    container=node,
                    command=[
                        "bash",
                        "-c",
                        f'echo "[host.\\"http://{self.config.cluster_name}-local-registry:5000\\"]\n'
                        '  capabilities = [\\"pull\\", \\"resolve\\"]\n'
                        '  skip_verify = true"'
                        " > /etc/containerd/certs.d/localhost:5000/hosts.toml",
                    ],
                )
                # Pull-through proxy caches
                for cache_provider in self.config.cache_providers:
                    self._dc.execute(
                        container=node, command=["mkdir", "-p", f"/etc/containerd/certs.d/{cache_provider.repository}"]
                    )
                    self._dc.execute(
                        container=node,
                        command=[
                            "bash",
                            "-c",
                            f'echo "server = \\"{cache_provider.url}\\"\n\n'
                            f'[host.\\"http://{self.config.cluster_name}-proxy-{cache_provider.description}:5000\\"]\n'
                            '  capabilities = [\\"pull\\", \\"resolve\\"]\n'
                            '  skip_verify = true"'
                            f" > /etc/containerd/certs.d/{cache_provider.repository}/hosts.toml",
                        ],
                    )
                # Make a new config take effect
                self._dc.execute(container=node, command=["systemctl", "restart", "containerd"])
            # Attach registries to the kind network
            for proxy in self._dc.container.list():
                if (
                    proxy.name.startswith(f"{self.config.cluster_name}-proxy-")
                    or proxy.name == f"{self.config.cluster_name}-local-registry"
                ):
                    self._dc.network.connect(network, proxy)

    def setup_resource(self, resource: HelmChart | Manifest | Command | dict) -> None:  # noqa: C901
        """Apply a resource to the cluster and start a watch if enabled."""
        if isinstance(resource, HelmChart):
            if self.watch and isinstance(resource, LocalHelmChart):
                for p in (resource.directory, resource.values_file):
                    if p is None:
                        continue
                    self.watcher.add_watch(
                        f"Helm: {resource.namespace}/{resource.name}",
                        p,
                        partial(helm_upgrade, resource, self.cluster.kubeconfig),
                        applicable=partial(
                            does_file_affect_helm,
                            directory=resource.directory,
                            values_file=resource.values_file,
                        ),
                    )
            ensure_helm_released(resource, self.cluster.kubeconfig)
        elif isinstance(resource, Manifest):
            if self.watch and isinstance(resource, LocalManifest):
                self.watcher.add_watch(
                    f"Manifest: {resource.get_source()}",
                    resource.get_source(),
                    partial(self.cluster.apply, resource.get_source()),
                )
            self.cluster.apply(resource.get_source())
        elif isinstance(resource, Command):
            # TODO(MR): need a test for commands
            # https://github.com/WIJIT-tech/pyk8sdev/issues/4
            try:
                rc = run(  # noqa: S603 comes from user's config
                    resource.command,
                    check=True,
                    shell=isinstance(resource.command, str),
                    capture_output=True,
                    text=True,
                    env={
                        **os.environ,
                        "KUBECONFIG": str(self.cluster.kubeconfig),
                    },
                )
            except CalledProcessError as rc_error:
                logger.info(rc_error.stdout)
                logger.exception(rc_error.stderr)
                raise

            if rc.stdout:
                logger.info(rc.stdout)
            if rc.stderr:
                logger.error(rc.stderr)
        elif isinstance(resource, dict):
            self.cluster.apply(resource)
        else:
            raise UnknownResourceError

    def setup_container(self, container: PyK8sDevContainer) -> None:
        """Bootstrap container and watch for changes to sources if applicable."""
        if self.watch:
            self.watcher.add_watch(
                f"Container: {container.name}:{container.tag}",
                container.directory,
                partial(self.rebuild_container, container),
                applicable=partial(does_file_effect_container, directory=container.directory),
            )
        self.rebuild_container(container)

    def rebuild_container(self, container: PyK8sDevContainer) -> None:
        """Build and deploy container then restart existing pods."""
        logger.info("Rebuilding container", extra={"container.name": container.name})
        ensure_container(self._dc, container.name, container.tag, container.containerfile, container.directory)
        logger.info("Rebuilt container", extra={"container.name": container.name})
        # Restart existing pods
        pod_list: dict = self.cluster.kubectl(["get", "pods", "--all-namespaces=true"])
        with ThreadPoolExecutor() as executor:
            for pod in pod_list["items"]:
                for c in pod["spec"]["containers"]:
                    if c["image"] != f"localhost:5000/{container.name}:{container.tag}":
                        continue
                    logger.info(
                        "Pod restart triggered for %s/%s",
                        pod["metadata"]["namespace"],
                        pod["metadata"]["name"],
                        extra={
                            "container.name": container.name,
                            "pod name": pod["metadata"]["name"],
                            "pod namespace": pod["metadata"]["namespace"],
                        },
                    )
                    executor.submit(  # Each delete takes a while, try to get them all concurrently
                        self.cluster.kubectl,
                        ["delete", "pod", "--namespace", pod["metadata"]["namespace"], pod["metadata"]["name"]],
                        as_dict=False,
                    )
                    break  # Only have to restart the pod once, not once per container

    def _create_cluster(self) -> AClusterManager:
        """Create a cluster with registry caches available."""
        # Common setup
        k8s_config = ClusterOptions()
        if self.config.cluster_name is not None:
            k8s_config.cluster_name = self.config.cluster_name
        if self.config.api_version is not None:
            k8s_config.api_version = self.config.api_version
        if self.config.provider_config is not None:
            k8s_config.provider_config = self.config.provider_config
        k8s_config.kubeconfig_path = self.config.cache_dir / "kubeconfig.yaml"
        if self.config.provider not in cluster_providers:
            raise ProviderNotAvailableError(self.config.provider)

        # Pre-create custom setup
        if self.config.provider == "talosctl":
            cluster_cache_options = [
                "--registry-mirror",
                "localhost:5000=http://10.5.0.1:5000",
                *chain(
                    *(
                        [
                            "--registry-mirror",
                            f"{registry.repository}=http://10.5.0.1:{5000 + i}",
                        ]
                        for i, registry in enumerate(self.config.cache_providers, start=1)
                    )
                ),
            ]
        elif self.config.provider == "kind":
            cluster_cache_options = self.pre_configure_kind_registry()
        else:
            cluster_cache_options = []

        with self._lock:  # create() has side-effects that we'll want to wrap with a lock
            self.cluster.create(
                cluster_options=k8s_config,
                options=self.config.extra_cluster_options + cluster_cache_options,
            )

        # Post-create custom setup
        if self.config.provider == "kind":
            self.post_configure_kind_registry()

        return self.cluster

    def __enter__(self) -> Self:
        """Create a cluster and all defined resources."""
        try:
            self._state_change_callback(ClusterEvent.STARTING_REGISTRIES)
            self._create_registry_containers()
            self._state_change_callback(ClusterEvent.STARTING_CLUSTER)
            self._create_cluster()
            logger.info("created cluster")
            if self.watch:
                self._state_change_callback(ClusterEvent.STARTING_WATCHER)
                self.watcher.start()
            self._state_change_callback(ClusterEvent.BUILDING_CONTAINERS)
            for container in self.config.containers:
                self.setup_container(container)
            self._state_change_callback(ClusterEvent.APPLYING_RESOURCES)
            for resource in self.config.resources:
                self.setup_resource(resource)
            self._state_change_callback(ClusterEvent.IDLE)
        except Exception:
            logger.exception("Error during cluster start-up")
            if self.config.crash_log_output is not None:
                with self.config.crash_log_output.open("w") as fd:
                    traceback.print_exc(file=fd)
            self.stop()
            # TODO(MR): Need a test to ensure everything is cleaned up when this happens
            # https://github.com/WIJIT-tech/pyk8sdev/issues/1
            raise
        return self

    def stop(self) -> None:
        """Teardown cluster and remove defined resources."""
        self._state_change_callback(ClusterEvent.STOPPING)
        self.watcher.stop()
        with self._lock:
            self.cluster.delete()
            # Remove all registry containers
            for registry in self._registries:
                registry.stop()
            self._registries = []
        self._state_change_callback(ClusterEvent.STOPPED)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Teardown cluster and remove defined resources."""
        self.stop()
        return None
