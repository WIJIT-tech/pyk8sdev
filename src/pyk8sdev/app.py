"""TUI for interacting with a cached kubernetes cluster."""

import asyncio
import logging
from typing import Any
from typing import TYPE_CHECKING

from rich.logging import RichHandler
from textual import events  # noqa: TC002
from textual.app import App
from textual.app import ComposeResult
from textual.app import ReturnType
from textual.logging import TextualHandler
from textual.screen import ModalScreen
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import RichLog
from textual.worker import get_current_worker
from textual.worker import NoActiveWorker
from textual.worker import Worker
from textual.worker import WorkerState

from pyk8sdev import CachedK8sCluster
from pyk8sdev.core import ClusterEvent

if TYPE_CHECKING:
    from rich.console import RenderableType
    from textual.widget import Widget

    from pyk8sdev.config import ConfigFile

logger = logging.getLogger(__name__)
DEFAULT_LOG_LEVEL = logging.INFO


class RichConsole(RichLog):
    """Display for logs."""

    file = False
    max_lines = 1000
    console: Widget

    def print(self, content: RenderableType | object) -> None:
        """Redirect for RichHandler method to RichConsole output."""
        self.write(content)


class RefreshModal(ModalScreen[str]):
    """Refresh the selection modal window."""

    app: TerminalInterface

    def compose(self) -> ComposeResult:
        """Lay out selections for refresh."""
        yield Header()
        yield Label("Item to refresh [enter to select, escape or q to exit]:", classes="heading")
        yield ListView(
            *[
                ListItem(Label("None", classes="meta")),
                ListItem(Label("All", classes="meta")),
                *(
                    ()
                    if self.app.k8s is None
                    else (
                        ListItem(Label(watch_name))
                        # Need to deduplicate (Helm has an entry for the directory and values file)
                        for watch_name in sorted({watch.name for watch in self.app.k8s.watcher.watched})
                    )
                ),
            ]
        )

    def on_key(self, event: events.Key) -> None:
        """Pass the selected button to the main window."""
        if event.name in ("escape", "q"):
            self.dismiss()
        if event.name == "enter":
            self.dismiss(self.query_one(ListView).highlighted_child.query_one(Label).content)


class TerminalInterface(App):
    """A Textual app to manage stopwatches."""

    TITLE = "pyk8sdev"
    BINDINGS = [  # noqa: RUF012 matches upstream signature unfortunately...
        ("l", "cycle_log_level", "Cycle log level"),
        ("r", "refresh_modal", "Refresh options"),
    ]
    SCREENS = {  # noqa: RUF012 matches upstream signature unfortunately...
        "refresh": RefreshModal,
    }
    CSS_PATH = "dom.tcss"

    _status = ClusterEvent.STOPPED
    _log_level = logging.getLevelName(DEFAULT_LOG_LEVEL)

    def __init__(self, *args: Any, config: ConfigFile, **kwargs: Any):
        """Add extra fields to our app."""
        super().__init__(*args, **kwargs)
        self.config = config
        self.k8s: CachedK8sCluster | None = None

    def compose(self) -> ComposeResult:
        """Call to add widgets to the app."""
        log = RichConsole(highlight=True, markup=True)
        yield Header()
        yield Footer()
        yield log

        # noinspection PyTypeChecker
        logging.basicConfig(
            level=DEFAULT_LOG_LEVEL,
            handlers=[RichHandler(console=log), TextualHandler()],
        )

    def _update_status(self) -> None:
        self.sub_title = f"Cluster status: {self._status.name} | Log level: {self._log_level}"
        self.refresh_bindings()

    def _state_change_callback(self, event: ClusterEvent) -> None:
        """Receive events from the cluster-runner thread."""
        try:
            get_current_worker()
        except NoActiveWorker:
            self._update_cluster_status(event)
        else:
            self.call_from_thread(self._update_cluster_status, event)

    def _update_cluster_status(self, event: ClusterEvent) -> None:
        """Update cluster status in UI."""
        self._status = event
        self._update_status()

        if event is ClusterEvent.STOPPED:
            super().exit()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002 match signature
        """Show or hide the refresh option as available."""
        if action == "refresh_modal":
            if self.k8s is None:
                return None
            if not self.k8s.watcher.watched:
                # No resources to watch, hide option
                return None
            if self.k8s is None:
                # Not ready yet, dim option
                return False
        return True

    def _modal_callback(self, result: str | None) -> None:
        """Trigger refresh of resource."""
        if result is None or result == "None":
            return
        if self.k8s is None:
            logger.error("kubernetes is not attached, but received refresh result?!")
            return
        self.query_one(RichConsole).print(f"Refreshing {result}")
        for watch in self.k8s.watcher.watched:
            logger.debug("checking watch=%s", watch.name)
            if result in ("All", watch.name):
                watch.update()
                self.k8s.watcher.executor.submit(watch.locked_apply)

    def action_refresh_modal(self) -> None:
        """Create manual refresh buttons modal window."""
        self.push_screen("refresh", self._modal_callback)

    def action_cycle_log_level(self) -> None:
        """Change to the next log level."""
        new_level = logging.getLogger().level - 10
        if new_level < logging.DEBUG:
            new_level = logging.CRITICAL
        logging.getLogger().setLevel(new_level)
        self._log_level = logging.getLevelName(new_level)
        self._update_status()
        self.notify(f"Log level now set to {logging.getLevelName(new_level)}", title="Log level changed")

    def exit(
        self,
        result: ReturnType | None = None,  # noqa: ARG002 matching overridden signature
        return_code: int = 0,  # noqa: ARG002 matching overridden signature
        message: RenderableType | None = None,  # noqa: ARG002 matching overridden signature
    ) -> None:
        """Trigger cluster shutdown.

        Actual super().exit() will be triggered when the cluster indicates it has stopped.
        """
        self.notify("Stopping cluster, please wait...", title="Stopping")
        self.workers.cancel_group(self, "main")

    def on_mount(self) -> None:
        """Kickoff cluster creation."""
        self.run_worker(self._start, name="main", group="main", exclusive=True, thread=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Update state if the main worker and exit if stopped."""
        self.log(event)
        if event.worker.name == "main" and event.state in {
            WorkerState.ERROR,
            WorkerState.SUCCESS,
        }:
            super().exit()

    def _handle_exception(self, error: Exception) -> None:
        """Clean up cluster on app exceptions."""
        if self.k8s is not None:
            self.k8s.stop()
        return super()._handle_exception(error)

    def _set(self, k8s: CachedK8sCluster) -> None:
        logger.info("setting k8s")
        self.k8s = k8s

    async def _start(self) -> None:
        """Start the cluster and wait for the user to terminate."""
        worker = get_current_worker()
        with CachedK8sCluster(self.config, watch=True, state_change_callback=self._state_change_callback) as k8s:
            self.call_from_thread(self._set, k8s)
            while not worker.is_cancelled:  # noqa: ASYNC110
                # asyncio.Event doesn't appear to work in a threaded worker, so fallback to while loop
                await asyncio.sleep(1)
