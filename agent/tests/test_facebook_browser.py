"""Chromium thật đọc HTML comment công khai được phục vụ cục bộ.

Không mở Facebook, không đăng nhập, không giải captcha. Bài kiểm tra chứng minh bộ đọc lấy đúng
nội dung, tác giả, thời điểm và lượt thích từ DOM mà trình duyệt trả về, rồi đi tiếp trang
"Xem thêm bình luận" mà không bấm link đăng nhập.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from commentscope_agent.browser import INSTALL_HINT
from commentscope_agent.reader import ReadOptions, read_public_facebook
from tests.test_facebook import NOW, PAGE, PAGE_TWO


@pytest.fixture(scope="module", autouse=True)
def _chromium_installed() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            playwright.chromium.launch().close()
        except sync_api.Error as exc:
            if "Executable doesn't exist" not in exc.message:
                raise
            pytest.skip(f"Chưa tải Chromium cho Playwright: {INSTALL_HINT}")


def _serve(pages: dict[str, str]) -> tuple[ThreadingHTTPServer, list[str]]:
    requested: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            requested.append(path)
            body = pages.get(path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, requested


async def test_chromium_reads_public_comments_and_does_not_open_the_login_link() -> None:
    server, requested = _serve({"/post": PAGE, "/story.php": PAGE_TWO})
    base = f"http://127.0.0.1:{server.server_address[1]}"
    async_api = pytest.importorskip("playwright.async_api")
    try:
        async with async_api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                result = await read_public_facebook(
                    page,
                    f"{base}/post",
                    ReadOptions(max_comments=10, time_budget_sec=30, now=NOW),
                )
            finally:
                await browser.close()
    finally:
        server.shutdown()

    assert result.outcome == "done"
    assert result.complete is True
    assert result.pages == 2
    assert [item.author for item in result.comments] == ["Nguyễn Văn A", "Trần Thị B", "Lê Văn C"]
    assert result.comments[0].text == "Bình luận công khai một"
    assert result.comments[0].likes == 1200
    assert result.comments[0].external_id == "c1"
    assert result.comments[2].text == "Bình luận trang hai"
    assert result.comments[2].likes == 3000
    assert all("login" not in path for path in requested)
