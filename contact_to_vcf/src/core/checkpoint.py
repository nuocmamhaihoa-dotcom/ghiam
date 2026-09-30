"""Atomic checkpoint load/save for resume after a stop or power loss."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from models.records import Checkpoint, JobConfig

CHECKPOINT_VERSION = 1


def state_dir(output_dir: Path) -> Path:
    return output_dir / ".contact_to_vcf"


def checkpoint_path(output_dir: Path) -> Path:
    return state_dir(output_dir) / "checkpoint.json"


def dedupe_path(output_dir: Path) -> Path:
    return state_dir(output_dir) / "dedupe.sqlite"


def save_checkpoint(checkpoint: Checkpoint) -> None:
    output = Path(checkpoint.output_dir)
    folder = state_dir(output)
    folder.mkdir(parents=True, exist_ok=True)
    target = checkpoint_path(output)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def load_checkpoint(output_dir: Path) -> Checkpoint | None:
    payload = _read_sqlite_payload(dedupe_path(output_dir))
    if payload is None:
        path = checkpoint_path(output_dir)
        if not path.is_file():
            return None
        payload = path.read_text(encoding="utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Checkpoint không hợp lệ")
    return Checkpoint.from_dict(data)


def clear_resume_state(output_dir: Path) -> None:
    """Drop resume files after a finished job. Exported VCF files stay in place."""
    folder = state_dir(output_dir)
    if folder.exists():
        shutil.rmtree(folder)


def config_matches(checkpoint: Checkpoint, config: JobConfig) -> str | None:
    """Return a Vietnamese reason when resume would change the saved job."""
    size, fingerprint = _fingerprint(config)
    if size != checkpoint.input_size or fingerprint != checkpoint.input_fingerprint:
        return "File nguồn đã thay đổi so với tiến trình đã lưu"
    if Path(checkpoint.input_path).resolve() != config.input_path.resolve():
        return "Đường dẫn file nguồn khác tiến trình đã lưu"
    checks: list[tuple[object, object, str]] = [
        (checkpoint.file_format, config.file_format, "định dạng"),
    ]
    if checkpoint.file_format != "xlsx":
        checks.append((checkpoint.delimiter, config.delimiter, "dấu phân cách"))
    checks.extend(
        [
            (checkpoint.has_header, config.has_header, "dòng tiêu đề"),
            (checkpoint.name_column, config.name_column, "cột tên"),
            (checkpoint.phone_column, config.phone_column, "cột số điện thoại"),
            (checkpoint.contacts_per_file, config.contacts_per_file, "số liên hệ mỗi file"),
            (checkpoint.dedupe, config.dedupe, "lọc trùng"),
            (checkpoint.normalize_phone, config.normalize_phone, "chuẩn hóa số"),
            (checkpoint.vn_to_e164, config.vn_to_e164, "chuyển +84"),
            (checkpoint.keep_original_name, config.keep_original_name, "giữ tên gốc"),
            (checkpoint.split_directories, config.split_directories, "chia thư mục"),
            (checkpoint.encoding, config.encoding, "bảng mã"),
        ]
    )
    for saved, current, label in checks:
        if saved != current:
            return f"Tùy chọn {label} khác tiến trình đã lưu"
    return None


def _read_sqlite_payload(path: Path) -> str | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'job_state'"
        ).fetchone()
        if exists is None:
            return None
        row = connection.execute("SELECT payload FROM job_state WHERE id = 1").fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _fingerprint(config: JobConfig) -> tuple[int, str]:
    from utils.fingerprint import file_fingerprint

    return file_fingerprint(config.input_path)
