"""pytest integration."""

from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pyk8sdev import CachedK8sCluster
from pyk8sdev.config import ConfigFile

if TYPE_CHECKING:
    from collections.abc import Generator

logger = getLogger(__name__)
_default_config_name = ".pyk8sdev.yaml"


@pytest.fixture(scope="session")
def cached_k8s_cluster(request: pytest.FixtureRequest) -> Generator[CachedK8sCluster]:
    """Create a cached kubernetes cluster."""
    config_file = None
    if "pyk8sdev" in request.keywords:
        req = dict(request.keywords["pyk8sdev"].kwargs)
        config_file = req.get("config")
    # Fallback to using a default file name where pytest was called from
    if config_file is None and (request.config.rootpath / _default_config_name).exists():
        config_file = request.config.rootpath / _default_config_name
    config = ConfigFile.from_file(Path(config_file)) if config_file is not None else ConfigFile()
    with CachedK8sCluster(config) as k8s:
        yield k8s


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add an option to specify a config file location."""
    k8s_group = parser.getgroup("pyk8sdev")
    k8s_group.addoption(
        "--pyk8sdev-config",
        type=Path,
        help="Path of the configuration file for creating a cached cluster",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Configure pyk8sdev based on command line options."""
    config_file = config.getoption("pyk8sdev_config")
    if config_file is not None and not config_file.exists():
        raise pytest.UsageError("The specified configuration file does not exist")  # noqa: TRY003 pytest pattern
