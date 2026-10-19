import json
import tempfile
import unittest
from pathlib import Path

from fb_poller.engines.guest_graphql import comments_from_payload
from fb_poller.engines.guest_ssr import comments_from_html
from fb_poller.engines.managed_api import _comments_from_json
from fb_poller.engines.octoparse_import import comments_from_export
from fb_poller.engines.registry import CATALOG, resolve


class EngineTests(unittest.TestCase):
    def test_catalog_is_seven_independent_ranks(self) -> None:
        self.assertEqual([item["rank"] for item in CATALOG], [2, 3, 4, 5, 6, 7, 8])
        for item in CATALOG:
            self.assertTrue(callable(resolve(str(item["name"]))))

    def test_ssr_reads_embedded_comment(self) -> None:
        html = '<script>{"__typename":"Comment","id":"9","body":{"text":"xin chao"},"author":{"name":"An"}}</script>'
        found = comments_from_html(html)
        self.assertEqual(found[0].text, "xin chao")
        self.assertEqual(found[0].author_name, "An")

    def test_graphql_payload(self) -> None:
        payload = {"data": {"node": {"__typename": "Comment", "body": {"text": "moi"}, "author": {"name": "Bo"}}}}
        found = comments_from_payload(payload, 10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].text, "moi")

    def test_managed_json_shapes(self) -> None:
        rows = _comments_from_json({"comments": [{"text": "a", "author": "C"}]}, 10)
        self.assertEqual(rows[0].author_name, "C")

    def test_octoparse_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            path.write_text("comment,author,time\nhello,Lan,2026-01-01\n", encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "hello")
        self.assertEqual(found[0].author_name, "Lan")

    def test_octoparse_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps([{"message": "json", "name": "Minh"}]), encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "json")


if __name__ == "__main__":
    unittest.main()
