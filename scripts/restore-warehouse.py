#!/usr/bin/env python3
"""Nạp lại kho tổng từ file đã tải về máy.

Ví dụ:
  python3 scripts/restore-warehouse.py --db control_data/server.db --file kho-tong.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from control_plane.warehouse_store import restore_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp file kho tổng đã tải về vào server.db.")
    parser.add_argument("--db", required=True, help="Đường dẫn server.db đang dùng")
    parser.add_argument("--file", required=True, help="File kho-tong.db đã tải về máy")
    args = parser.parse_args()
    db_path = Path(args.db)
    source = Path(args.file)
    if not source.is_file():
        raise SystemExit(f"Không thấy file: {source}")
    incoming = db_path.with_name(db_path.name + ".incoming")
    shutil.copy2(source, incoming)
    stats = restore_snapshot(db_path, incoming)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM warehouse_contacts").fetchone()[0]
    print(f"Đã nạp {db_path} ({count} dòng, {stats['bytes']} byte)")


if __name__ == "__main__":
    main()
