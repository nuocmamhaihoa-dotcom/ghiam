"""Stream contacts into vCard files of a fixed size."""

from __future__ import annotations

import os
import re
from pathlib import Path

from exporters.vcf_validator import (
    VcfStructureError,
    count_markers,
    format_card,
    truncate_to_contacts,
    validate_vcf,
)

_NAME = re.compile(r"contacts_(\d+)\.vcf$")


class VcfExporter:
    def __init__(
        self,
        output_dir: Path,
        contacts_per_file: int,
        *,
        split_directories: bool,
        files_per_batch: int,
    ) -> None:
        self.output_dir = output_dir
        self.contacts_per_file = contacts_per_file
        self.split_directories = split_directories
        self.files_per_batch = files_per_batch
        self.completed_files = 0
        self.current_count = 0
        self._handle: object | None = None
        self._path: Path | None = None

    @property
    def files_created(self) -> int:
        return self.completed_files + (1 if self.current_count else 0)

    def prepare_resume(self, completed_files: int, current_contacts: int) -> None:
        self.completed_files = completed_files
        self.current_count = current_contacts
        current_index = completed_files + 1
        for index, path in _existing_files(self.output_dir):
            if index > current_index or (index == current_index and current_contacts == 0):
                path.unlink()
                continue
            if index == current_index:
                truncate_to_contacts(path, current_contacts)
                continue
            if index == completed_files:
                begin, end = count_markers(path)
                if begin != self.contacts_per_file or end != self.contacts_per_file:
                    raise VcfStructureError(
                        f"{path.name} đã đánh dấu hoàn thành nhưng không đủ "
                        f"{self.contacts_per_file} contact"
                    )
        if current_contacts > 0:
            path = self.path_for(current_index)
            if not path.is_file():
                raise VcfStructureError("Thiếu file VCF đang viết dở trong tiến trình đã lưu")
            self._path = path
            self._handle = path.open("ab")

    def add(self, name: str, phone: str) -> Path | None:
        if self._handle is None:
            self._path = self.path_for(self.completed_files + 1)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("wb")
        handle = self._require_handle()
        handle.write(format_card(name, phone).encode("utf-8"))
        self.current_count += 1
        if self.current_count >= self.contacts_per_file:
            return self._finalize()
        return None

    def flush(self) -> None:
        if self._handle is None:
            return
        handle = self._require_handle()
        handle.flush()
        os.fsync(handle.fileno())

    def finish(self) -> Path | None:
        if self.current_count == 0:
            self.close()
            return None
        return self._finalize()

    def close(self) -> None:
        if self._handle is not None:
            self._require_handle().close()
            self._handle = None

    def path_for(self, index: int) -> Path:
        name = f"contacts_{index:05d}.vcf"
        if not self.split_directories:
            return self.output_dir / name
        batch = (index - 1) // self.files_per_batch + 1
        return self.output_dir / f"batch_{batch:04d}" / name

    def _finalize(self) -> Path:
        if self._path is None or self._handle is None:
            raise VcfStructureError("Không có file VCF đang mở")
        path = self._path
        expected = self.current_count
        self.flush()
        self.close()
        validate_vcf(path, expected)
        self.completed_files += 1
        self.current_count = 0
        self._path = None
        return path

    def _require_handle(self):
        handle = self._handle
        if handle is None:
            raise VcfStructureError("File VCF đã đóng")
        return handle


def _existing_files(output_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    candidates = list(output_dir.glob("contacts_*.vcf"))
    candidates.extend(output_dir.glob("batch_*/contacts_*.vcf"))
    for path in candidates:
        match = _NAME.fullmatch(path.name)
        if match is None:
            continue
        found.append((int(match.group(1)), path))
    return found
