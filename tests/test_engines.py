import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fb_poller.engines.apify_actor import _from_items
from fb_poller.engines.graph_ppca import post_id_from_url
from fb_poller.engines.guard import public_error
from fb_poller.engines.guest_graphql import comments_from_html as graphql_from_html
from fb_poller.engines.guest_graphql import comments_from_payload
from fb_poller.engines.guest_ssr import comments_from_html
from fb_poller.engines.managed_api import _comments_from_json
from fb_poller.engines.octoparse_import import comments_from_export
from fb_poller.engines.pa1_newest import comments_from_articles
from fb_poller.engines.registry import CATALOG, resolve, scan_one
from fb_poller.workers.proxy_manager import parse_proxy_line


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

    def test_ssr_ignores_post_body(self) -> None:
        html = '<script>{"__typename":"Post","body":{"text":"the post"},"author":{"name":"Page"}}</script>'
        self.assertEqual(comments_from_html(html), [])

    def test_ssr_keeps_comment_when_login_link_is_present(self) -> None:
        html = (
            '<a href="/login.php">Log in</a>'
            '<script>{"__typename":"Comment","id":"1","body":{"text":"still public"},"author":{"name":"An"}}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].text, "still public")

    def test_graphql_html_and_prefix_payload(self) -> None:
        html = '<script>{"data":{"node":{"__typename":"Comment","id":"c1","body":{"text":"tu script"},"author":{"name":"Bo"}}}}</script>'
        found = graphql_from_html(html, 5)
        self.assertEqual(found[0].text, "tu script")
        self.assertEqual(found[0].comment_id, "c1")
        post = {"__typename": "Story", "body": {"text": "not a comment"}, "author": {"name": "Page"}}
        self.assertEqual(comments_from_payload(post, 5), [])

    def test_pa1_strips_time_and_reaction_count(self) -> None:
        block = "\n".join(
            [
                "Mark Patrick",
                "Top fan",
                "4 miles in 12 hours?",
                "40w",
                "62",
                "View all 12 replies",
            ]
        )
        post = "NASA\n10 January\nrollout\nAll reactions:\n12K"
        found = comments_from_articles([post, block, block], limit=10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].author_name, "Mark Patrick")
        self.assertEqual(found[0].text, "4 miles in 12 hours?")
        placeholder = "Page\nWrite a comment…"
        self.assertEqual(comments_from_articles([placeholder], 5), [])

    def test_managed_nested_and_single_object(self) -> None:
        nested = _comments_from_json({"data": {"comments": [{"message": "xin", "user_name": "Lan"}]}}, 5)
        self.assertEqual(nested[0].text, "xin")
        self.assertEqual(nested[0].author_name, "Lan")
        single = _comments_from_json({"comment": "mot", "author": {"name": "Minh"}}, 5)
        self.assertEqual(single[0].author_name, "Minh")

    def test_apify_wrapped_items(self) -> None:
        rows = _from_items({"items": [{"text": "actor", "profileName": "Ha"}]}, 5)
        self.assertEqual(rows[0].author_name, "Ha")

    def test_graph_id_from_permalink(self) -> None:
        self.assertEqual(post_id_from_url("https://www.facebook.com/NASA/posts/1426466738848698/"), "1426466738848698")
        self.assertEqual(post_id_from_url("https://www.facebook.com/story.php?story_fbid=99&id=1"), "99")
        self.assertIsNone(post_id_from_url("https://www.facebook.com/NASA"))

    def test_octoparse_semicolon_empty_list_and_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = folder / "semi.csv"
            csv_path.write_text("bình luận;tác giả\nxin chào;An\n", encoding="utf-8")
            found = comments_from_export(csv_path)
            self.assertEqual(found[0].text, "xin chào")
            self.assertEqual(found[0].author_name, "An")
            empty = folder / "empty.json"
            empty.write_text(json.dumps({"comments": []}), encoding="utf-8")
            self.assertEqual(comments_from_export(empty), [])
            lines = folder / "rows.jsonl"
            lines.write_text('{"message":"a","name":"A"}\n{"message":"b","name":"B"}\n', encoding="utf-8")
            lined = comments_from_export(lines)
            self.assertEqual([item.text for item in lined], ["a", "b"])

    def test_proxy_password_may_contain_colons(self) -> None:
        parsed = parse_proxy_line("1.2.3.4:8080:admin:pa:ss")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.password, "pa:ss")
        self.assertEqual(parsed.as_httpx(), "http://admin:pa%3Ass@1.2.3.4:8080")
        self.assertIsNone(parse_proxy_line("1.2.3.4:notaport"))

    def test_public_error_hides_proxy_password(self) -> None:
        exc = RuntimeError("proxy http://admin:112233@1.2.3.4:8080 refused")
        text = public_error(exc)
        self.assertNotIn("112233", text)
        self.assertIn("RuntimeError", text)
        secret = public_error(RuntimeError("GET https://api.example/run?token=super-secret&x=1"))
        self.assertNotIn("super-secret", secret)
        self.assertIn("token=*", secret)

    def test_missing_url_and_bad_limit_do_not_call_network(self) -> None:
        missing = asyncio.run(scan_one("guest-ssr", "  ", limit=5))
        self.assertEqual(missing.error, "missing_url")
        bad = asyncio.run(scan_one("octoparse", "", limit=0, export_path="x.csv"))
        self.assertEqual(bad.error, "invalid_limit")

    def test_comment_after_many_json_values_is_kept(self) -> None:
        late = '{"__typename":"Comment","id":"9","body":{"text":"late"},"author":{"name":"An"}}'
        html = "<script>" + ('{"a":1}' * 500) + late + "</script>"
        found = comments_from_html(html)
        self.assertEqual(found[0].text, "late")
        self.assertEqual(found[0].author_name, "An")

    def test_login_link_on_a_public_page_is_not_a_wall(self) -> None:
        from fb_poller.engines.guest_ssr import looks_like_login

        body = '<a href="/login.php">Log in</a><title>NASA</title>'
        self.assertFalse(looks_like_login("https://www.facebook.com/NASA/posts/1", body))
        self.assertTrue(looks_like_login("https://www.facebook.com/login.php", "<title>Facebook</title>"))

    def test_empty_list_does_not_hide_later_comments(self) -> None:
        rows = _comments_from_json({"data": [], "comments": [{"text": "kept", "author": "A"}]}, 5)
        self.assertEqual(rows[0].text, "kept")
        items = _from_items([{"text": "parent", "profileName": "P", "comments": []}], 5)
        self.assertEqual(items[0].text, "parent")

    def test_encoded_proxy_password_is_decoded_once(self) -> None:
        parsed = parse_proxy_line("http://admin:pa%3Ass@1.2.3.4:8080")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.password, "pa:ss")
        self.assertEqual(parsed.as_httpx(), "http://admin:pa%3Ass@1.2.3.4:8080")

    def test_translation_chrome_is_not_part_of_the_comment(self) -> None:
        block = "\n".join(["Lan", "xin chào", "See translation", "2d"])
        found = comments_from_articles([block], 5)
        self.assertEqual(found[0].text, "xin chào")

    def test_bad_provider_actor_and_graph_version_fail_before_network(self) -> None:
        with patch.dict(os.environ, {"MANAGED_PROVIDER": "nope", "MANAGED_API_KEY": "k"}, clear=False):
            managed = asyncio.run(scan_one("managed-api", "https://www.facebook.com/NASA/posts/1", limit=1))
        self.assertEqual(managed.error, "unknown_provider")
        with patch.dict(os.environ, {"APIFY_TOKEN": "t", "APIFY_ACTOR_ID": "bad actor"}, clear=False):
            apify = asyncio.run(scan_one("apify", "https://www.facebook.com/NASA/posts/1", limit=1))
        self.assertEqual(apify.error, "invalid_APIFY_ACTOR_ID")
        with patch.dict(os.environ, {"GRAPH_ACCESS_TOKEN": "t", "GRAPH_VERSION": "not-a-version"}, clear=False):
            graph = asyncio.run(scan_one("graph-ppca", "https://www.facebook.com/NASA/posts/1", limit=1))
        self.assertEqual(graph.error, "invalid_GRAPH_VERSION")

    def test_comment_fields_before_typename_are_kept(self) -> None:
        html = (
            '<script>{"author":{"name":"An"},"__typename":"Comment","id":"9",'
            '"body":{"text":"xin chao"}}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].text, "xin chao")
        self.assertEqual(found[0].author_name, "An")

    def test_number_inside_a_comment_is_kept(self) -> None:
        block = "\n".join(["Lan", "The code is", "404", "2d", "3"])
        found = comments_from_articles([block], 5)
        self.assertEqual(found[0].text, "The code is 404")

    def test_octoparse_multiline_csv_cell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.csv"
            path.write_text('comment,author\n"line one\nline two",Lan\n', encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "line one\nline two")
        self.assertEqual(found[0].author_name, "Lan")

    def test_octoparse_numeric_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nums.json"
            path.write_text(json.dumps([{"message": 42, "name": "An"}]), encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "42")
        self.assertEqual(found[0].author_name, "An")


if __name__ == "__main__":
    unittest.main()
