"""Khôi phục cột địa chỉ từ file Excel đã nạp, không đụng cột khác."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator

from control_plane import db
from control_plane.customers import (
    extract_phone,
    prefer_import_address,
    restore_stored_addresses,
)
from control_plane.excel_import import iter_mapped_file, mapping_for_file
from control_plane.vietnam_places import cell_is_short_admin_only

IMPORT_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt"}


def iter_import_sources(
    data_dir: Path,
    include_uploads: bool = False,
    include_imports: bool = True,
) -> Iterator[tuple[str, Path]]:
    imports = data_dir / "imports"
    uploads = data_dir / "khach-uploads"
    if include_imports and imports.is_dir():
        for path in sorted(imports.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMPORT_SUFFIXES:
                yield path.name, path
    if include_uploads and uploads.is_dir():
        for meta_path in sorted(uploads.glob("*/meta.json")):
            data_path = meta_path.parent / "data"
            if not data_path.is_file():
                continue
            try:
                name = str(json.loads(meta_path.read_text(encoding="utf-8")).get("name") or data_path.name)
            except (OSError, ValueError, TypeError):
                name = data_path.name
            yield name, data_path


class ImportAddressStore:
    """Bảng tạm phone_key -> địa chỉ gốc, không giữ cả kho trong RAM."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA temp_store=FILE;")
        self.conn.execute("PRAGMA cache_size=-8000;")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS src(phone_key TEXT PRIMARY KEY, address TEXT NOT NULL)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, key: str, default: str = "") -> str:
        if not key:
            return default
        row = self.conn.execute("SELECT address FROM src WHERE phone_key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def get_many(self, keys: list[str]) -> dict[str, str]:
        wanted = [key for key in keys if key]
        if not wanted:
            return {}
        found: dict[str, str] = {}
        for index in range(0, len(wanted), 400):
            chunk = wanted[index : index + 400]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT phone_key, address FROM src WHERE phone_key IN ({marks})",
                chunk,
            )
            for key, address in rows:
                found[str(key)] = str(address or "")
        return found

    def put(self, key: str, address: str) -> bool:
        if not key or not address or cell_is_short_admin_only(address):
            return False
        current = self.get(key)
        chosen = prefer_import_address(current, address)
        if not chosen or chosen == current:
            return False
        self.conn.execute(
            "INSERT INTO src(phone_key, address) VALUES(?, ?) "
            "ON CONFLICT(phone_key) DO UPDATE SET address=excluded.address",
            (key, chosen),
        )
        return True

    def commit(self) -> None:
        self.conn.commit()

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM src").fetchone()[0] or 0)


def _mapping_cache_key(filename: str) -> str:
    name = Path(filename).name.casefold()
    if name.startswith("delivery_export_"):
        return "delivery_export"
    if "tổng hợp" in name or "tong hop" in name:
        return "tong_hop"
    return name


def collect_import_addresses(
    data_dir: Path,
    store: ImportAddressStore,
    progress: Callable[[dict[str, int]], None] | None = None,
    include_uploads: bool = False,
    include_imports: bool = True,
) -> dict[str, int]:
    files = 0
    rows = 0
    kept = 0
    skipped = 0
    errors = 0
    dupes = 0
    seen_files: set[tuple[str, int]] = set()
    mappings: dict[str, dict[str, Any]] = {}
    skip_tong_hop = store.count() >= 1_000_000
    for filename, path in iter_import_sources(
        Path(data_dir),
        include_uploads=include_uploads,
        include_imports=include_imports,
    ):
        fingerprint = (Path(filename).name.casefold(), path.stat().st_size)
        if fingerprint in seen_files:
            dupes += 1
            continue
        seen_files.add(fingerprint)
        if skip_tong_hop and _mapping_cache_key(filename) == "tong_hop":
            dupes += 1
            continue
        files += 1
        try:
            cache_key = _mapping_cache_key(filename)
            mapping = mappings.get(cache_key)
            if mapping is None:
                mapping = mapping_for_file({}, filename, path)
                mappings[cache_key] = mapping
            _width, mapped = iter_mapped_file(filename, path, mapping, None)
            dirty = 0
            for row in mapped:
                rows += 1
                phone = extract_phone(row.get("phone") or "") or str(row.get("phone") or "")
                key = db.phone_key(phone)
                address = str(row.get("address") or "").strip()
                if not key or not address:
                    continue
                if store.put(key, address):
                    kept += 1
                    dirty += 1
                else:
                    skipped += 1
            if dirty:
                store.commit()
        except Exception:
            errors += 1
        if progress is not None and files % 20 == 0:
            progress(
                {
                    "files": files,
                    "rows": rows,
                    "kept": kept,
                    "skipped": skipped,
                    "errors": errors,
                    "dupes": dupes,
                }
            )
    store.commit()
    if progress is not None:
        progress(
            {
                "files": files,
                "rows": rows,
                "kept": kept,
                "skipped": skipped,
                "errors": errors,
                "dupes": dupes,
            }
        )
    return {
        "files": files,
        "rows": rows,
        "kept": kept,
        "skipped": skipped,
        "errors": errors,
        "dupes": dupes,
        "phones": store.count(),
    }


def collect_from_customer_db(
    source_db: Path,
    store: ImportAddressStore,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """Lấy địa chỉ gốc từ bản sao kho cũ. Chỉ thêm khi dài hơn bản đang có."""
    conn = sqlite3.connect(f"file:{Path(source_db)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    seen = 0
    kept = 0
    skipped = 0
    try:
        conn.execute("PRAGMA busy_timeout=60000;")
        top = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM customers").fetchone()[0] or 0)
        start = 0
        size = 20000
        has_phone_key = any(
            str(row[1]) == "phone_key" for row in conn.execute("PRAGMA table_info(customers)")
        )
        select = (
            "SELECT phone, phone_key, address FROM customers WHERE rowid > ? AND rowid <= ? AND address != ''"
            if has_phone_key
            else "SELECT phone, address FROM customers WHERE rowid > ? AND rowid <= ? AND address != ''"
        )
        while start < top:
            rows = conn.execute(select, (start, start + size)).fetchall()
            dirty = 0
            for row in rows:
                seen += 1
                address = str(row["address"] or "").strip()
                if has_phone_key:
                    key = str(row["phone_key"] or "") or db.phone_key(row["phone"])
                else:
                    key = db.phone_key(row["phone"])
                if store.put(key, address):
                    kept += 1
                    dirty += 1
                else:
                    skipped += 1
            if dirty:
                store.commit()
            start += size
            if progress is not None:
                progress({"rowid": start, "top": top, "seen": seen, "kept": kept, "skipped": skipped})
    finally:
        conn.close()
    store.commit()
    return {"seen": seen, "kept": kept, "skipped": skipped, "phones": store.count()}


def restore_addresses_from_imports(
    db_path: Any,
    data_dir: Path,
    store_path: Path,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    store = ImportAddressStore(store_path)
    try:
        collect_stats = collect_import_addresses(data_dir, store, progress=progress, include_uploads=False)
        apply_stats = restore_stored_addresses(db_path, store, progress=progress)
    finally:
        store.close()
    return {"collect": collect_stats, "apply": apply_stats}
