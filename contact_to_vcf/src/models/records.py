"""Typed records for jobs, progress, and checkpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PhoneResult:
    ok: bool
    phone: str
    reason: str


@dataclass(frozen=True)
class RawRecord:
    row_number: int
    columns: tuple[str, ...]
    end_offset: int
    error: str | None = None


@dataclass
class JobConfig:
    input_path: Path
    output_dir: Path
    file_format: str
    delimiter: str
    has_header: bool
    name_column: int
    phone_column: int
    contacts_per_file: int = 50000
    dedupe: bool = True
    normalize_phone: bool = True
    vn_to_e164: bool = True
    keep_original_name: bool = True
    split_directories: bool = False
    files_per_batch: int = 1000
    encoding: str = "utf-8-sig"
    chunk_size: int = 5000

    def validate(self) -> None:
        if self.file_format not in {"csv", "xlsx", "txt"}:
            raise ValueError("Định dạng nguồn phải là CSV, XLSX hoặc TXT")
        if self.name_column < 0 or self.phone_column < 0:
            raise ValueError("Chưa chọn cột tên và cột số điện thoại")
        if self.name_column == self.phone_column:
            raise ValueError("Cột tên và cột số điện thoại phải khác nhau")
        if self.contacts_per_file < 1:
            raise ValueError("Số liên hệ mỗi file phải lớn hơn 0")
        if self.chunk_size < 1:
            raise ValueError("Kích thước khối xử lý phải lớn hơn 0")
        if self.files_per_batch < 1:
            raise ValueError("Số file mỗi thư mục phải lớn hơn 0")
        if self.file_format == "csv" and len(self.delimiter) != 1:
            raise ValueError("CSV chỉ hỗ trợ dấu phân cách đúng một ký tự")
        if self.file_format == "txt" and self.delimiter == "":
            raise ValueError("TXT cần dấu phân cách")
        if not self.input_path.is_file():
            raise ValueError("Không tìm thấy file nguồn")
        if self.output_dir == self.input_path:
            raise ValueError("Thư mục xuất không được trùng file nguồn")
        resolved_out = self.output_dir.resolve()
        resolved_in = self.input_path.resolve()
        if resolved_out == resolved_in:
            raise ValueError("Thư mục xuất không được trùng file nguồn")


@dataclass
class Progress:
    processed: int = 0
    total: int | None = None
    valid: int = 0
    invalid: int = 0
    duplicate: int = 0
    exported: int = 0
    files_created: int = 0
    elapsed_sec: float = 0.0
    last_file: str = ""

    @property
    def percent(self) -> float:
        if not self.total:
            return 0.0
        return min(100.0, (self.processed / self.total) * 100.0)

    @property
    def speed(self) -> float:
        if self.elapsed_sec <= 0:
            return 0.0
        return self.processed / self.elapsed_sec

    @property
    def eta_sec(self) -> float | None:
        if not self.total or self.speed <= 0:
            return None
        remaining = self.total - self.processed
        if remaining <= 0:
            return 0.0
        return remaining / self.speed


@dataclass
class Report:
    total_rows: int
    valid: int
    invalid: int
    duplicate: int
    exported: int
    vcf_files: int
    elapsed_sec: float
    average_speed: float

    def render(self) -> str:
        return (
            f"TOTAL ROWS: {_comma(self.total_rows)}\n"
            f"VALID: {_comma(self.valid)}\n"
            f"INVALID: {_comma(self.invalid)}\n"
            f"DUPLICATE: {_comma(self.duplicate)}\n"
            f"EXPORTED: {_comma(self.exported)}\n"
            f"VCF FILES: {_comma(self.vcf_files)}\n"
            f"ELAPSED: {_elapsed(self.elapsed_sec)}\n"
            f"AVERAGE SPEED: {_comma(int(self.average_speed))} rows/s\n"
        )


@dataclass
class JobResult:
    cancelled: bool
    message: str
    progress: Progress
    report: Report | None = None


@dataclass
class FileInspection:
    path: Path
    file_format: str
    encoding: str
    delimiter: str
    has_header: bool
    columns: list[str]
    samples: list[tuple[str, ...]]
    size: int
    fingerprint: str
    estimated_rows: int | None


@dataclass
class Checkpoint:
    version: int
    input_path: str
    input_size: int
    input_fingerprint: str
    file_format: str
    delimiter: str
    has_header: bool
    name_column: int
    phone_column: int
    contacts_per_file: int
    dedupe: bool
    normalize_phone: bool
    vn_to_e164: bool
    keep_original_name: bool
    split_directories: bool
    files_per_batch: int
    encoding: str
    chunk_size: int
    next_offset: int
    next_row: int
    processed: int
    valid: int
    invalid: int
    duplicate: int
    exported: int
    completed_files: int
    current_file_contacts: int
    errors_bytes: int
    elapsed_sec: float
    output_dir: str
    total_rows: int | None
    column_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "input_path": self.input_path,
            "input_size": self.input_size,
            "input_fingerprint": self.input_fingerprint,
            "file_format": self.file_format,
            "delimiter": self.delimiter,
            "has_header": self.has_header,
            "name_column": self.name_column,
            "phone_column": self.phone_column,
            "contacts_per_file": self.contacts_per_file,
            "dedupe": self.dedupe,
            "normalize_phone": self.normalize_phone,
            "vn_to_e164": self.vn_to_e164,
            "keep_original_name": self.keep_original_name,
            "split_directories": self.split_directories,
            "files_per_batch": self.files_per_batch,
            "encoding": self.encoding,
            "chunk_size": self.chunk_size,
            "next_offset": self.next_offset,
            "next_row": self.next_row,
            "processed": self.processed,
            "valid": self.valid,
            "invalid": self.invalid,
            "duplicate": self.duplicate,
            "exported": self.exported,
            "completed_files": self.completed_files,
            "current_file_contacts": self.current_file_contacts,
            "errors_bytes": self.errors_bytes,
            "elapsed_sec": self.elapsed_sec,
            "output_dir": self.output_dir,
            "total_rows": self.total_rows,
            "column_names": self.column_names,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Checkpoint:
        columns = data.get("column_names", [])
        if not isinstance(columns, list):
            columns = []
        total = data.get("total_rows")
        return cls(
            version=int(data["version"]),  # type: ignore[arg-type]
            input_path=str(data["input_path"]),
            input_size=int(data["input_size"]),  # type: ignore[arg-type]
            input_fingerprint=str(data["input_fingerprint"]),
            file_format=str(data["file_format"]),
            delimiter=str(data["delimiter"]),
            has_header=bool(data["has_header"]),
            name_column=int(data["name_column"]),  # type: ignore[arg-type]
            phone_column=int(data["phone_column"]),  # type: ignore[arg-type]
            contacts_per_file=int(data["contacts_per_file"]),  # type: ignore[arg-type]
            dedupe=bool(data["dedupe"]),
            normalize_phone=bool(data["normalize_phone"]),
            vn_to_e164=bool(data["vn_to_e164"]),
            keep_original_name=bool(data["keep_original_name"]),
            split_directories=bool(data["split_directories"]),
            files_per_batch=int(data["files_per_batch"]),  # type: ignore[arg-type]
            encoding=str(data["encoding"]),
            chunk_size=int(data["chunk_size"]),  # type: ignore[arg-type]
            next_offset=int(data["next_offset"]),  # type: ignore[arg-type]
            next_row=int(data["next_row"]),  # type: ignore[arg-type]
            processed=int(data["processed"]),  # type: ignore[arg-type]
            valid=int(data["valid"]),  # type: ignore[arg-type]
            invalid=int(data["invalid"]),  # type: ignore[arg-type]
            duplicate=int(data["duplicate"]),  # type: ignore[arg-type]
            exported=int(data["exported"]),  # type: ignore[arg-type]
            completed_files=int(data["completed_files"]),  # type: ignore[arg-type]
            current_file_contacts=int(data["current_file_contacts"]),  # type: ignore[arg-type]
            errors_bytes=int(data["errors_bytes"]),  # type: ignore[arg-type]
            elapsed_sec=float(data["elapsed_sec"]),  # type: ignore[arg-type]
            output_dir=str(data["output_dir"]),
            total_rows=None if total is None else int(total),  # type: ignore[arg-type]
            column_names=[str(item) for item in columns],
        )


def _comma(value: int) -> str:
    return f"{value:,}"


def _elapsed(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
