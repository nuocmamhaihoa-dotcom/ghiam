#!/usr/bin/env python3
"""Lấy lại kho tổng từ bản gương hoặc từ sổ ghi.

Ba bản lưu:
- server.db: kho đang dùng
- warehouse_mirror.db: bản gương, chép sau mỗi lần ghi
- warehouse.journal.jsonl: sổ chỉ thêm, dựng lại được từng dòng

Ví dụ:
  python3 scripts/restore-warehouse.py --db control_data/server.db --from mirror
  python3 scripts/restore-warehouse.py --db control_data/server.db --from journal
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from control_plane.warehouse_store import _copy_paths, rebuild_from_journal


def main() -> None:
    parser = argparse.ArgumentParser(description="Phục hồi kho tổng từ bản gương hoặc sổ ghi.")
    parser.add_argument("--db", required=True, help="Đường dẫn server.db đang dùng")
    parser.add_argument("--from", dest="source", choices=("mirror", "journal"), required=True)
    parser.add_argument("--replace", action="store_true", help="Ghi đè server.db bằng bản phục hồi")
    args = parser.parse_args()
    db_path = Path(args.db)
    mirror, journal, _pos = _copy_paths(db_path)
    restored = db_path.with_name("server.restored.db")
    if args.source == "mirror":
        if not mirror.is_file():
            raise SystemExit(f"Không thấy bản gương: {mirror}")
        shutil.copy2(mirror, restored)
    else:
        if not journal.is_file():
            raise SystemExit(f"Không thấy sổ ghi: {journal}")
        applied = rebuild_from_journal(restored, journal)
        print(f"Đã dựng {applied} thay đổi từ sổ ghi.")
    with sqlite3.connect(restored) as conn:
        count = conn.execute("SELECT COUNT(*) FROM warehouse_contacts").fetchone()[0]
    print(f"Bản phục hồi: {restored} ({count} dòng)")
    if args.replace:
        backup = db_path.with_name("server.before-restore.db")
        if db_path.is_file():
            shutil.copy2(db_path, backup)
            print(f"Đã chép kho hiện tại sang {backup}")
        shutil.copy2(restored, db_path)
        print(f"Đã ghi đè {db_path}")


if __name__ == "__main__":
    main()
