"""Pydantic models for YAML config."""

import json
from abc import ABC
from abc import abstractmethod
from functools import lru_cache
from logging import getLogger
from pathlib import Path
from shutil import which as shutil_which
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Self

import yaml
from platformdirs import user_cache_dir
from pydantic import AnyHttpUrl
from pydantic import AnyUrl
from pydantic import BaseModel
from pydantic import Discriminator
from pydantic import Field
from pydantic import model_validator
from pydantic import Tag

from pyk8sdev.exceptions import BinaryNotFoundError
from pyk8sdev.exceptions import CantNameOCIRepoError
from pyk8sdev.exceptions import MustNameNonOCIRepoError

logger = getLogger(__name__)

Providers = Literal["kind", "minikube", "minikube-docker", "minikube-kvm2", "external", "talosctl", "k3d"]


class CacheProvider(BaseModel):
    """Extra pull-through proxy caches to configure."""

    description: str
    repository: str
    url_override: str | None = None
    registry_image_version: str = "2"

    @property
    def url(self) -> str:
        """The URL to pull the image from."""
        if self.url_override is None:
            return f"https://{self.repository}"
        return self.url_override


class HelmChart(BaseModel, ABC):
    """Common Helm chart handling."""

    name: str
    namespace: str = "default"

    values_file: Path | None = None
    values_override: str = Field(default="", description="YAML-formatted values, i.e. 'foo: 1'")

    @abstractmethod
    def get_chart_ref(self) -> str:  # pragma: no cover
        """Return the reference to the Helm chart to be installed."""
        return NotImplemented


class LocalHelmChart(HelmChart):
    """Local Helm chart representation (watched)."""

    directory: Path

    def get_chart_ref(self) -> str:
        """Return the reference to the Helm chart to be installed."""
        return str(self.directory.absolute())


class RemoteHelmChart(HelmChart):
    """Remote Helm chart representation (not watched)."""

    version: str | None = None

    repository_url: AnyUrl
    repository_name: str | None = None

    @model_validator(mode="after")
    def handle_oci_repos(self) -> Self:
        """Handle OCI repository information."""
        if self.repository_url.scheme == "oci" and self.repository_name is not None:
            raise CantNameOCIRepoError
        if self.repository_url.scheme != "oci" and self.repository_name is None:
            raise MustNameNonOCIRepoError
        return self

    def get_chart_ref(self) -> str:
        """Return the reference to the Helm chart to be installed."""
        if self.repository_name is None:
            return str(self.repository_url)
        return f"{self.repository_name}/{self.name}"


class Manifest(BaseModel, ABC):
    """Common manifest handling."""

    @abstractmethod
    def get_source(self) -> Path | str:  # pragma: no cover
        """Return reference to the manifest location."""
        return NotImplemented


class LocalManifest(Manifest):
    """Plain Kubernetes manifest file representation watched on the local filesystem."""

    source: Path

    def get_source(self) -> Path:
        """Return source path."""
        return self.source


class RemoteManifest(Manifest):
    """Plain Kubernetes manifest file representation from a remote source (not watched)."""

    source: AnyHttpUrl

    def get_source(self) -> str:
        """Return source URL."""
        return str(self.source)


class Container(BaseModel):
    """Representation of a container to be built for development."""

    name: str
    tag: str
    containerfile: Path
    directory: Path


class Command(BaseModel):
    """Arbitrary command to run during resource setup."""

    command: str | list[str]


def _get_resource_type(  # noqa: C901, PLR0911 have to repeat for dict and object cases
    v: object,
) -> Literal["local_manifest", "remote_manifest", "local_helm_chart", "remote_helm_chart", "dict_resource", "command"]:
    """Union discriminator for resource types."""
    if isinstance(v, dict):
        if "source" in v:
            if "://" in v.get("source", ""):
                return "remote_manifest"
            return "local_manifest"
        if "name" in v:
            if "repository_url" in v:
                return "remote_helm_chart"
            return "local_helm_chart"
        if "command" in v:
            return "command"
        return "dict_resource"
    if isinstance(v, RemoteManifest):
        return "remote_manifest"
    if isinstance(v, LocalManifest):
        return "local_manifest"
    if isinstance(v, RemoteHelmChart):
        return "remote_helm_chart"
    if isinstance(v, LocalHelmChart):
        return "local_helm_chart"
    if isinstance(v, Command):
        return "command"
    return "dict_resource"


class ConfigFile(BaseModel):
    """Represents a configuration file."""

    cluster_name: str = "test"
    provider: Providers = "kind"
    api_version: str = "1.34.0"
    provider_config: Path | None = None
    cache_dir_override: Path | None = None
    extra_cluster_options: list[str] = Field(
        description="Extra options to pass on cluster creation",
        default_factory=list,
    )
    crash_log_output: Path | None = None

    containers: list[Container] = Field(default_factory=list)

    additional_cache_providers: list[CacheProvider] = Field(default_factory=list)

    resources: list[
        Annotated[
            Annotated[LocalManifest, Tag("local_manifest")]
            | Annotated[RemoteManifest, Tag("remote_manifest")]
            | Annotated[LocalHelmChart, Tag("local_helm_chart")]
            | Annotated[RemoteHelmChart, Tag("remote_helm_chart")]
            | Annotated[Command, Tag("command")]
            | Annotated[dict, Tag("dict_resource")],
            Discriminator(_get_resource_type),
        ]
    ] = Field(
        description="Order of resources to apply",
        default_factory=list,
    )

    _config_file_path: Path | None = None

    @model_validator(mode="after")
    def handle_relative_paths(self) -> Self:
        """Make any relative paths relative to the config file if set."""
        if self._config_file_path is None:
            return self
        _make_path_absolute(self, self._config_file_path.parent)
        return self

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401 match override signature
        """Use configfile path as the base for relative Paths."""
        if context is not None and isinstance(context, dict) and "_config_file_path" in context:
            self._config_file_path = context["_config_file_path"]
        else:
            logger.warning("Created config without a file path")
        return super().model_post_init(context)

    @property
    def cache_providers(self) -> list[CacheProvider]:
        """Combined list of all desired cache providers."""
        return [
            CacheProvider(description="docker", repository="docker.io", url_override="https://registry-1.docker.io"),
            # Please note that the quay.io proxy doesn't support the recent Docker image schema, so we run an older
            # registry image version (2.5).
            # https://www.talos.dev/v0.6/guides/configuring-pull-through-cache/#launch-the-caching-docker-registry-proxies
            CacheProvider(description="quay", repository="quay.io", registry_image_version="2.5"),
            CacheProvider(description="gcr", repository="gcr.io"),
            CacheProvider(description="ghcr", repository="ghcr.io"),
            CacheProvider(description="k8s", repository="registry.k8s.io"),
            *self.additional_cache_providers,
        ]

    @property
    def cache_dir(self) -> Path:
        """Location of all cache files."""
        if self.cache_dir_override is not None:
            return self.cache_dir_override
        return Path(user_cache_dir(appname="pyk8sdev"))

    @classmethod
    def from_file(cls, file: Path) -> Self:
        """Load configuration from the provided file."""
        with file.open() as fd:
            data = yaml.safe_load(fd.read())
            if data is None:
                data = {}
            return cls.model_validate(data, context={"_config_file_path": file})


def _make_path_absolute(instance: object, base_path: Path) -> None:
    if not isinstance(instance, BaseModel):
        return
    for field_name in type(instance).model_fields:
        field = getattr(instance, field_name)
        if isinstance(field, list):
            for item in field:
                _make_path_absolute(item, base_path)
        elif isinstance(field, BaseModel):
            _make_path_absolute(field, base_path)
        elif not isinstance(field, Path) or field.is_absolute():
            continue
        else:
            setattr(instance, field_name, base_path / field)


def save_config_schema(schema_path: str | Path) -> None:
    """Save config schema to a file."""
    Path(schema_path).write_text(json.dumps(ConfigFile.model_json_schema(), indent=2))


@lru_cache
def which(binary_name: str) -> str:
    """shutil.which but raise exception for None."""
    binary_path = shutil_which(binary_name)
    if binary_path is None:
        raise BinaryNotFoundError(binary_name)
    return binary_path
