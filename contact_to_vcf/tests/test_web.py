"""The browser app imports phones and serves a phone-sized page."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

from web.server import serve


def test_phone_and_computer_page_and_pool(tmp_path: Path) -> None:
    server = serve(tmp_path / "kho", host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        sample = opener.open(f"http://127.0.0.1:{port}/danhba_test_30.vcf").read().decode()
        assert sample.count("BEGIN:VCARD") == 30
        assert "FN:Test 01" in sample
        assert "TEL;TYPE=CELL:0900000030" in sample
        page = opener.open(f"http://127.0.0.1:{port}/").read().decode()
        assert "Vào phần mềm" in page
        assert "Nạp vào kho" in page
        assert "min-width: 900px" in page
        assert "width=device-width" in page

        login = opener.open(
            Request(
                f"http://127.0.0.1:{port}/api/login",
                data=json.dumps({"user": "danhba", "password": "danhba123"}).encode(),
                headers={"Content-Type": "application/json"},
            )
        )
        assert json.load(login)["ok"] is True

        body, content_type = _form({"file": ("people.csv", b"ten,so\nAn,0901234567\nBinh,0901234567\n")})
        inspected = json.load(
            opener.open(
                Request(
                    f"http://127.0.0.1:{port}/api/inspect",
                    data=body,
                    headers={"Content-Type": content_type},
                )
            )
        )
        assert inspected["phone_column"] == 1
        imported = json.load(
            opener.open(
                Request(
                    f"http://127.0.0.1:{port}/api/import",
                    data=json.dumps(
                        {
                            "upload_id": inspected["upload_id"],
                            "phone_column": 1,
                            "has_header": True,
                            "delimiter": ",",
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
            )
        )
        assert imported["added"] == 1
        assert imported["duplicate"] == 1
        books = json.load(opener.open(f"http://127.0.0.1:{port}/api/books"))
        assert books["total"] == 1
        downloaded = opener.open(
            f"http://127.0.0.1:{port}/api/books/{books['books'][0]['id']}/download"
        )
        card = downloaded.read().decode()
        assert "FN:0901234567" in card
        assert "TEL;TYPE=CELL:0901234567" in card
    finally:
        server.shutdown()


def _form(files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----danhba"
    chunks: list[bytes] = []
    for name, (filename, payload) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n\r\n'.encode()
        )
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
