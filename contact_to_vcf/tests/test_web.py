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
        sample_response = opener.open(f"http://127.0.0.1:{port}/danhba_test_30.vcf")
        assert sample_response.headers.get("Content-Type") == "text/x-vcard"
        sample = sample_response.read().decode()
        assert sample.count("BEGIN:VCARD") == 30
        assert "FN:Test 01" in sample
        assert "TEL;TYPE=CELL:+84900000030" in sample
        exact = opener.open(f"http://127.0.0.1:{port}/test_10_contacts.vcf").read()
        assert exact.count(b"BEGIN:VCARD") == 10
        assert b"TEL;TYPE=CELL:+84900000001\r\n" in exact
        page = opener.open(f"http://127.0.0.1:{port}/").read().decode()
        assert "Vào phần mềm" in page
        assert "Tạo danh bạ" in page
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
        assert "TEL;TYPE=CELL:+84901234567" in card

        auto_body, auto_type = _form({"file": ("so.txt", "0901111111\n0902222222\n".encode())})
        automatic = json.load(
            opener.open(
                Request(
                    f"http://127.0.0.1:{port}/api/import-now",
                    data=auto_body,
                    headers={"Content-Type": auto_type},
                )
            )
        )
        assert automatic["added"] == 2
        assert automatic["pending"]
        auto_card = opener.open(
            f"http://127.0.0.1:{port}/api/books/{automatic['pending'][0]}/download"
        ).read().decode()
        assert "FN:0901111111" in auto_card
        assert "TEL;TYPE=CELL:+84901111111" in auto_card

        other_body, other_type = _form(
            {"file": ("danh_sach.dat", "0912345678\n0987654321\n".encode())}
        )
        other = json.load(
            opener.open(
                Request(
                    f"http://127.0.0.1:{port}/api/import-now",
                    data=other_body,
                    headers={"Content-Type": other_type},
                )
            )
        )
        assert other["added"] == 2

        star = (
            "------danhba\r\n"
            "Content-Disposition: form-data; name=\"file\"; "
            "filename*=UTF-8''New%20Text%20Document%20%284%29.txt\r\n"
            "\r\n"
            "0903333333\r\n"
            "------danhba--\r\n"
        ).encode()
        named = json.load(
            opener.open(
                Request(
                    f"http://127.0.0.1:{port}/api/import-now",
                    data=star,
                    headers={"Content-Type": "multipart/form-data; boundary=----danhba"},
                )
            )
        )
        assert named["added"] == 1
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
