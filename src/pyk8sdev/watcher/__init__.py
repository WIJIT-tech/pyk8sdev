"""Use inotify to watch for file changes and trigger actions."""

import contextlib
import datetime
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from functools import partial
from logging import getLogger
from typing import ClassVar
from typing import Self
from typing import TYPE_CHECKING

from asyncinotify import Mask
from asyncinotify import RecursiveInotify

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


logger = getLogger(__name__)

TIMEOUT: int | float = 1


@dataclass
class _Watched:
    name: str
    path: Path
    applicable: Callable[[Path], bool]
    apply: Callable[[], None]
    last_applied: datetime.datetime = field(
        init=False,
        default=datetime.datetime.fromtimestamp(0, tz=datetime.UTC),
    )
    _time_lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _apply_lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def update(self) -> None:
        """Update last_applied to the current time."""
        with self._time_lock:
            self.last_applied = datetime.datetime.now(tz=datetime.UTC)
        logger.debug("%s last_applied %s", self.name, self.last_applied)

    def locked_apply(self) -> None:
        """Run the apply function once at a time.

        Assuming that the apply function doesn't modify the file, running this after update() should prevent race
        conditions where files get updated by the developer during the execution of the apply function. Also ensures
        that we only try to apply once at a time.
        """
        try:
            with self._apply_lock:
                logger.debug("%s applying...", self.name)
                self.apply()
            logger.info("%s applying completed", self.name)
        except Exception:
            logger.exception("%s apply failed", self.name)


@dataclass(frozen=True)
class _QueuedInotifyEvent:
    cookie: int
    mask: Mask
    name: Path | None
    path: Path | None
    created: datetime.datetime = field(init=False, default_factory=partial(datetime.datetime.now, tz=datetime.UTC))

    def __post_init__(self) -> None:
        """Log creation."""
        logger.debug("Created %s event for %s", self.mask.name, self.path)


class Watcher:
    """Inotify file watch singleton."""

    _instance: ClassVar[Self | None] = None
    _singleton_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> Self:
        """Enforce Watcher as a singleton per cluster."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _destroy(cls) -> None:  # pragma: no cover
        """Use for tests to reset the singleton."""
        if cls._instance is not None:
            cls._instance.stop()
            cls._instance = None

    def __init__(self) -> None:
        """Create queues and locks."""
        self._inotify = RecursiveInotify()
        self._inotify.sync_timeout = TIMEOUT
        self.executor = ThreadPoolExecutor(thread_name_prefix="watcher")
        self._is_running = threading.Event()
        self.watched: list[_Watched] = []
        self._event_queue: queue.LifoQueue[_QueuedInotifyEvent] = queue.LifoQueue()

    def _watch_loop(self) -> None:
        while self._is_running.is_set():
            event = self._inotify.sync_get()
            if event is not None:
                self._event_queue.put(
                    _QueuedInotifyEvent(
                        cookie=event.cookie,
                        mask=event.mask,
                        name=event.name,
                        path=event.path,
                    )
                )
        logger.info("Watch loop completed")

    def _check_loop(self) -> None:
        while self._is_running.is_set():
            with contextlib.suppress(queue.Empty):
                event = self._event_queue.get(timeout=TIMEOUT)
                for applicable, watched in zip(
                    self.executor.map(lambda x, e=event: x.applicable(e.path), self.watched),
                    self.watched,
                    strict=True,
                ):
                    if not applicable:
                        continue
                    if watched.last_applied > event.created:
                        logger.debug("Skipping older %s event for %s", event.mask.name, event.path)
                        continue

                    watched.update()
                    self.executor.submit(watched.locked_apply)
                    logger.debug("Completed %s event for %s", event.mask.name, event.path)
                self._event_queue.task_done()
        logger.info("Check loop completed")

    def start(self) -> None:
        """Start the watcher loop."""
        self._is_running.set()
        self.executor.submit(self._watch_loop)
        self.executor.submit(self._check_loop)

    def stop(self) -> None:
        """Stop the watcher loop."""
        self._is_running.clear()
        self.watched = []
        for watch in self._inotify.watches:
            self._inotify.rm_watch(watch)
        self.executor.shutdown()

    def add_watch(
        self,
        name: str,
        path: Path,
        apply: Callable[[], None],
        applicable: Callable[[Path], bool] | None = None,
    ) -> None:
        """Add an inotify watch with callback and optional applicability checker."""
        if applicable is None:
            applicable = (
                partial(_reverse_relative, parent=path) if path.is_dir() else partial(_same_file, original=path)
            )
        self.watched.append(_Watched(name, path, applicable, apply))
        self._inotify.add_recursive_watch(
            path if path.is_dir() else path.parent,
            Mask.MODIFY | Mask.ATTRIB | Mask.DELETE | Mask.CREATE | Mask.MASK_ADD,
        )


def _reverse_relative(path: Path, *, parent: Path) -> bool:
    """Check if the path is a child of the parent."""
    try:
        return path.is_relative_to(parent)
    except OSError:
        # Ignore; probably have temporarily removed parent, and we're about to see MOVE/CREATE events to recreate this
        return False


def _same_file(path: Path, *, original: Path) -> bool:
    """Check if the path is the same as the original path."""
    try:
        return path.samefile(original)
    except OSError:
        # Ignore; probably have temporarily removed the original,
        # and we're about to see MOVE/CREATE event to recreate this
        return False
