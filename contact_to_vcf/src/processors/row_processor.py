"""Turn one source row into a contact or a typed error. One bad row never raises."""

from __future__ import annotations

from models.records import JobConfig, RawRecord
from processors.phone_normalizer import extract_phones, prepare_name


def process_row(
    record: RawRecord, config: JobConfig
) -> tuple[list[tuple[str, str]], str]:
    """Return every contact in the row. An empty list carries the error reason."""
    if record.error:
        return [], record.error
    if not record.columns or all(cell.strip() == "" for cell in record.columns):
        return [], "Dòng trống"
    if config.name_column >= len(record.columns) or config.phone_column >= len(record.columns):
        return [], "Không đủ cột"
    name, name_error = prepare_name(
        record.columns[config.name_column],
        keep_original=config.keep_original_name,
    )
    if name_error or name is None:
        return [], name_error or "Thiếu tên"
    phones = extract_phones(
        record.columns[config.phone_column],
        normalize=config.normalize_phone,
        vn_to_e164=config.vn_to_e164,
    )
    contacts = [(name, phone.phone) for phone in phones if phone.ok]
    if not contacts:
        reason = phones[0].reason if phones else "Thiếu số điện thoại"
        return [], reason
    return contacts, ""
