"""Tests for watcher functionality."""

import logging
import threading
from collections import defaultdict
from functools import partial
from time import sleep
from typing import TYPE_CHECKING

import pytest
from asyncinotify import Mask

import pyk8sdev.watcher

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


pyk8sdev.watcher.TIMEOUT = 0.1


def _queue_empty(watcher: pyk8sdev.watcher.Watcher) -> None:
    """Block until watcher has nothing left in the queue."""
    sleep(2 * pyk8sdev.watcher.TIMEOUT)
    while not watcher._event_queue.empty():
        sleep(pyk8sdev.watcher.TIMEOUT)


@pytest.fixture
def watcher() -> Generator[pyk8sdev.watcher.Watcher]:
    """Clean up Watcher singleton after each test."""
    yield pyk8sdev.watcher.Watcher()
    pyk8sdev.watcher.Watcher._destroy()


def test_init(watcher: pyk8sdev.watcher.Watcher) -> None:
    """Ensure Watcher class can be initialised."""
    assert not watcher._inotify.watches
    assert not watcher._is_running.is_set()
    assert len(watcher.watched) == 0
    assert watcher._event_queue.empty()


def test_singleton() -> None:
    """Ensure Watcher is a singleton."""
    w1 = pyk8sdev.watcher.Watcher()
    w2 = pyk8sdev.watcher.Watcher()
    assert w1 is w2


def test_lifecycle(watcher: pyk8sdev.watcher.Watcher) -> None:
    """Ensure Watcher can be started and stopped."""
    assert not watcher._inotify.watches
    watcher.start()
    assert watcher._is_running.is_set()
    sleep(2 * pyk8sdev.watcher.TIMEOUT)
    watcher.stop()
    assert not watcher._is_running.is_set()
    sleep(2 * pyk8sdev.watcher.TIMEOUT)


def test_consumes_events(watcher: pyk8sdev.watcher.Watcher, caplog: pytest.LogCaptureFixture) -> None:
    """Ensure Watcher consumes events and doesn't get stuck if there are no files to watch."""
    assert not watcher._inotify.watches
    with caplog.at_level(logging.DEBUG):
        watcher.start()
        assert watcher._event_queue.empty()
        watcher._event_queue.put(
            pyk8sdev.watcher._QueuedInotifyEvent(
                cookie=0,
                mask=Mask.IGNORED,
                name=None,
                path=None,
            )
        )
        watcher._event_queue.join()
        assert watcher._event_queue.empty()
        watcher.stop()
        sleep(2 * pyk8sdev.watcher.TIMEOUT)
    assert (pyk8sdev.watcher.__name__, logging.DEBUG, "Created IGNORED event for None") in caplog.record_tuples
    assert (pyk8sdev.watcher.__name__, logging.DEBUG, "Completed IGNORED event for None") not in caplog.record_tuples
    assert (pyk8sdev.watcher.__name__, logging.INFO, "Check loop completed") in caplog.record_tuples
    assert (pyk8sdev.watcher.__name__, logging.INFO, "Watch loop completed") in caplog.record_tuples


def test_detects_file_events(
    watcher: pyk8sdev.watcher.Watcher,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Ensure Watcher detects inotify file changes."""
    call_count = 0
    expected_calls = 0
    lock = threading.Lock()
    assert not watcher._inotify.watches

    def callback() -> None:
        """Mock callback to ensure it gets run."""
        nonlocal call_count
        with lock:
            call_count += 1

    with caplog.at_level(logging.DEBUG):
        watcher.start()
        watcher.add_watch("test", tmp_path, callback)

        (tmp_path / "new_file").touch()
        expected_calls += 1
        _queue_empty(watcher)
        assert call_count == expected_calls

        (tmp_path / "new_file").write_text("test")
        expected_calls += 1
        _queue_empty(watcher)
        assert call_count == expected_calls

        (tmp_path / "new_file").unlink()
        expected_calls += 1
        _queue_empty(watcher)
        assert call_count == expected_calls

        watcher.stop()
        _queue_empty(watcher)

    assert (
        pyk8sdev.watcher.__name__,
        logging.DEBUG,
        f"Created CREATE event for {(tmp_path / 'new_file')}",
    ) in caplog.record_tuples
    assert (
        pyk8sdev.watcher.__name__,
        logging.DEBUG,
        f"Created MODIFY event for {(tmp_path / 'new_file')}",
    ) in caplog.record_tuples
    assert (
        pyk8sdev.watcher.__name__,
        logging.DEBUG,
        f"Created DELETE event for {(tmp_path / 'new_file')}",
    ) in caplog.record_tuples
    assert (
        pyk8sdev.watcher.__name__,
        logging.DEBUG,
        f"Completed DELETE event for {(tmp_path / 'new_file')}",
    ) in caplog.record_tuples


def test_file_replaced(watcher: pyk8sdev.watcher.Watcher, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Ensure watch remains if file is removed and recreated."""
    assert not watcher._inotify.watches
    call_count = 0
    expected_calls = 0
    lock = threading.Lock()
    f1 = tmp_path / "f1"
    f1.touch()

    def callback() -> None:
        """Mock callback to ensure it gets run."""
        nonlocal call_count
        with lock:
            call_count += 1

    with caplog.at_level(logging.DEBUG):
        watcher.start()
        watcher.add_watch("test", f1, callback)

        _queue_empty(watcher)
        assert f1.exists()
        assert call_count == expected_calls

        f1.write_text("test start")
        expected_calls += 1
        _queue_empty(watcher)
        assert f1.exists()
        assert call_count == expected_calls

        f1.unlink()
        # Files won't exist, so applicable will return false and callback won't be called
        sleep(5 * pyk8sdev.watcher.TIMEOUT)
        assert not f1.exists()
        assert call_count == expected_calls

        f1.touch()
        expected_calls += 1
        _queue_empty(watcher)
        assert f1.exists()
        assert call_count == expected_calls

        f1.write_text("test end")
        expected_calls += 1
        _queue_empty(watcher)
        assert f1.exists()
        assert call_count == expected_calls

        watcher.stop()


def test_detects_multiple_file_events(watcher: pyk8sdev.watcher.Watcher, tmp_path: Path) -> None:
    """Ensure Watcher detects inotify file changes for multiple watchers."""
    assert not watcher._inotify.watches
    call_count: dict[Path, int] = defaultdict(int)
    lock = threading.Lock()
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    f1 = tmp_path / "f1"
    d1.mkdir()
    d2.mkdir()
    f1.touch()

    def callback(*, root_path: Path) -> None:
        """Mock callback to ensure it gets run."""
        with lock:
            call_count[root_path] += 1
        # Simulate long processing time
        sleep(2 * pyk8sdev.watcher.TIMEOUT)

    watcher.start()
    for f in (d1, d2, f1):
        watcher.add_watch("test", f, partial(callback, root_path=f))
    write_count = 100
    for i in range(write_count):
        f1.write_text(f"test{i}")
        sleep(pyk8sdev.watcher.TIMEOUT / 4)
    _queue_empty(watcher)
    assert call_count[f1] < write_count  # at least some of the events should be skipped due to the processing time

    (d1 / "test_move").touch()
    sleep(2 * pyk8sdev.watcher.TIMEOUT)  # wait so we get distinct events
    (d1 / "test_move").rename(d2 / "test_move")
    _queue_empty(watcher)
    watcher.stop()
    assert call_count[d1] > 1
    assert call_count[d2] == 1
