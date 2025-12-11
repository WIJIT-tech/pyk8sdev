"""Add Talos as a supported provider.

Doesn't support image loading, so it's not easy to upstream.
"""

from logging import getLogger
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

from pytest_kubernetes.providers import AClusterManager

from pyk8sdev.exceptions import MissingKernelModuleError
from pyk8sdev.exceptions import TooOldError
from pyk8sdev.exceptions import UnsupportedError

if TYPE_CHECKING:
    from pytest_kubernetes.options import ClusterOptions

logger = getLogger(__name__)


class TalosManagerBase(AClusterManager):
    """Add Talos as a provider."""

    @classmethod
    def get_binary_name(cls) -> str:
        """Binary name for talos."""
        return "talosctl"

    def _on_create(self, cluster_options: ClusterOptions, **kwargs: Any) -> None:
        opts = kwargs.get("options", [])

        if cluster_options.api_version == "1.25.3":
            raise TooOldError

        if "br_netfilter" not in Path("/proc/modules").read_text():  # pragma: no cover
            raise MissingKernelModuleError

        if cluster_options.provider_config:
            opts += [
                "--talosconfig",
                str(cluster_options.provider_config),
            ]
        else:
            opts += [
                "--name",
                self.cluster_name,
                "--kubernetes-version",
                cluster_options.api_version,
            ]

        self._exec(
            [
                "cluster",
                "create",
                "docker",
                *opts,
            ],
            additional_env={
                "KUBECONFIG": str(self._cluster_options.kubeconfig_path),
            },
        )

    def _on_delete(self) -> None:
        self._exec(
            [
                "cluster",
                "destroy",
                "--name",
                self.cluster_name,
            ],
        )
        self._exec(
            [
                "config",
                "remove",
                "--noconfirm",
                self.cluster_name,
            ],
        )

    def load_image(self, image: str) -> None:  # noqa: ARG002 because we're matching the existing signature
        """Talos doesn't really support this, and we're overlaying a local registry anyway."""
        raise UnsupportedError
