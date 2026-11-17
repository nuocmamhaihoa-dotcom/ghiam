"""Start the address-book app on this computer, using the local data folder."""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen


def package_root() -> Path:
    """Folder that contains ``Mo-Danh-Ba.bat``, ``kho`` and ``contact_to_vcf``."""
    return Path(__file__).resolve().parents[3]


def local_kho() -> Path:
    configured = os.environ.get("DANHBA_KHO", "").strip()
    if configured:
        return Path(configured)
    return package_root() / "kho"


def local_host() -> str:
    """This copy never follows a server address. The browser stays on this PC."""
    return "127.0.0.1"


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


def local_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _local_app_is_running(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/kho", timeout=1) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return False
    return data.get("local") is True


def _open_local(url: str) -> None:
    if not url.startswith("http://127.0.0.1:"):
        return
    webbrowser.open(url)


def run(kho: Path, host: str, port: int, *, open_browser: bool) -> None:
    del host
    root = Path(__file__).resolve().parents[1]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.environ["DANHBA_LOCAL"] = "1"
    os.environ["DANHBA_HOST"] = "127.0.0.1"
    require_existing_pool(kho)
    from processors.pool import list_books, pool_total
    from web.server import serve

    if _local_app_is_running(port):
        url = local_url(port)
        print(f"Danh bạ đã chạy trên máy tính này: {url}")
        if open_browser:
            _open_local(url)
        return
    total = pool_total(kho)
    book_count = len(list_books(kho, "all"))
    print(f"Đã nạp {total} số từ {book_count} danh bạ đã làm.")
    print("Số đã có trong kho sẽ không được tạo danh bạ mới.")
    server = None
    bound_port = port
    for candidate in range(port, port + 20):
        try:
            server = serve(kho, host="127.0.0.1", port=candidate)
            bound_port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("Không mở được cổng trên máy tính này.")
    url = local_url(bound_port)
    print(f"Danh bạ đang chạy trên máy tính này: {url}")
    print("Đóng cửa sổ này để tắt phần mềm.")
    if open_browser:
        threading.Timer(0.8, lambda: _open_local(url)).start()
    server.serve_forever()


def main() -> None:
    run(local_kho(), local_host(), local_port(), open_browser=True)


if __name__ == "__main__":
    main()
