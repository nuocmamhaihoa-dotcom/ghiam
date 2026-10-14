"""Kiểm tra đọc link Fanpage và khách hàng Meta Ad Library API."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer

from ads_library.meta_client import MetaApiError, lookup, redact
from ads_library.page_ref import PageRefError, parse_page_ref
from ads_library.server import STATIC_DIR, AdsLibraryHandler, search_request


class PageRefTests(unittest.TestCase):
    def test_username_and_numeric_paths(self) -> None:
        self.assertEqual(parse_page_ref("https://www.facebook.com/example.page").value, "example.page")
        self.assertEqual(parse_page_ref("facebook.com/pages/Shop/123456789012345").kind, "id")
        self.assertEqual(parse_page_ref("https://m.facebook.com/profile.php?id=99887766").value, "99887766")
        library = parse_page_ref("https://www.facebook.com/ads/library/?view_all_page_id=1122334455")
        self.assertEqual(library, parse_page_ref("https://facebook.com/1122334455"))

    def test_rejects_non_pages(self) -> None:
        with self.assertRaises(PageRefError):
            parse_page_ref("https://example.com/shop")
        with self.assertRaises(PageRefError):
            parse_page_ref("https://fb.me/shop")
        with self.assertRaises(PageRefError):
            parse_page_ref("javascript:alert(1)")
        with self.assertRaisesRegex(PageRefError, "view_all_page_id"):
            parse_page_ref("https://www.facebook.com/ads/library/?id=999")
        with self.assertRaisesRegex(PageRefError, "Marketplace"):
            parse_page_ref("https://www.facebook.com/marketplace/item/1")


class MetaClientTests(unittest.TestCase):
    def test_lookup_uses_only_graph_and_stops_foreign_paging(self) -> None:
        calls: list[str] = []

        def fetch(url: str, timeout: float) -> tuple[int, str]:
            self.assertGreater(timeout, 0)
            calls.append(url)
            if "/ads_archive?" in url:
                body = {
                    "data": [{"id": "10", "page_name": "Shop", "ad_creative_bodies": ["Xin chào"]}],
                    "paging": {"next": "https://evil.example/steal?access_token=secret"},
                }
                return 200, json.dumps(body)
            return 200, json.dumps({"id": "555", "name": "Shop", "link": "https://www.facebook.com/555"})

        result = lookup(
            parse_page_ref("https://www.facebook.com/shop.name"),
            country="VN",
            status="ACTIVE",
            limit=50,
            token="test-token",
            fetch=fetch,
        )
        self.assertEqual(result.page.id, "555")
        self.assertEqual(result.ads[0]["id"], "10")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(url.startswith("https://graph.facebook.com/v21.0/") for url in calls))
        self.assertIn("ad_reached_countries=%5B%22VN%22%5D", calls[1])
        self.assertNotIn("evil.example", "".join(calls))

    def test_meta_error_is_redacted(self) -> None:
        def fetch(url: str, timeout: float) -> tuple[int, str]:
            del timeout
            return 400, json.dumps({"error": {"message": "bad access_token=test-token"}})

        with self.assertRaises(MetaApiError) as caught:
            lookup(
                parse_page_ref("https://facebook.com/555555"),
                country="VN",
                status="ACTIVE",
                limit=10,
                token="test-token",
                fetch=fetch,
            )
        self.assertNotIn("test-token", str(caught.exception))
        self.assertIn("[token]", redact("access_token=abc&x=1", "abc"))

    def test_rejects_bad_filters_before_paging(self) -> None:
        def fetch(url: str, timeout: float) -> tuple[int, str]:
            del url, timeout
            raise AssertionError("không được gọi mạng khi bộ lọc sai")

        with self.assertRaises(MetaApiError):
            lookup(
                parse_page_ref("https://facebook.com/555555"),
                country="vietnam",
                status="ACTIVE",
                limit=10,
                token="test-token",
                fetch=fetch,
            )


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


class StaticTests(unittest.TestCase):
    def test_page_ids_are_unique_and_status_is_the_filter(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        parser = _IdCollector()
        parser.feed(html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertIn("message", parser.ids)
        self.assertIn("status", parser.ids)
        self.assertIn('$("message").textContent', script)
        self.assertIn('status: $("status").value', script)


class ServerTests(unittest.TestCase):
    def test_search_request_maps_errors(self) -> None:
        with self.assertRaises(PageRefError):
            search_request({"url": "https://example.com/a"}, "token")
        with self.assertRaises(MetaApiError):
            search_request({"url": "https://facebook.com/shop", "limit": True}, "token")

    def test_http_ready_and_rejected_url(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AdsLibraryHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ready") as response:
                ready = json.loads(response.read().decode())
            self.assertIn("configured", ready)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/search",
                data=json.dumps({"url": "https://example.com/shop"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 400)
            body = json.loads(caught.exception.read().decode())
            self.assertIn("Facebook", body["error"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as page:
                html = page.read().decode()
            self.assertIn("Thư viện quảng cáo Fanpage", html)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
