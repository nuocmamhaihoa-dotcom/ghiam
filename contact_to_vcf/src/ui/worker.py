"""Background conversion so the window stays responsive."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.control import RunControl
from models.records import JobConfig
from parsers.detect import inspect_source
from processors.pool import ImportStats, import_file
from processors.streaming_engine import execute


class ConversionWorker(QThread):
    progress = Signal(object)
    message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, config: JobConfig, mode: str, resume: bool) -> None:
        super().__init__()
        self.config = config
        self.mode = mode
        self.resume = resume
        self.control = RunControl()

    def run(self) -> None:
        try:
            result = execute(
                self.config,
                mode=self.mode,
                resume=self.resume,
                control=self.control,
                on_progress=self._on_progress,
                on_message=self.message.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)

    def _on_progress(self, progress: object) -> None:
        self.progress.emit(replace(progress))  # type: ignore[type-var]


class PoolImportWorker(QThread):
    """Load phone numbers into the persistent pool without blocking the window."""

    progressed = Signal(int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        folder: Path,
        source: Path,
        file_format: str,
        delimiter: str,
        has_header: bool,
        encoding: str,
        phone_column: int,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.source = source
        self.file_format = file_format
        self.delimiter = delimiter
        self.has_header = has_header
        self.encoding = encoding
        self.phone_column = phone_column
        self._stop = False

    def request_cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            stats: ImportStats = import_file(
                self.folder,
                self.source,
                file_format=self.file_format,
                delimiter=self.delimiter,
                has_header=self.has_header,
                encoding=self.encoding,
                phone_column=self.phone_column,
                should_stop=lambda: self._stop,
                on_progress=self.progressed.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(stats)


class InspectWorker(QThread):
    """Read columns and count lines away from the UI thread."""

    finished_ok = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        token: int,
        path: Path,
        file_format: str,
        delimiter: str | None,
        has_header: bool,
    ) -> None:
        super().__init__()
        self.token = token
        self.path = path
        self.file_format = file_format
        self.delimiter = delimiter
        self.has_header = has_header

    def run(self) -> None:
        try:
            inspection = inspect_source(
                self.path,
                file_format=self.file_format,
                delimiter=self.delimiter,
                has_header=self.has_header,
            )
        except Exception as exc:
            self.failed.emit(self.token, str(exc))
            return
        self.finished_ok.emit(self.token, inspection)
