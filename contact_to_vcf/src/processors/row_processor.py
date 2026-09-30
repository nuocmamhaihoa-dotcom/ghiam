"""Turn one source row into a contact or a typed error. One bad row never raises."""

from __future__ import annotations

from models.records import JobConfig, RawRecord
from processors.phone_normalizer import normalize_phone, prepare_name


def process_row(
    record: RawRecord, config: JobConfig
) -> tuple[tuple[str, str] | None, str]:
    if record.error:
        return None, record.error
    if not record.columns or all(cell.strip() == "" for cell in record.columns):
        return None, "Dòng trống"
    if config.name_column >= len(record.columns) or config.phone_column >= len(record.columns):
        return None, "Không đủ cột"
    name, name_error = prepare_name(
        record.columns[config.name_column],
        keep_original=config.keep_original_name,
    )
    if name_error or name is None:
        return None, name_error or "Thiếu tên"
    phone = normalize_phone(
        record.columns[config.phone_column],
        normalize=config.normalize_phone,
        vn_to_e164=config.vn_to_e164,
    )
    if not phone.ok:
        return None, phone.reason
    return (name, phone.phone), ""
