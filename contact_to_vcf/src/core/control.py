"""Pause, resume, and cancel signals shared by the engine and the GUI."""

from __future__ import annotations

import threading


class RunControl:
    def __init__(self) -> None:
        self._pause = threading.Event()
        self._pause.set()
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def observe(self, progress: object) -> None:
        """Hook for tests that stop a run after a durable row count."""
        del progress

    def keep_going(self) -> bool:
        while not self._pause.is_set():
            if self._cancel.is_set():
                return False
            self._pause.wait(0.2)
        return not self._cancel.is_set()
