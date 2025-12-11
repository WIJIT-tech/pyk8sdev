"""Tests for config handling."""

import logging
from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from pydantic import AnyUrl

from pyk8sdev.config import _make_path_absolute
from pyk8sdev.config import ConfigFile
from pyk8sdev.config import Container
from pyk8sdev.config import LocalManifest
from pyk8sdev.config import RemoteHelmChart
from pyk8sdev.config import save_config_schema
from pyk8sdev.config import which
from pyk8sdev.exceptions import BinaryNotFoundError


def test_default_config(caplog: pytest.LogCaptureFixture) -> None:
    """Test that the default config values are correctly set."""
    with caplog.at_level(logging.WARNING):
        config = ConfigFile()
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert "Created config without a file path" in caplog.records[0].msg
    assert config.cluster_name == "test"
    assert config.provider == "kind"
    assert config._config_file_path is None


def test_config_file_absolute_path_handling(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Ensure that absolute paths are correctly referenced from the config file path."""
    file = tmp_path / "config.yaml"
    with file.open("w") as fd:
        fd.write(
            yaml.dump(
                {
                    "provider_config": str(Path("/test/provider.yaml")),
                    "cache_dir_override": str(Path("/cache")),
                }
            )
        )
    with caplog.at_level(logging.WARNING):
        config = ConfigFile.from_file(file)
        assert len(caplog.records) == 0
    assert config._config_file_path == file
    assert config.provider_config == Path("/test/provider.yaml")
    assert config.cache_dir == Path("/cache")


def test_config_file_relative_path_handling(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Ensure that relative paths are correctly referenced from the config file path."""
    file = tmp_path / "config.yaml"
    with file.open("w") as fd:
        fd.write(
            yaml.dump(
                {
                    "provider_config": str(Path("test/provider.yaml")),
                    "cache_dir_override": str(Path("cache")),
                    "containers": [
                        {
                            "name": "test",
                            "tag": "latest",
                            "containerfile": "container/Containerfile",
                            "directory": "container",
                        }
                    ],
                }
            )
        )
    with caplog.at_level(logging.WARNING):
        config = ConfigFile.from_file(file)
        assert len(caplog.records) == 0
    assert config._config_file_path == file
    assert config.provider_config == file.parent / "test" / "provider.yaml"
    assert config.cache_dir == file.parent / "cache"
    assert config.containers[0].containerfile == file.parent / "container" / "Containerfile"
    assert config.containers[0].directory == file.parent / "container"


@pytest.mark.parametrize(
    ("file_name", "file_content", "expected_result"),
    [
        (
            "empty.yaml",
            dedent("""\
            ---
            """),
            ConfigFile(),
        ),
        (
            "mixed.yaml",
            dedent("""\
            ---
            cluster_name: mixed
            containers:
              - name: mixed
                tag: latest
                containerfile: Containerfile
                directory: .
            resources:
              - source: manifests/1.yaml
              - source: manifests/2.yaml
              - name: example
                namespace: example
                version: v1.0.0
                repository_url: oci://example.com/example
                values_override: |-
                  ---
                  a:
                    enabled: true
                  b:
                    enabled: false
                  c: debug
              - source: manifests/3.yaml
            """),
            ConfigFile(
                cluster_name="mixed",
                containers=[
                    Container(
                        name="mixed",
                        tag="latest",
                        containerfile=Path("Containerfile"),
                        directory=Path(),
                    ),
                ],
                resources=[
                    LocalManifest(source=Path("manifests/1.yaml")),
                    LocalManifest(source=Path("manifests/2.yaml")),
                    RemoteHelmChart(
                        name="example",
                        namespace="example",
                        version="v1.0.0",
                        repository_url=AnyUrl("oci://example.com/example"),
                        values_override=dedent("""\
                            ---
                            a:
                              enabled: true
                            b:
                              enabled: false
                            c: debug"""),
                    ),
                    LocalManifest(source=Path("manifests/3.yaml")),
                ],
            ),
        ),
    ],
    ids=[
        "empty",
        "mixed",
    ],
)
def test_sync_config_loading(tmp_path: Path, file_name: str, file_content: str, expected_result: ConfigFile) -> None:
    """Ensure we can parse config files."""
    file = tmp_path / file_name
    _make_path_absolute(expected_result, tmp_path)
    file.write_text(file_content)
    config = ConfigFile.from_file(file)
    assert str(config) == str(expected_result)
    assert config._config_file_path == file


def test_remote_helm_chart_validation() -> None:
    """Ensure that OCI logic works correctly for remote Helm charts."""
    with pytest.raises(ValueError, match="require a name"):
        RemoteHelmChart(
            name="chart1",
            repository_url=AnyUrl("https://example.com/chart1"),
        )
    with pytest.raises(ValueError, match="name an OCI"):
        RemoteHelmChart(
            name="chart2",
            repository_name="chart2repo",
            repository_url=AnyUrl("oci://example.com/chart2repo"),
        )


def test_missing_binary() -> None:
    """Should raise an exception if a binary can't be found."""
    with pytest.raises(BinaryNotFoundError, match="not found"):
        which("sdf9078sdfjklmsdnf8923h278fdh")


def test_save_config_schema(tmp_path: Path) -> None:
    """Should be able to save config schema."""
    schema_path = tmp_path / "config.schema.json"
    save_config_schema(schema_path)
    assert schema_path.exists()
    assert schema_path.stat().st_size > 0
