"""The PC package starts on this computer and keeps the local phone pool."""

from __future__ import annotations

from pathlib import Path

import pytest

from web.pc_launcher import local_host, local_kho, local_port, require_existing_pool


def test_pc_launcher_uses_this_computer_and_the_bundled_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DANHBA_KHO", raising=False)
    monkeypatch.delenv("DANHBA_HOST", raising=False)
    monkeypatch.delenv("DANHBA_PORT", raising=False)
    assert local_host() == "127.0.0.1"
    assert local_port() == 8080
    monkeypatch.setenv("DANHBA_KHO", str(tmp_path))
    assert local_kho() == tmp_path
    bat = Path(__file__).resolve().parents[1] / "Mo-Danh-Ba.bat"
    text = bat.read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "DANHBA_KHO=%~dp0kho" in text
    assert "14.225" not in text
    with pytest.raises(SystemExit):
        require_existing_pool(tmp_path)
    database = tmp_path / "kho.sqlite"
    database.write_bytes(b"")
    assert require_existing_pool(tmp_path) == database


def test_pc_launcher_serves_the_local_pool(tmp_path: Path) -> None:
    import json
    import threading
    from urllib.request import Request, urlopen

    from web.server import serve

    server = serve(tmp_path, host="127.0.0.1", port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_address[1]
    try:
        page = urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "Danh bạ" in page
        assert "4.000" in page
        assert "Số đã tạo danh bạ sẽ không bị tạo lại." in page
        assert "không cần máy chủ" in page
        status = json.load(urlopen(f"http://127.0.0.1:{port}/api/kho", timeout=5))
        assert status == {"total": 0, "books": 0}
        login = urlopen(
            Request(
                f"http://127.0.0.1:{port}/api/login",
                data=json.dumps({"user": "danhba", "password": "danhba123"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=5,
        )
        assert json.load(login)["ok"] is True
    finally:
        server.shutdown()
