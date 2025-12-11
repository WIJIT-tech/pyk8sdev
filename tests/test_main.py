"""Tests for running as a CLI tool."""

import sys
from subprocess import run


def test_config_file_not_specified() -> None:
    """Test we get an error if the config file isn't specified."""
    rc = run(  # noqa: S603 all trusted input
        [sys.executable, "-m", "pyk8sdev"],
        check=False,
        capture_output=True,
    )
    assert rc.returncode == 2  # noqa: PLR2004 it's a return code from argparse
    assert "Config file does not exist at .pyk8sdev.yaml" in rc.stderr.decode()


def test_config_file_does_not_exist() -> None:
    """Test we get an error if the config file doesn't exist."""
    rc = run(  # noqa: S603 all trusted input
        [sys.executable, "-m", "pyk8sdev", "-c", "/test/does/not/exist.yaml"],
        check=False,
        capture_output=True,
    )
    assert rc.returncode == 2  # noqa: PLR2004 it's a return code from argparse
    assert "Config file does not exist" in rc.stderr.decode()


# TODO(MR): Test with a simple config file
# https://github.com/WIJIT-tech/pyk8sdev/issues/3
