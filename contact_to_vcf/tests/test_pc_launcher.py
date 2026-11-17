"""The PC package starts on this computer and keeps the local phone pool."""

from __future__ import annotations

from pathlib import Path

import pytest

from web.pc_launcher import local_host, local_kho, local_port, require_existing_pool


def test_pc_launcher_uses_this_computer_and_the_bundled_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DANHBA_KHO", raising=False)
    monkeypatch.setenv("DANHBA_HOST", "14.225.198.188")
    monkeypatch.delenv("DANHBA_PORT", raising=False)
    assert local_host() == "127.0.0.1"
    assert local_port() == 8080
    monkeypatch.setenv("DANHBA_KHO", str(tmp_path))
    assert local_kho() == tmp_path
    root = Path(__file__).resolve().parents[1]
    text = (root / "Mo-Danh-Ba.bat").read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "DANHBA_KHO=%~dp0kho" in text
    assert "DANHBA_LOCAL=1" in text
    assert "Danh ba.lnk" in text
    assert "14.225" not in text
    installer = (root / "scripts" / "build_pc_installer.py").read_text(encoding="utf-8")
    assert 'RunProgram="cmd.exe /c Mo-Danh-Ba.bat"' in installer
    assert "14.225" not in installer
    page = (root / "src" / "web" / "index.html").read_text(encoding="utf-8")
    assert "14.225" not in page
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
        assert "Không kết nối máy chủ" in page
        assert "14.225" not in page
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


def test_local_mode_serves_the_pool_without_opening_a_server(tmp_path: Path, monkeypatch) -> None:
    import json
    import threading
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from web.pc_launcher import _open_local
    from web.server import serve

    opened: list[str] = []
    monkeypatch.setattr("web.pc_launcher.webbrowser.open", opened.append)
    _open_local("http://14.225.198.188/")
    _open_local("http://127.0.0.1:8080/")
    assert opened == ["http://127.0.0.1:8080/"]

    monkeypatch.setenv("DANHBA_LOCAL", "1")
    server = serve(tmp_path, host="127.0.0.1", port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    port = server.server_address[1]
    try:
        status = json.load(urlopen(f"http://127.0.0.1:{port}/api/kho", timeout=5))
        assert status["local"] is True
        books = json.load(urlopen(f"http://127.0.0.1:{port}/api/books", timeout=5))
        assert books["books"] == []
        page = urlopen(f"http://127.0.0.1:{port}/tai", timeout=5).read().decode()
        assert "Không kết nối máy chủ" in page
        assert "DanhBa-Setup.exe" not in page
        assert "14.225" not in page
        monkeypatch.delenv("DANHBA_LOCAL", raising=False)
        try:
            urlopen(f"http://127.0.0.1:{port}/api/books", timeout=5)
            raise AssertionError("the server copy must still ask for login")
        except HTTPError as exc:
            assert exc.code == 401
    finally:
        server.shutdown()
