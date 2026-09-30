"""Atomic checkpoint load/save for resume after a stop or power loss."""

from __future__ import annotations

import json
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
    path = checkpoint_path(output_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Checkpoint không hợp lệ")
    return Checkpoint.from_dict(data)


def config_matches(checkpoint: Checkpoint, config: JobConfig) -> str | None:
    """Return a Vietnamese reason when resume would change the saved job."""
    size, fingerprint = _fingerprint(config)
    if size != checkpoint.input_size or fingerprint != checkpoint.input_fingerprint:
        return "File nguồn đã thay đổi so với tiến trình đã lưu"
    if Path(checkpoint.input_path).resolve() != config.input_path.resolve():
        return "Đường dẫn file nguồn khác tiến trình đã lưu"
    checks = [
        (checkpoint.file_format, config.file_format, "định dạng"),
        (checkpoint.delimiter, config.delimiter, "dấu phân cách"),
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
    for saved, current, label in checks:
        if saved != current:
            return f"Tùy chọn {label} khác tiến trình đã lưu"
    return None


def _fingerprint(config: JobConfig) -> tuple[int, str]:
    from utils.fingerprint import file_fingerprint

    return file_fingerprint(config.input_path)
