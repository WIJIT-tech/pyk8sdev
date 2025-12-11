"""CLI interface for pyk8sdev."""

import argparse
from logging import getLogger
from pathlib import Path

from pyk8sdev.config import ConfigFile
from pyk8sdev.config import save_config_schema

logger = getLogger(__name__)


def _main() -> None:
    """Parse commandline flags and start."""
    parser = argparse.ArgumentParser(description="pyk8sdev CLI interface")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to config file",
        default=Path(".pyk8sdev.yaml"),
    )
    parser.add_argument(
        "-s",
        "--schema",
        action="store_true",
        help="Save config schema",
    )
    args = parser.parse_args()
    if args.schema:
        save_config_schema(args.config.parent / f"{args.config.stem}.schema.json")
        return
    if not args.config.exists():
        parser.error(f"Config file does not exist at {args.config}")
    config = ConfigFile.from_file(args.config)

    # Try running CLI
    try:
        from pyk8sdev.app import TerminalInterface  # noqa: PLC0415
    except ImportError:
        pass  # Ignore and run without CLI
    else:
        app = TerminalInterface(config=config)
        app.run()
        return

    # No CLI, just run until interrupted
    from threading import Event  # noqa: PLC0415

    from pyk8sdev import CachedK8sCluster  # noqa: PLC0415

    with CachedK8sCluster(config, watch=True):
        Event().wait()


if __name__ == "__main__":
    _main()
