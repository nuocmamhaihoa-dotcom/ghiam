"""Start the address-book app on this computer, using the local data folder."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path


def package_root() -> Path:
    """Folder that contains ``Mo-Danh-Ba.bat``, ``kho`` and ``contact_to_vcf``."""
    return Path(__file__).resolve().parents[3]


def local_kho() -> Path:
    configured = os.environ.get("DANHBA_KHO", "").strip()
    if configured:
        return Path(configured)
    return package_root() / "kho"


def local_host() -> str:
    return os.environ.get("DANHBA_HOST", "127.0.0.1").strip() or "127.0.0.1"


def local_port() -> int:
    raw = os.environ.get("DANHBA_PORT", "8080").strip() or "8080"
    return int(raw)


def require_existing_pool(kho: Path) -> Path:
    """Use the phone pool shipped with the app. Never start from an empty one."""
    database = kho / "kho.sqlite"
    if not database.is_file():
        print("Không thấy kho số đã làm. Hãy giải nén đủ thư mục kho cạnh file Mo-Danh-Ba.bat.")
        print(f"Đang tìm: {database}")
        raise SystemExit(1)
    return database


def run(kho: Path, host: str, port: int, *, open_browser: bool) -> None:
    root = Path(__file__).resolve().parents[1]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    require_existing_pool(kho)
    from processors.pool import list_books, pool_total
    from web.server import serve

    total = pool_total(kho)
    book_count = len(list_books(kho, "all"))
    print(f"Đã nạp {total} số từ {book_count} danh bạ đã làm.")
    print("Số đã có trong kho sẽ không được tạo danh bạ mới.")
    server = serve(kho, host=host, port=port)
    bound_port = int(server.server_address[1])
    url = f"http://{host}:{bound_port}/"
    print(f"Danh bạ đang chạy tại {url}")
    print("Đóng cửa sổ này để tắt phần mềm.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    server.serve_forever()


def main() -> None:
    run(local_kho(), local_host(), local_port(), open_browser=True)


if __name__ == "__main__":
    main()
