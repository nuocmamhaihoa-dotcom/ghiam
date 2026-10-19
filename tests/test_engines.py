import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fb_poller.engines.apify_actor import _from_items, _one
from fb_poller.engines.graph_ppca import accepted_object_id, comments_from_graph_payload, post_id_from_url
from fb_poller.engines.guard import (
    comment_id,
    first_comment_id,
    person_name,
    public_error,
    redact_text,
    time_text,
    timestamp_of,
)
from fb_poller.engines.guest_graphql import comments_from_html as graphql_from_html
from fb_poller.engines.guest_graphql import comments_from_payload, merge_comments
from fb_poller.engines.guest_ssr import comments_from_html
from fb_poller.engines.managed_api import _comments_from_json, bright_dataset_id
from fb_poller.engines.octoparse_import import _from_mapping, comments_from_export
from fb_poller.engines.pa1_newest import comments_from_articles
from fb_poller.engines.registry import CATALOG, resolve, scan_each_independent, scan_one
from fb_poller.engines.repair import heal_comments, heal_result
from fb_poller.engines.types import EngineResult, PublicComment
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
        hidden = redact_text('{"access_token":"EAAAsecret","password":"112233"}')
        self.assertNotIn("EAAAsecret", hidden)
        self.assertNotIn("112233", hidden)
        self.assertIn('"access_token":"*"', hidden)

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

    def test_comment_count_is_not_a_comment(self) -> None:
        payload = {"__typename": "CommentCount", "body": {"text": "12"}, "id": "1"}
        self.assertEqual(comments_from_payload(payload, 5), [])
        kept = comments_from_payload(
            {"__typename": "FeedbackComment", "body": {"text": "ok"}, "author": {"name": "An"}},
            5,
        )
        self.assertEqual(kept[0].text, "ok")

    def test_pa1_keeps_a_comment_that_is_only_a_number(self) -> None:
        block = "\n".join(["Lan", "404", "Like", "Reply"])
        found = comments_from_articles([block], 5)
        self.assertEqual(found[0].text, "404")

    def test_one_engine_error_does_not_stop_the_others(self) -> None:
        async def flaky(name: str, url: str, **kwargs: object) -> EngineResult:
            if name == "guest-graphql":
                raise RuntimeError("boom token=super-secret")
            return EngineResult(name, 0, False, error="skipped")

        with patch("fb_poller.engines.registry.scan_one", flaky):
            results = asyncio.run(scan_each_independent("https://www.facebook.com/NASA/posts/1", limit=1))
        self.assertEqual(len(results), 7)
        self.assertEqual(results[0].engine, "guest-graphql")
        self.assertFalse(results[0].ok)
        self.assertNotIn("super-secret", results[0].error or "")
        self.assertEqual(results[1].error, "skipped")

    def test_graph_rejects_an_object_id_that_would_break_the_path(self) -> None:
        self.assertEqual(accepted_object_id("1426466738848698"), "1426466738848698")
        self.assertIsNone(accepted_object_id("https://evil.example/comments"))
        self.assertIsNone(accepted_object_id(True))

    def test_parent_comment_is_kept_when_replies_are_nested(self) -> None:
        managed = _comments_from_json(
            {"text": "parent", "author": "P", "comments": [{"text": "reply", "author": "R"}]},
            5,
        )
        self.assertEqual([item.text for item in managed], ["parent", "reply"])
        apify = _from_items(
            [{"text": "parent", "profileName": "P", "comments": [{"text": "reply", "profileName": "R"}]}],
            5,
        )
        self.assertEqual([item.text for item in apify], ["parent", "reply"])

    def test_octoparse_skips_a_broken_jsonl_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text('{"message":"a","name":"A"}\nNOT JSON\n{"message":"b","name":"B"}\n', encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual([item.text for item in found], ["a", "b"])

    def test_longer_copy_of_the_same_comment_replaces_a_short_one(self) -> None:
        short = PublicComment(text="hi", comment_id="1", author_name="An")
        long = PublicComment(text="hi there", comment_id="1", author_name="An")
        merged = merge_comments([[short], [long]], 5)
        self.assertEqual(merged[0].text, "hi there")

    def test_brightdata_dataset_id_cannot_add_query_parameters(self) -> None:
        with patch.dict(os.environ, {"BRIGHTDATA_DATASET_ID": "gd_ok&format=csv"}, clear=False):
            self.assertIsNone(bright_dataset_id())
        with patch.dict(os.environ, {"BRIGHTDATA_DATASET_ID": "gd_abc123"}, clear=False):
            self.assertEqual(bright_dataset_id(), "gd_abc123")

    def test_pa1_drops_a_compact_reaction_count(self) -> None:
        block = "\n".join(["Lan", "hello", "1.2K", "Like", "Reply"])
        found = comments_from_articles([block], 5)
        self.assertEqual(found[0].text, "hello")
        price = "\n".join(["Lan", "The price is 1.2K", "Like", "Reply"])
        self.assertEqual(comments_from_articles([price], 5)[0].text, "The price is 1.2K")

    def test_missing_export_hides_the_directory(self) -> None:
        missing = asyncio.run(scan_one("octoparse", "", export_path="/tmp/token=super-secret/missing.csv"))
        self.assertEqual(missing.error, "file_not_found: missing.csv")
        self.assertNotIn("super-secret", missing.error or "")

    def test_pa1_keeps_a_duration_written_in_the_comment(self) -> None:
        block = "\n".join(["Lan", "I waited 2h", "Like", "Reply", "3"])
        found = comments_from_articles([block], 5)
        self.assertEqual(found[0].text, "I waited 2h")

    def test_comment_that_mentions_reactions_is_kept(self) -> None:
        comment = "\n".join(["Lan", "I clicked all reactions by accident", "Like", "Reply"])
        post = "NASA\nrollout\nAll reactions:\n12K"
        found = comments_from_articles([post, comment], 5)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].text, "I clicked all reactions by accident")

    def test_ascii_composer_hint_is_not_a_comment(self) -> None:
        payload = {"__typename": "Comment", "body": {"text": "Write a comment..."}}
        self.assertEqual(comments_from_payload(payload, 5), [])
        kept = comments_from_payload({"__typename": "Comment", "body": {"text": "hello..."}}, 5)
        self.assertEqual(kept[0].text, "hello...")

    def test_semicolon_csv_keeps_a_comma_inside_the_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "semi.csv"
            path.write_text('bình luận;"note, a, b";tác giả\nxin chào;hi;An\n', encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "xin chào")
        self.assertEqual(found[0].author_name, "An")

    def test_repeated_managed_id_is_kept_once(self) -> None:
        payload = {
            "text": "hello",
            "id": "9",
            "author": "An",
            "data": {"text": "hello", "id": "9", "author": "An"},
        }
        found = _comments_from_json(payload, 5)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].text, "hello")

    def test_octoparse_numeric_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nums.json"
            path.write_text(json.dumps([{"message": 42, "name": "An"}]), encoding="utf-8")
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "42")
        self.assertEqual(found[0].author_name, "An")


    def test_message_object_is_a_comment(self) -> None:
        graphql = comments_from_payload(
            {"__typename": "Comment", "message": {"text": "xin"}, "author": {"name": "An"}},
            5,
        )
        self.assertEqual(graphql[0].text, "xin")
        html = '<script>{"__typename":"Comment","message":{"text":"xin"},"author":{"name":"An"}}</script>'
        self.assertEqual(comments_from_html(html)[0].text, "xin")
        graph, error = comments_from_graph_payload({"message": {"text": "xin"}, "from": {"name": "An"}}, 5)
        self.assertIsNone(error)
        self.assertEqual(graph[0].author_name, "An")

    def test_graphql_accepts_a_string_author(self) -> None:
        found = comments_from_payload(
            {"__typename": "Comment", "message": "xin", "from": "An", "created_at": "2020-01-01"},
            5,
        )
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2020-01-01")

    def test_apify_single_object_and_empty_list(self) -> None:
        single = _from_items({"text": "solo", "profileName": "Ha"}, 5)
        self.assertEqual(single[0].text, "solo")
        hidden = _from_items({"comments": [], "results": [{"text": "kept", "profileName": "Ha"}]}, 5)
        self.assertEqual(hidden[0].text, "kept")

    def test_graph_list_and_error_with_comments(self) -> None:
        listed, error = comments_from_graph_payload(
            [{"message": "hi", "from": "Ann", "id": "1"}],
            5,
        )
        self.assertIsNone(error)
        self.assertEqual(listed[0].author_name, "Ann")
        kept, warning = comments_from_graph_payload(
            {"error": {"message": "token=secret"}, "data": [{"message": "kept", "id": "2"}]},
            5,
        )
        self.assertIsNone(warning)
        self.assertEqual(kept[0].text, "kept")
        failed, message = comments_from_graph_payload({"error": {"message": "token=secret"}}, 5)
        self.assertEqual(failed, [])
        self.assertNotIn("secret", message or "")

    def test_healer_replays_known_defects(self) -> None:
        duration = PublicComment(text="I waited 2h\n40w\nSee translation", author_name="Lan")
        mention = PublicComment(text="I clicked all reactions by accident", author_name="Lan")
        number = PublicComment(text="The code is\n404", author_name="Lan")
        placeholder = PublicComment(text="Write a comment...", author_name="Lan")
        cleaned, codes = heal_comments([duration, mention, number, placeholder])
        self.assertEqual([item.text for item in cleaned], ["I waited 2h", "I clicked all reactions by accident", "The code is\n404"])
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("drop_placeholder", codes)

        short = PublicComment(text="hi", comment_id="9", author_name="An")
        long = PublicComment(text="hi there", comment_id="9", author_name="An")
        again = PublicComment(text="hi there", comment_id="9", author_name="An")
        merged, merge_codes = heal_comments([short, long, again])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].text, "hi there")
        self.assertIn("prefer_longer_text", merge_codes)
        self.assertIn("dedupe_comment_id", merge_codes)

        encoded = PublicComment(text="Tom &amp;amp; Jerry\u200b", comment_id="True", author_name="Top fan")
        plain, plain_codes = heal_comments([encoded])
        self.assertEqual(plain[0].text, "Tom & Jerry")
        self.assertIsNone(plain[0].comment_id)
        self.assertIsNone(plain[0].author_name)
        self.assertIn("unescape_text", plain_codes)
        self.assertIn("clear_bool_id", plain_codes)
        self.assertIn("clear_chrome_author", plain_codes)

        blob = PublicComment(
            text='{"text":"parent","author":"P","comments":[{"text":"the reply","author":"R"}]}'
        )
        expanded, expand_codes = heal_comments([blob])
        self.assertEqual([item.text for item in expanded], ["parent", "the reply"])
        self.assertIn("expand_json_text", expand_codes)

        recovered = heal_result(
            EngineResult(
                "managed-api",
                3,
                False,
                error='http_400: {"comments":[{"message":"xin","name":"An"}]} token=super-secret',
            )
        )
        self.assertTrue(recovered.ok)
        self.assertIsNone(recovered.error)
        self.assertEqual(recovered.comments[0].text, "xin")
        self.assertNotIn("super-secret", recovered.note or "")
        self.assertIn("recover_comments_from_error", recovered.note or "")

        glued, glued_codes = heal_comments(
            [PublicComment(text="xin chào See translation", author_name="Lan", created_time="null")]
        )
        self.assertEqual(glued[0].text, "xin chào")
        self.assertIsNone(glued[0].created_time)
        self.assertIn("strip_glued_chrome", glued_codes)
        self.assertIn("clear_null_time", glued_codes)
        self.assertEqual(heal_comments([PublicComment(text="I waited 2h")])[0][0].text, "I waited 2h")
        compact, compact_codes = heal_comments([PublicComment(text="hello\n1.2K\n1,234")])
        self.assertEqual(compact[0].text, "hello")
        self.assertIn("strip_chrome_lines", compact_codes)
        self.assertEqual(heal_comments([PublicComment(text="The code is\n404")])[0][0].text, "The code is\n404")

        marked, mark_codes = heal_comments([PublicComment(text="line<br>two", author_name="An\u200b")])
        self.assertEqual(marked[0].text, "line\ntwo")
        self.assertEqual(marked[0].author_name, "An")
        self.assertIn("strip_markup", mark_codes)

        encoded_row, pct_codes = heal_comments([PublicComment(text="hello%20world")])
        self.assertEqual(encoded_row[0].text, "hello world")
        self.assertIn("decode_percent", pct_codes)
        self.assertEqual(heal_comments([PublicComment(text="100% sure")])[0][0].text, "100% sure")

        prefixed, prefix_codes = heal_comments(
            [PublicComment(text='for (;;);{"message":"xin","name":"An"}')]
        )
        self.assertEqual(prefixed[0].text, "xin")
        self.assertEqual(prefixed[0].author_name, "An")
        self.assertIn("expand_json_text", prefix_codes)

        won = heal_result(
            EngineResult("guest-ssr", 7, False, comments=[PublicComment(text="kept")], error="login_wall")
        )
        self.assertTrue(won.ok)
        self.assertIsNone(won.error)
        self.assertEqual(won.comments[0].text, "kept")
        self.assertIn("comments_win", won.note or "")

        twice, _first = heal_comments(cleaned)
        again_clean, second_codes = heal_comments(twice)
        self.assertEqual([item.text for item in again_clean], [item.text for item in twice])
        self.assertEqual(second_codes, [])

    def test_nested_message_object_is_kept(self) -> None:
        managed = _comments_from_json({"body": {"message": "xin"}, "author": {"name": "An"}}, 5)
        self.assertEqual(managed[0].text, "xin")
        self.assertEqual(managed[0].author_name, "An")
        hidden = _from_items(
            [{"text": {"ranges": []}, "message": "real", "profileName": "Ha"}],
            5,
        )
        self.assertEqual(hidden[0].text, "real")
        nested = _from_items([{"text": {"message": "actor"}, "profileName": "Ha"}], 5)
        self.assertEqual(nested[0].text, "actor")
        deep, error = comments_from_graph_payload(
            {"message": {"text": {"message": "deep"}}, "from": {"name": "An"}},
            5,
        )
        self.assertIsNone(error)
        self.assertEqual(deep[0].text, "deep")
        graphql = comments_from_payload(
            {"__typename": "Comment", "body": {"message": "xin"}, "author": {"name": "An"}},
            5,
        )
        self.assertEqual(graphql[0].text, "xin")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested.json"
            path.write_text(
                json.dumps([{"body": {"message": "file"}, "name": "Lan"}]),
                encoding="utf-8",
            )
            found = comments_from_export(path)
        self.assertEqual(found[0].text, "file")
        self.assertEqual(found[0].author_name, "Lan")
        recovered = heal_result(
            EngineResult(
                "managed-api",
                3,
                False,
                error='http_400: {"body":{"message":"xin"},"author":{"name":"An"}}',
            )
        )
        self.assertTrue(recovered.ok)
        self.assertEqual(recovered.comments[0].text, "xin")
        self.assertEqual(recovered.comments[0].author_name, "An")

    def test_empty_reply_list_short_name_and_relative_time(self) -> None:
        managed = _comments_from_json(
            {"text": "xin", "id": True, "legacy_fbid": "99", "author": {"short_name": "An"}},
            5,
        )
        self.assertEqual(managed[0].text, "xin")
        self.assertEqual(managed[0].author_name, "An")
        self.assertEqual(managed[0].comment_id, "99")
        replies = _from_items(
            [
                {
                    "text": "parent",
                    "profileName": "P",
                    "comments": [],
                    "replies": [
                        {
                            "text": "the reply",
                            "author": {"short_name": "R"},
                            "replies": [{"body": {"message": "again"}, "name": "Q"}],
                        }
                    ],
                }
            ],
            5,
        )
        self.assertEqual([item.text for item in replies], ["parent", "the reply", "again"])
        self.assertEqual(replies[1].author_name, "R")
        block = "\n".join(["Lan", "hello there", "2 hours ago", "62 likes", "Like", "Reply"])
        self.assertEqual(comments_from_articles([block], 5)[0].text, "hello there")
        sentence = "\n".join(["Lan", "I replied 2 hours ago", "Like", "Reply"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "I replied 2 hours ago")
        cleaned, codes = heal_comments(
            [PublicComment(text="hello there\n2 giờ trước\n3 lượt thích", author_name="Lan")]
        )
        self.assertEqual(cleaned[0].text, "hello there")
        self.assertIn("strip_chrome_lines", codes)
        kept, _kept_codes = heal_comments([PublicComment(text="I replied 2 hours ago")])
        self.assertEqual(kept[0].text, "I replied 2 hours ago")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "author.json"
            path.write_text(
                json.dumps([{"message": "file", "author": {"short_name": "Lan"}}]),
                encoding="utf-8",
            )
            found = comments_from_export(path)
        self.assertEqual(found[0].author_name, "Lan")

    def test_graph_connection_keeps_nested_replies(self) -> None:
        payload = {
            "data": [],
            "comments": {
                "data": [
                    {
                        "message": "parent",
                        "from": {"name": "P"},
                        "id": "1",
                        "comments": {
                            "data": [{"message": "the reply", "from": {"short_name": "R"}, "id": "2"}]
                        },
                    }
                ]
            },
        }
        found, error = comments_from_graph_payload(payload, 5)
        self.assertIsNone(error)
        self.assertEqual([item.text for item in found], ["parent", "the reply"])
        self.assertEqual(found[1].author_name, "R")
        wrapped = _from_items(
            [{"text": "parent", "profileName": "P", "replies": {"data": [{"text": "the reply", "profileName": "R"}]}}],
            5,
        )
        self.assertEqual([item.text for item in wrapped], ["parent", "the reply"])
        recovered = heal_result(
            EngineResult(
                "graph-ppca",
                6,
                False,
                error='http_400: {"comments":{"data":[{"message":"xin","from":{"short_name":"An"}}]}}',
            )
        )
        self.assertEqual(recovered.comments[0].text, "xin")
        self.assertEqual(recovered.comments[0].author_name, "An")

    def test_nbsp_entity_and_late_typename_are_kept(self) -> None:
        cleaned, codes = heal_comments([PublicComment(text="hello&nbsp;there")])
        self.assertEqual(cleaned[0].text, "hello there")
        self.assertIn("unescape_text", codes)
        numbered, numbered_codes = heal_comments([PublicComment(text="hello&#160;there")])
        self.assertEqual(numbered[0].text, "hello there")
        self.assertIn("normalize_text", numbered_codes)
        junk = ",".join(f'"k{i}":{{"a":1}}' for i in range(45))
        html = (
            '<script>{"id":"9","author":{"name":"An"},'
            + junk
            + ',"__typename":"Comment","body":{"text":"xin"}}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].text, "xin")
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].comment_id, "9")

    def test_original_button_and_wrapped_name_are_kept(self) -> None:
        block = "\n".join(["Lan", "xin chào", "See original (Vietnamese)", "2d"])
        self.assertEqual(comments_from_articles([block], 5)[0].text, "xin chào")
        sentence = "\n".join(["Lan", "See original photos in the album", "Like", "Reply"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "See original photos in the album")
        glued, glued_codes = heal_comments([PublicComment(text="xin chào See original")])
        self.assertEqual(glued[0].text, "xin chào")
        self.assertIn("strip_glued_chrome", glued_codes)
        managed = _comments_from_json(
            {
                "text": "xin",
                "author": {"name": {"text": "An"}},
                "created_time": {"timestamp": 1600000000},
            },
            5,
        )
        self.assertEqual(managed[0].author_name, "An")
        self.assertEqual(managed[0].created_time, "1600000000")
        graph, error = comments_from_graph_payload(
            {"message": "xin", "from": {"name": {"text": "An"}}, "created_time": {"text": "2020-01-01T00:00:00+0000"}},
            5,
        )
        self.assertIsNone(error)
        self.assertEqual(graph[0].author_name, "An")
        self.assertEqual(graph[0].created_time, "2020-01-01T00:00:00+0000")

    def test_follow_button_soft_hyphen_and_dot_timestamp(self) -> None:
        block = "\n".join(["Lan", "hello", "Follow", "an hour ago", "Like"])
        self.assertEqual(comments_from_articles([block], 5)[0].text, "hello")
        sentence = "\n".join(["Lan", "Please follow the instructions", "Like", "Reply"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "Please follow the instructions")
        soft, soft_codes = heal_comments([PublicComment(text="hel\u00adlo\u200e")])
        self.assertEqual(soft[0].text, "hello")
        self.assertIn("normalize_text", soft_codes)
        dotted, dotted_codes = heal_comments([PublicComment(text="xin · 2h")])
        self.assertEqual(dotted[0].text, "xin")
        self.assertIn("strip_glued_chrome", dotted_codes)
        about, about_codes = heal_comments([PublicComment(text="hello\nabout an hour ago")])
        self.assertEqual(about[0].text, "hello")
        self.assertIn("strip_chrome_lines", about_codes)
        managed = _comments_from_json(
            {"text": "xin", "id": 9.0, "profileName": "An"},
            5,
        )
        self.assertEqual(managed[0].comment_id, "9")
        self.assertEqual(managed[0].author_name, "An")
        triple, triple_codes = heal_comments([PublicComment(text="Tom &amp;amp;amp; Jerry")])
        self.assertEqual(triple[0].text, "Tom & Jerry")
        self.assertIn("unescape_text", triple_codes)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(
                json.dumps([{"message": "file", "profile": {"name": "Lan"}}]),
                encoding="utf-8",
            )
            found = comments_from_export(path)
        self.assertEqual(found[0].author_name, "Lan")

    def test_more_button_share_count_and_vietnamese_ago(self) -> None:
        block = "\n".join(["Lan", "hello", "More", "Write a reply...", "1 share", "khoảng 1 giờ trước"])
        self.assertEqual(comments_from_articles([block], 5)[0].text, "hello")
        sentence = "\n".join(["Lan", "I need more time", "Like"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "I need more time")
        glued, codes = heal_comments([PublicComment(text="hello · 1 share")])
        self.assertEqual(glued[0].text, "hello")
        self.assertIn("strip_glued_chrome", codes)
        kept, _codes = heal_comments([PublicComment(text="I left 1 comment yesterday in the thread")])
        self.assertEqual(kept[0].text, "I left 1 comment yesterday in the thread")

    def test_reply_button_pfbid_and_ipv6_proxy(self) -> None:
        block = "\n".join(
            ["Lan", "hello", "Reply to Lan", "Hide 2 replies", "1,234 comments", "Sponsored", "·"]
        )
        self.assertEqual(comments_from_articles([block], 5)[0].text, "hello")
        sentence = "\n".join(["Lan", "I will reply to Lan tomorrow", "Like"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "I will reply to Lan tomorrow")
        glued, codes = heal_comments([PublicComment(text="hello Hide 2 replies")])
        self.assertEqual(glued[0].text, "hello")
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(comment_id("9.0"), "9")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/permalink.php?story_fbid=pfbid0abc&id=1"),
            "pfbid0abc",
        )
        parsed = parse_proxy_line("http://user:pw@[2001:db8::1]:8080")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.server, "http://[2001:db8::1]:8080")
        self.assertEqual(parsed.as_httpx(), "http://user:pw@[2001:db8::1]:8080")

    def test_preferred_body_and_see_one_reply(self) -> None:
        graphql = comments_from_payload(
            {"__typename": "Comment", "preferred_body": {"text": "xin"}, "author": {"name": "An"}},
            5,
        )
        self.assertEqual(graphql[0].text, "xin")
        html = '<script>{"__typename":"Comment","preferred_body":{"text":"xin"},"author":{"name":"An"}}</script>'
        self.assertEqual(comments_from_html(html)[0].text, "xin")
        managed = _comments_from_json({"comment_text": "xin", "author": "An"}, 5)
        self.assertEqual(managed[0].text, "xin")
        apify = _from_items([{"commentText": "xin", "profileName": "Ha"}], 5)
        self.assertEqual(apify[0].text, "xin")
        block = "\n".join(["Lan", "hello", "See 1 reply", "2d · Edited"])
        self.assertEqual(comments_from_articles([block], 5)[0].text, "hello")
        glued, codes = heal_comments([PublicComment(text="hello · Edited")])
        self.assertEqual(glued[0].text, "hello")
        self.assertIn("strip_glued_chrome", codes)
        lines, line_codes = heal_comments(
            [PublicComment(text="hello\nSee 1 reply\nTranslated from Vietnamese\nLove")]
        )
        self.assertEqual(lines[0].text, "hello")
        self.assertIn("strip_chrome_lines", line_codes)
        translated, _ = heal_comments([PublicComment(text="xin chào Translated from Vietnamese")])
        self.assertEqual(translated[0].text, "xin chào")
        sentence = "\n".join(["Lan", "I love this photo", "Like"])
        self.assertEqual(comments_from_articles([sentence], 5)[0].text, "I love this photo")

    def test_compound_post_id_and_python_repr(self) -> None:
        self.assertEqual(post_id_from_url("https://www.facebook.com/NASA/posts/111_222"), "111_222")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/NASA/posts/pfbid0abcXYZ12/"),
            "pfbid0abcXYZ12",
        )
        self.assertEqual(post_id_from_url("https://www.facebook.com/watch/?v=998877"), "998877")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/permalink.php?story_fbid=pfbid0abc%5F1&id=9"),
            "pfbid0abc_1",
        )
        self.assertEqual(post_id_from_url("https://www.facebook.com/NASA/photos/a.111/222"), "222")
        self.assertIsNone(post_id_from_url("https://www.facebook.com/share/p/abcXYZ12/"))
        long_pfbid = "pfbid" + ("a" * 140)
        self.assertEqual(
            post_id_from_url(f"https://www.facebook.com/NASA/posts/{long_pfbid}"),
            long_pfbid,
        )
        expanded, codes = heal_comments([PublicComment(text="{'message': 'xin', 'author': 'An'}")])
        self.assertEqual(expanded[0].text, "xin")
        self.assertEqual(expanded[0].author_name, "An")
        self.assertIn("expand_json_text", codes)
        recovered = heal_result(
            EngineResult("graph-ppca", 6, False, error="{'message': 'xin', 'author': 'An'}")
        )
        self.assertEqual(recovered.comments[0].text, "xin")
        self.assertTrue(recovered.ok)
        self.assertIsNone(recovered.error)
        plain = heal_result(EngineResult("graph-ppca", 6, False, error="missing_GRAPH_ACCESS_TOKEN"))
        self.assertEqual(plain.comments, [])
        self.assertEqual(plain.error, "missing_GRAPH_ACCESS_TOKEN")

    def test_author_stamp_user_object_and_nested_id(self) -> None:
        block = "\n".join(["Lan · 2h", "hello", "Comment as Lan"])
        article = comments_from_articles([block], 5)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nComment as Lan", author_name="Lan · Just now")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        kept, _ = heal_comments([PublicComment(text="hello", author_name="Lan · Nguyen")])
        self.assertEqual(kept[0].author_name, "Lan · Nguyen")
        apify = _from_items([{"comment": "xin", "user": {"name": "Lan"}}], 5)
        self.assertEqual(apify[0].author_name, "Lan")
        managed = _comments_from_json({"text": "xin", "user": {"name": "Lan"}}, 5)
        self.assertEqual(managed[0].author_name, "Lan")
        graph, err = comments_from_graph_payload(
            {"data": [{"body": {"text": "xin"}, "from": {"name": "An"}}]},
            5,
        )
        self.assertEqual(graph[0].text, "xin")
        self.assertEqual(graph[0].author_name, "An")
        self.assertIsNone(err)
        self.assertEqual(first_comment_id({"id": {"id": "999"}}), "999")
        self.assertEqual(first_comment_id({"id": {"name": "nope"}, "legacy_fbid": "7"}), "7")
        parsed = parse_proxy_line("[2001:db8::1]:8080")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.server, "http://[2001:db8::1]:8080")
        authed = parse_proxy_line("[2001:db8::1]:8080:user:pa:ss")
        self.assertIsNotNone(authed)
        assert authed is not None
        self.assertEqual(authed.username, "user")
        self.assertEqual(authed.password, "pa:ss")
        self.assertEqual(authed.as_httpx(), "http://user:pa%3Ass@[2001:db8::1]:8080")

    def test_edges_hours_and_export_id(self) -> None:
        edge = {
            "comments": {
                "edges": [
                    {
                        "cursor": "c",
                        "node": {"message": "xin", "from": {"name": "An"}, "id": "9"},
                    }
                ]
            }
        }
        graph, err = comments_from_graph_payload(edge, 5)
        self.assertEqual(graph[0].text, "xin")
        self.assertEqual(graph[0].author_name, "An")
        self.assertEqual(graph[0].comment_id, "9")
        self.assertIsNone(err)
        relay, relay_err = comments_from_graph_payload(
            {"data": [{"node": {"message": "xin", "from": {"name": "An"}, "id": "9"}}]},
            5,
        )
        self.assertEqual(relay[0].comment_id, "9")
        self.assertIsNone(relay_err)
        self.assertEqual(_comments_from_json(edge, 5)[0].author_name, "An")
        self.assertEqual(_from_items({"items": [{"node": {"comment": "xin", "user": {"name": "Lan"}}}]}, 5)[0].author_name, "Lan")
        healed, codes = heal_comments([PublicComment(text=json.dumps(edge))])
        self.assertEqual(healed[0].text, "xin")
        self.assertEqual(healed[0].author_name, "An")
        self.assertIn("expand_json_text", codes)
        later, later_err = comments_from_graph_payload(
            {"comments": {"edges": [], "data": [{"message": "later", "from": {"name": "Bo"}}]}},
            5,
        )
        self.assertEqual(later[0].text, "later")
        self.assertIsNone(later_err)
        article = comments_from_articles(["Lan · 2 hrs\nhello\n2 hours"], 5)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI waited 2 hours"], 5)
        self.assertEqual(sentence[0].text, "I waited 2 hours")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            path.write_text("comment,author,id\nxin,An,42\n", encoding="utf-8")
            exported = comments_from_export(path)
        self.assertEqual(exported[0].comment_id, "42")
        self.assertEqual(exported[0].author_name, "An")

    def test_photo_url_days_and_content_field(self) -> None:
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/NASA/photos/pcb.111/222333"),
            "222333",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/NASA/photos/a.111/222333/"),
            "222333",
        )
        self.assertIsNone(post_id_from_url("https://www.facebook.com/share/p/abcXYZ12/"))
        article = comments_from_articles(["Lan · 2 days\nhello\n2 days\nYesterday at 3:00 PM"], 5)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI waited 2 days"], 5)
        self.assertEqual(sentence[0].text, "I waited 2 days")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nYesterday at 3:00 PM", author_name="Lan · 2 days")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        listed = comments_from_payload(
            {"__typename": "Comment", "body": {"text": ["xin"]}, "author": {"name": "An"}},
            5,
        )
        self.assertEqual(listed[0].text, "xin")
        self.assertEqual(_comments_from_json({"content": "xin", "author": "An"}, 5)[0].text, "xin")
        timed = _from_items([{"content": "xin", "profileName": "Ha", "createdAt": "2020-01-01"}], 5)
        self.assertEqual(timed[0].text, "xin")
        self.assertEqual(timed[0].created_time, "2020-01-01")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.tsv"
            path.write_text("comment\tauthor\tid\nxin\tAn\t7\n", encoding="utf-8")
            exported = comments_from_export(path)
        self.assertEqual(exported[0].text, "xin")
        self.assertEqual(exported[0].author_name, "An")
        self.assertEqual(exported[0].comment_id, "7")

    def test_weekday_stamp_text_parts_and_comment_url(self) -> None:
        self.assertEqual(post_id_from_url("https://www.facebook.com/NASA/videos/pcb.111/222"), "222")
        article = comments_from_articles(
            ["Lan · Monday at 3:00 PM\nhello\nToday at 1:05 PM\nTranslated by Google"],
            5,
        )
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI said Monday at 3:00 PM"], 5)
        self.assertEqual(sentence[0].text, "I said Monday at 3:00 PM")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nMonday at 3:00 PM\nTranslated by Google",
                    author_name="Lan · Today at 1:05 PM",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        parts = comments_from_payload(
            {"__typename": "Comment", "message": [{"text": "xin"}], "author": {"name": "An"}},
            5,
        )
        self.assertEqual(parts[0].text, "xin")
        self.assertEqual(
            comments_from_payload(
                {"__typename": "Comment", "message": [{"text": "xin", "author": "An"}]},
                5,
            ),
            [],
        )
        row = _from_items(
            [
                {
                    "text": "xin",
                    "profileName": "Ha",
                    "commentUrl": "https://www.facebook.com/x/posts/1?comment_id=42",
                }
            ],
            5,
        )
        self.assertEqual(row[0].comment_id, "42")

    def test_clock_date_and_more_comment(self) -> None:
        article = comments_from_articles(
            ["Lan · 3:00 PM\nhello\nMarch 2 at 3:00 PM\nSee 1 more comment"],
            5,
        )
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nThe meeting is at 3:00 PM"], 5)
        self.assertEqual(sentence[0].text, "The meeting is at 3:00 PM")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\n15:04\nMar 2, 2024\nSee 1 more comment",
                    author_name="Lan · 3:00 PM",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        glued, _ = heal_comments([PublicComment(text="hello See 1 more comment")])
        self.assertEqual(glued[0].text, "hello")
        kept, _ = heal_comments([PublicComment(text="The meeting is at 3:00 PM", author_name="Lan · Nguyen")])
        self.assertEqual(kept[0].text, "The meeting is at 3:00 PM")
        self.assertEqual(kept[0].author_name, "Lan · Nguyen")

    def test_liked_by_and_numeric_dates(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nLiked by Lan and 3 others\nLiked\nWrite a reply…\n"
                "02/03/2024\nngày 2 tháng 3\n2 thg 3\n· 3:00 PM\nEdited · 3:00 PM"
            ],
            5,
        )
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nI have 1 like already\ncall me on 02/03/2024"],
            5,
        )
        self.assertEqual(sentence[0].text, "I have 1 like already call me on 02/03/2024")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text=(
                        "hello\nLiked by Lan and 3 others\n02/03/2024\nngày 2 tháng 3\n"
                        "· 3:00 PM\nEdited · 3:00 PM\nWrite a reply…"
                    ),
                    author_name="Lan · 02/03/2024",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        glued, _ = heal_comments([PublicComment(text="hello Write a reply…")])
        self.assertEqual(glued[0].text, "hello")
        kept, _ = heal_comments(
            [PublicComment(text="I have 1 like already", author_name="Lan · Nguyen")]
        )
        self.assertEqual(kept[0].text, "I have 1 like already")
        self.assertEqual(kept[0].author_name, "Lan · Nguyen")

    def test_reply_counts_iso_dates_and_comment_id_case(self) -> None:
        article = comments_from_articles(
            [
                "Lan · 15h04\nhello\nView 1 reply\n1 Reply\nSee previous comments\n"
                "Load more comments\nLast week\nTuần trước\nEdited · March 2\n"
                "2024-03-02\nWrite a public comment…\nand 3 others\n3:00 p.m.\n"
                "2 thg 3 lúc 15:04\nShow more\nSee who reacted\nAll 12 comments"
            ],
            5,
        )
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nI met Lan and 3 others last week\nThe meeting is at 3:00 p.m."],
            5,
        )
        self.assertEqual(
            sentence[0].text,
            "I met Lan and 3 others last week The meeting is at 3:00 p.m.",
        )
        healed, codes = heal_comments(
            [
                PublicComment(
                    text=(
                        "hello\nView 1 reply\n1 Reply\nAll 12 comments\n2024-03-02\n"
                        "Write a public comment…\n3:00 p.m.\n15h04"
                    ),
                    author_name="Lan · 2024-03-02",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertEqual(first_comment_id({"commentid": "42"}), "42")
        self.assertEqual(first_comment_id({"CommentId": "42"}), "42")
        self.assertEqual(first_comment_id({"COMMENT_ID": "9"}), "9")
        self.assertEqual(
            first_comment_id({"commenturl": "https://www.facebook.com/x?comment_id=77"}),
            "77",
        )
        self.assertEqual(first_comment_id({"ID": {"ID": "999"}}), "999")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as handle:
            handle.write("Comment,Author,CommentId\nxin,Lan,42\n")
            export_path = handle.name
        try:
            imported = comments_from_export(Path(export_path), 5)
        finally:
            Path(export_path).unlink(missing_ok=True)
        self.assertEqual(imported[0].comment_id, "42")
        self.assertEqual(imported[0].text, "xin")

    def test_relative_times_and_folded_headers(self) -> None:
        article = comments_from_articles(
            [
                "Lan · a few seconds ago\nhello\nEdited · just now\nvài phút trước\n"
                "hơn 1 giờ trước\nToday\n2w ago\n3:00 PM GMT\nView more replies\n"
                "Top contributor\nLan · Follow"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI saw this a few seconds ago"], 5)
        self.assertEqual(sentence[0].text, "I saw this a few seconds ago")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\na few seconds ago\n2w ago\n3:00 PM GMT\nView more replies",
                    author_name="Lan · vài phút trước",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        item = _from_mapping({"Comment": "xin", "Profile Name": "Lan", "Comment Time": "2024-03-02"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.author_name, "Lan")
        self.assertEqual(item.created_time, "2024-03-02")
        self.assertEqual(time_text({"Created_Time": "2024-03-02"}), "2024-03-02")
        self.assertEqual(time_text({"createdAt": "2024-03-02T15:04:00Z"}), "2024-03-02T15:04:00Z")
        named = _one({"text": "xin", "UserName": "Ha"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Ha")

    def test_short_ago_stamps_and_nested_comment(self) -> None:
        article = comments_from_articles(
            [
                "Lan · 2 hrs ago\nhello\n1 min ago\nless than a minute ago\n"
                "Edited · a few seconds ago\nEdited 2h\n1 mo\n2 mos\n1 yr\n"
                "khoảng vài phút trước\nHide reply\n1 like this\nFilter\nLan · Author"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI waited 2 hours"], 5)
        self.assertEqual(sentence[0].text, "I waited 2 hours")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\n2 hrs ago\n1 mo\nless than a minute ago\nkhoảng vài phút trước",
                    author_name="Lan · 1 yr",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        nested = _one(
            {
                "comment": {
                    "text": "xin",
                    "author": {"name": "Lan"},
                    "id": "9",
                    "createdAt": "2024-03-02",
                }
            }
        )
        self.assertIsNotNone(nested)
        assert nested is not None
        self.assertEqual(nested.text, "xin")
        self.assertEqual(nested.author_name, "Lan")
        self.assertEqual(nested.comment_id, "9")
        self.assertEqual(nested.created_time, "2024-03-02")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "author": "Lan", "createdAt": "2024-03-02"}]},
            5,
        )
        self.assertEqual(managed[0].created_time, "2024-03-02")
        graph = comments_from_graph_payload(
            {
                "data": [
                    {
                        "comment": {
                            "message": "xin",
                            "from": {"name": "Lan"},
                            "id": "8",
                            "created_time": "2024-03-02T00:00:00+0000",
                        }
                    }
                ]
            },
            5,
        )
        self.assertEqual(graph[0][0].author_name, "Lan")
        self.assertEqual(graph[0][0].comment_id, "8")

    def test_moment_ago_and_creation_time(self) -> None:
        article = comments_from_articles(
            [
                "Lan · a moment ago\nhello\nEdited yesterday\nEdited · 1 mo\n"
                "moments ago\nvừa mới\nYou like this\n1 người thích nội dung này\n"
                "Be the first to comment\nNo comments yet\nOlder comments\n"
                "See 1 more reply\nComment · 2h"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI hope you like this photo"], 5)
        self.assertEqual(sentence[0].text, "I hope you like this photo")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\na moment ago\nEdited yesterday\nOlder comments",
                    author_name="Lan · moments ago",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(first_comment_id({"comment_fbid": "42"}), "42")
        named = _one({"text": "xin", "commenterName": "Lan", "creation_time": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        self.assertEqual(named.created_time, "2024-03-02")
        managed = _comments_from_json(
            {"comments": [{"message": "xin", "from": {"name": "Lan"}, "creation_time": "2024-03-02"}]},
            5,
        )
        self.assertEqual(managed[0].created_time, "2024-03-02")
        graph = comments_from_graph_payload(
            {"data": [{"message": "xin", "from": {"name": "Lan"}, "creation_time": "2024-03-02", "id": "7"}]},
            5,
        )
        self.assertEqual(graph[0][0].created_time, "2024-03-02")
        imported = _from_mapping(
            {"Comment Text": "xin", "Commenter": "Lan", "Creation Time": "2024-03-02"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_relevant_replies_and_feedback_id(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nRelevant comments\nAll replies\nSee who reacted to this\n"
                "Unfollow\nSee original in Vietnamese\nRate this translation"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSee original photos in the album"], 5)
        self.assertEqual(sentence[0].text, "See original photos in the album")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text=(
                        "hello\nRelevant comments\nAll replies\nUnfollow\n"
                        "xin See original in Vietnamese\nxin Rate this translation"
                    ),
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello\nxin\nxin")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(first_comment_id({"feedbackId": "77"}), "77")
        self.assertEqual(first_comment_id({"id": {"feedback_id": "88"}}), "88")
        named = _one(
            {"text": "xin", "commenter": {"name": "Lan"}, "created": "2024-03-02", "feedbackId": "77"}
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        self.assertEqual(named.created_time, "2024-03-02")
        self.assertEqual(named.comment_id, "77")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "authorName": "Lan", "feedbackId": "77", "created": "2024-03-02"}]},
            5,
        )
        self.assertEqual(managed[0].comment_id, "77")
        self.assertEqual(managed[0].author_name, "Lan")
        self.assertEqual(managed[0].created_time, "2024-03-02")

    def test_pinned_other_and_display_name(self) -> None:
        article = comments_from_articles(
            [
                "Lan · một giờ trước\nhello\nLan and 1 other\nHide all replies\n"
                "See all replies\nView all replies\nPinned\nPinned comment\n"
                "See who liked this\n1 new comment\nComment hidden\n"
                "… See more\nLoad more replies"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nI met Lan and 1 other friend"],
            5,
        )
        self.assertEqual(sentence[0].text, "I met Lan and 1 other friend")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nPinned\nHide all replies\nand 1 other\nmột giờ trước\n… See more",
                    author_name="Lan · một phút trước",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(person_name({"displayName": "Lan"}), "Lan")
        self.assertEqual(person_name({"Full Name": "Lan"}), "Lan")
        self.assertEqual(person_name({"user_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"Short Name": "Lan"}), "Lan")
        named = _one({"text": "xin", "author": {"displayName": "Lan"}, "commentTime": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        self.assertEqual(named.created_time, "2024-03-02")
        imported = _from_mapping({"Comment Text": "xin", "Display Name": "Lan"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(post_id_from_url("https://www.facebook.com/reels/999/"), "999")
        self.assertEqual(post_id_from_url("https://www.facebook.com/reel/999/"), "999")
        self.assertEqual(timestamp_of({"created": {"created": "2024-03-02"}}), "2024-03-02")

    def test_menu_chrome_and_nickname(self) -> None:
        article = comments_from_articles(
            [
                "Lan · Moderator\nhello\nReport comment\nCopy link\nSort by\n"
                "Comments are turned off\nLog in to comment\nFollowed by Mai\n"
                "Show translation\nView original\nAuto-translated\n"
                "Was this translation helpful?\nSee insights\nTurn on notifications\n"
                "Hide all comments\nUnhide\nWhy am I seeing this?\nManage comments\n"
                "Who can comment?\nRising contributor\nGroup expert\nNew member\n"
                "Just\u00a0now"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI will report comment spam tomorrow"], 5)
        self.assertEqual(sentence[0].text, "I will report comment spam tomorrow")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nReport comment\nSort by Newest\nxin Show translation",
                    author_name="Lan · New member",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello\nxin")
        self.assertEqual(healed[0].author_name, "Lan")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(person_name({"nickname": "Lan"}), "Lan")
        self.assertEqual(person_name({"screen_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"Nick Name": "Lan"}), "Lan")
        named = _one({"text": "xin", "author": {"nickname": "Lan"}, "updated_time": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        self.assertEqual(named.created_time, "2024-03-02")
        imported = _from_mapping({"Comment Text": "xin", "Nick Name": "Lan", "Updated Time": "2024-03-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_nodes_connection_and_pin_menu(self) -> None:
        article = comments_from_articles(
            [
                "Lan∙just now\nhello\nTop comments\nPin comment\nEdit comment\n"
                "Mark as spam\nNotify me\nFollowing\nTurn off notifications for this comment\n"
                "edited 2 hours ago\nđã chỉnh sửa 2 giờ trước\nView 1 comment\n1 người khác"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nI will edit comment later"], 5)
        self.assertEqual(sentence[0].text, "I will edit comment later")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nTop comments\nedited 2 hours ago\nhello View 1 comment",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello\nhello")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        graph = comments_from_graph_payload(
            {"comments": {"nodes": [{"message": "xin", "from": [{"name": "Lan"}], "id": "9"}]}},
            5,
        )
        self.assertEqual(graph[0][0].text, "xin")
        self.assertEqual(graph[0][0].author_name, "Lan")
        self.assertEqual(graph[0][0].comment_id, "9")
        managed = _comments_from_json(
            {"comments": {"nodes": [{"commentBody": "xin", "authorName": "Lan", "id": "8"}]}},
            5,
        )
        self.assertEqual(managed[0].text, "xin")
        self.assertEqual(managed[0].comment_id, "8")
        named = _one({"bodyText": "xin", "from": [{"name": "Lan"}]})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.text, "xin")
        self.assertEqual(named.author_name, "Lan")
        found = _from_items({"comments": {"nodes": [{"text": "xin", "profileName": "Lan", "id": "4"}]}}, 5)
        self.assertEqual(found[0].comment_id, "4")
        self.assertEqual(found[0].author_name, "Lan")

    def test_encoded_permalink_and_reply_to(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nWrite a reply to Lan\nSee 1 reply from Mai\n"
                "edited about 2 hours ago\nEdited about an hour ago\n"
                "Hide comment\nThis comment was hidden\nSee original post\n"
                "Go to post\nLink copied\njoined the conversation"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nSee original photos in the album\nI will write a reply to Lan tomorrow"],
            5,
        )
        self.assertEqual(
            sentence[0].text,
            "See original photos in the album I will write a reply to Lan tomorrow",
        )
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nSee 1 reply from Mai\nxin Write a reply to Lan",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello\nxin")
        self.assertIn("strip_chrome_lines", codes)
        self.assertIn("strip_glued_chrome", codes)
        self.assertEqual(
            first_comment_id({"permalink": "https://www.facebook.com/x/posts/1?comment_id%3D42"}),
            "42",
        )
        self.assertEqual(person_name('{"name": "Lan"}'), "Lan")
        named = _one({"message_text": "xin", "author": '{"name": "Lan"}'})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.text, "xin")
        self.assertEqual(named.author_name, "Lan")
        graph = comments_from_graph_payload(
            {"data": [{"plain_body": "xin", "from": {"name": "Lan"}, "id": "6"}]},
            5,
        )
        self.assertEqual(graph[0][0].text, "xin")
        self.assertEqual(graph[0][0].author_name, "Lan")

    def test_zero_width_space_and_page_name(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nJust\u200bnow\nLi\u200bke\nDelete comment\nBlock this person\n"
                "Translate comment\nSee more from Mai\n1 reply from Lan"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSee more from the album later"], 5)
        self.assertEqual(sentence[0].text, "See more from the album later")
        healed, _codes = heal_comments(
            [PublicComment(text="hello\nJust\u200bnow\nLi\u200bke", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertEqual(
            first_comment_id({"url": "https://www.facebook.com/x/posts/1/?comment_id=42"}),
            "42",
        )
        self.assertEqual(person_name({"actor": {"name": "Lan"}}), "Lan")
        named = _one({"text": "xin", "page_name": "Lan", "postedAt": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        self.assertEqual(named.created_time, "2024-03-02")
        managed = _comments_from_json(
            {"comment_list": [{"message_text": "xin", "page_name": "Lan", "published_at": "2024-03-02"}]},
            5,
        )
        self.assertEqual(managed[0].text, "xin")
        self.assertEqual(managed[0].author_name, "Lan")
        self.assertEqual(managed[0].created_time, "2024-03-02")
        imported = _from_mapping({"Comment Text": "xin", "Page Name": "Lan", "Posted At": "2024-03-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_trailing_bullet_and_couple_hours(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nAll comments ·\nReply from Mai\nCopy link to comment\n"
                "a couple of hours ago\ncách đây 2 giờ\nAuthor liked this\n"
                "See translation from Vietnamese\nMost relevant first"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSee translation from Vietnamese poetry tonight"], 5)
        self.assertEqual(sentence[0].text, "See translation from Vietnamese poetry tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nAll comments ·\nAll comments•\nhalf an hour ago", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        named = _one({"text": "xin", "profileName": "Lan", "dateTime": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.created_time, "2024-03-02")
        found = _from_items({"items": {"nodes": [{"text": "xin", "profileName": "Lan", "id": "5"}]}}, 5)
        self.assertEqual(found[0].comment_id, "5")
        self.assertEqual(found[0].author_name, "Lan")
        edges = _from_items(
            {"data": {"edges": [{"node": {"text": "xin", "profileName": "Lan", "id": "6"}}]}},
            5,
        )
        self.assertEqual(edges[0].comment_id, "6")

    def test_see_less_translated_and_ngay_dang(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nDịch bởi Google\nSee less from Mai\nView less\nẨn bớt\n"
                "Comment settings\nCài đặt bình luận\nWho can see this comment?\n"
                "Ai có thể xem bình luận này?\nmột lúc trước\nvừa nãy\nEdited recently"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nDịch bởi Google Maps cho khách"], 5)
        self.assertEqual(sentence[0].text, "Dịch bởi Google Maps cho khách")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nDịch bởi Google\nEdited recently\nView less", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        imported = _from_mapping({"Nội dung": "xin", "Tên trang": "Lan", "Ngày đăng": "2024-03-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_creation_time_and_comment_menus(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\njust now · Edited\nHide or report this comment\nComment options\n"
                "Unpin comment\nNew contributor\nLimited profile\nCommenting as Mai\n"
                "Write a public reply\nView 1 more reply\nHide 1 reply\nShow 2 more comments\n"
                "No replies yet\nBe the first to reply\nkhoảng một phút trước\n"
                "Ẩn hoặc báo cáo bình luận này\nViết phản hồi công khai"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nHide or report this comment later tonight"], 5)
        self.assertEqual(sentence[0].text, "Hide or report this comment later tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\njust now · Edited\nComment options", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        found = comments_from_payload(
            {
                "__typename": "Comment",
                "legacy_fbid": "9",
                "body": {"text": "xin"},
                "author": {"name": "An"},
                "creation_time": 1710000000,
            },
            5,
        )
        self.assertEqual(found[0].created_time, "1710000000")
        html = (
            '<script>{"__typename":"Comment","id":"8","body":{"text":"xin"},'
            '"author":{"name":"An"},"creation_time":1710000000}</script>'
        )
        self.assertEqual(comments_from_html(html)[0].created_time, "1710000000")
        named = _one({"text": "xin", "profileName": "Lan", "date_created": "2024-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.created_time, "2024-03-02")

    def test_removed_comment_chrome_and_posted_time(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nThis comment was removed\nContent unavailable\nWas this helpful?\n"
                "Seen by Mai\ncách đây một giờ\nHide post\nBan from group\nShare to story\n"
                "Send in Messenger\nUnfollow Mai\nSnooze Mai for 30 days\nXem 1 phản hồi khác"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nThis comment was removed from my notes"], 5)
        self.assertEqual(sentence[0].text, "This comment was removed from my notes")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nThis comment was removed\nWas this helpful?", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        self.assertEqual(person_name({"first_name": "Lan", "last_name": "Nguyen"}), "Lan Nguyen")
        html = (
            '<script>{"__typename":"Comment","id":"1","body":{"text":"xin"},'
            '"author":{"name":"An"},"posted_time":"2024-03-02"}</script>'
        )
        self.assertEqual(comments_from_html(html)[0].created_time, "2024-03-02")
        created = comments_from_payload(
            {
                "__typename": "Comment",
                "id": "2",
                "plaintext": "xin",
                "author": {"name": "An"},
                "comment_create_time": 1710000001,
            },
            5,
        )
        self.assertEqual(created[0].text, "xin")
        self.assertEqual(created[0].created_time, "1710000001")
        listed = _from_items({"commentList": [{"text": "xin", "userName": "Lan", "id": "7"}]}, 5)
        self.assertEqual(listed[0].comment_id, "7")
        wrapped = _comments_from_json(
            {"result": {"comments": [{"message": "xin", "from": {"name": "Lan"}, "id": "6"}]}},
            5,
        )
        self.assertEqual(wrapped[0].comment_id, "6")
        html_body = _one({"body_html": "<p>xin<br>chao</p>", "author": {"name": "Lan"}, "id": "4"})
        self.assertIsNotNone(html_body)
        assert html_body is not None
        healed_html, _html_codes = heal_comments([html_body])
        self.assertEqual(healed_html[0].text, "xin\nchao")

    def test_owner_name_and_replying_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nEdited · 2 phút trước\nReplying to Mai\nĐang trả lời Mai\n"
                "See more replies from Mai\n1 like · Reply\nLan and Mai like this\n"
                "Share comment\nEdited by author\nGroup admin\nViết câu trả lời\n"
                "Dịch sang tiếng Việt\nMake top comment"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nReplying to Mai was the best part"], 5)
        self.assertEqual(sentence[0].text, "Replying to Mai was the best part")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nEdited · 2 phút trước\nShare comment", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        self.assertEqual(
            person_name({"first_name": "Lan", "middle_name": "Thi", "last_name": "Nguyen"}),
            "Lan Thi Nguyen",
        )
        named = _one({"text": "xin", "ownerName": "Lan", "id": "3"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        managed = _comments_from_json(
            {"comments": [{"message": "xin", "ownerName": "Lan", "id": "8"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Lan")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/1/?post_id=pfbidABC123"),
            "pfbidABC123",
        )
        graph = comments_from_graph_payload(
            {"data": [{"message": "xin", "ownerName": "Lan", "id": "4"}]},
            5,
        )
        self.assertEqual(graph[0][0].author_name, "Lan")

    def test_display_name_and_action_bar(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nĐã chỉnh sửa · vừa xong\nSee translation · Hide\n2 replies · Like\n"
                "Like · Reply · 2d\nWrite a reply to Lan's comment\nLiked your comment\n"
                "1 new reply\nAlso posting as Mai\nEnd of comments\nShow original\nCreated with AI"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nWrite a reply to Lan's comment about the soup"], 5)
        self.assertEqual(sentence[0].text, "Write a reply to Lan's comment about the soup")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nLike · Reply · 2d\nEnd of comments", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        named = _one({"text": "xin", "displayName": "Lan", "id": "1"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan")
        full = _one({"message": "xin", "fullName": "Lan Nguyen", "commentId": "2"})
        self.assertIsNotNone(full)
        assert full is not None
        self.assertEqual(full.author_name, "Lan Nguyen")
        self.assertEqual(full.comment_id, "2")
        wrapped = _from_items(
            {"feedback": {"comments": {"edges": [{"node": {"text": "xin", "id": "6", "author": {"name": "Lan"}}}]}}},
            5,
        )
        self.assertEqual(wrapped[0].comment_id, "6")
        self.assertEqual(
            first_comment_id(
                {
                    "permalink_url": "https://www.facebook.com/permalink.php?story_fbid=1&id=2&comment_id=3&reply_comment_id=44"
                }
            ),
            "44",
        )
        imported = _from_mapping(
            {"Nội dung bình luận": "xin", "Họ và tên": "Lan", "Thời gian đăng": "2024-03-02"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")
        comet = comments_from_payload(
            {
                "__typename": "Comment",
                "id": "1",
                "author": {"name": "An"},
                "comet_sections": {"content": {"story": {"message": {"text": "xin"}}}},
            },
            5,
        )
        self.assertEqual(comet[0].text, "xin")
        self.assertEqual(comet[0].author_name, "An")

    def test_author_title_and_pending_comment(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\njust now · Like\nSee less · Translate\nLoad 5 more comments\n"
                "This comment is pending approval\nMarked as spam\nPinned by Mai\n"
                "Author replied\nStop seeing this\nHide from timeline"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nThis comment is pending approval from the city council"],
            5,
        )
        self.assertEqual(sentence[0].text, "This comment is pending approval from the city council")
        healed, codes = heal_comments(
            [PublicComment(text="hello\njust now · Like\nMarked as spam", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        titled = _one({"text": "xin", "authorTitle": "Lan", "id": "1"})
        self.assertIsNotNone(titled)
        assert titled is not None
        self.assertEqual(titled.author_name, "Lan")
        named = _one({"text": "xin", "userFullName": "Lan Nguyen", "profileUrl": "https://facebook.com/other", "id": "2"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan Nguyen")
        self.assertEqual(person_name("https://www.facebook.com/lan.nguyen"), "lan.nguyen")
        html = (
            '<script>{"__typename":"Comment","legacy_fbid":"5","message":{"text":"xin"},'
            '"author":{"name":"An"},"creationTimestamp":1710000002}</script>'
        )
        self.assertEqual(comments_from_html(html)[0].created_time, "1710000002")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/story.php?story_fbid=pfbidABC%3D&id=1"),
            "pfbidABC",
        )
        imported = _from_mapping({"Bình luận": "xin", "Người bình luận": "Lan", "Ngày": "2024-03-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_nickname_and_reaction_bar(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nReply · Like · Share\n2w · Like\nEdited · Like\nSee more · Translate\n"
                "View 3 more replies from Mai\nShow 4 earlier comments\nFeatured comment\n"
                "1 reaction · Reply\nWhy am I seeing this comment?\nTagged you in a comment"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nWhy am I seeing this comment on my feed today"],
            5,
        )
        self.assertEqual(sentence[0].text, "Why am I seeing this comment on my feed today")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nReply · Like · Share\n2w · Like", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        nick = _one({"text": "xin", "authorNickname": "Lan", "id": "1"})
        self.assertIsNotNone(nick)
        assert nick is not None
        self.assertEqual(nick.author_name, "Lan")
        full = _one({"text": "xin", "commenterFullName": "Lan Nguyen", "id": "2"})
        self.assertIsNotNone(full)
        assert full is not None
        self.assertEqual(full.author_name, "Lan Nguyen")
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"i18n_name":"An"},"creation_timestamp":1710000004}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(timestamp_of({"created_time": {"relative": "2h"}}), "2h")
        wrapped = _from_items(
            {"data": {"feedback": {"comments": {"nodes": [{"message": "xin", "id": "9", "author": {"name": "Lan"}}]}}}},
            5,
        )
        self.assertEqual(wrapped[0].comment_id, "9")
        self.assertEqual(wrapped[0].author_name, "Lan")
        imported = _from_mapping({"Comment": "xin", "Biệt danh": "Lan", "Time text": "2h"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2h")

    def test_given_name_and_profile_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nSee Mai's profile\nAdd friend · Message\nNo results found\n"
                "You're all caught up\nNew comments will appear here\nHide replies\n"
                "Featured by the page\nMeta AI replied\nMost relevant · All comments"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSee Mai's profile picture from the trip"], 5)
        self.assertEqual(sentence[0].text, "See Mai's profile picture from the trip")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nNo results found\nHide replies", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        self.assertEqual(person_name({"given_name": "Lan", "family_name": "Nguyen"}), "Lan Nguyen")
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"localized_name":"An"},"created_time_seconds":1710000005}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "1710000005")
        named = comments_from_payload(
            {
                "__typename": "Comment",
                "id": "8",
                "body": {"text": "xin"},
                "author": {"given_name": "Lan", "family_name": "Nguyen"},
            },
            5,
        )
        self.assertEqual(named[0].author_name, "Lan Nguyen")
        handle = _one({"text": "xin", "userHandle": "@lan", "id": "2"})
        self.assertIsNotNone(handle)
        assert handle is not None
        self.assertEqual(handle.author_name, "@lan")
        wrapped = _from_items({"comment": {"nodes": [{"text": "xin", "id": "11", "author": {"name": "Lan"}}]}}, 5)
        self.assertEqual(wrapped[0].comment_id, "11")
        self.assertEqual(post_id_from_url("https://www.facebook.com/events/555666/"), "555666")
        imported = _from_mapping({"Nội dung comment": "xin", "Tên người dùng": "Lan", "Thời điểm": "2024-03-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "Lan")
        self.assertEqual(imported.created_time, "2024-03-02")

    def test_structured_name_and_counted_action_bar(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nLike · 4 · Reply\nEdited · 2d · Like\njust now · Reply · Like\n"
                "Send · Like\nTranslate · See original\nCopy link · Share\n"
                "Report · Block · Hide\nPin · Unpin\nMessage Mai\nXem thêm câu trả lời"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nMessage Mai about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Message Mai about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nLike · 4 · Reply\nPin · Unpin", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"structured_name":{"text":"An"}},"createdTimeSeconds":1710000007}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "1710000007")
        named = _one({"text": "xin", "commenterHandle": "lan", "id": "3"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "lan")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/item/123456789/"),
            "123456789",
        )
        imported = _from_mapping({"Text comment": "xin", "User handle": "lan", "Created seconds": "1710000009"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "1710000009")

    def test_commenter_name_and_reaction_bars(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nLove · 2 · Reply\nPublic · Friends · Only me\nGIF · Sticker · Photo\n"
                "Most relevant · Newest\nTop fan · Message\nView 1 reply from Lan\n"
                "Copy link · Send in Messenger\nEdited · just now · Like\n"
                "Replied to a comment you are following"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nView 1 reply from Lan about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "View 1 reply from Lan about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nLove · 2 · Reply\nPublic · Friends · Only me", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"commenter_name":"An"},"createdSeconds":1710000011}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "1710000011")
        named = _one({"text": "xin", "legacyCommentId": "99", "author": {"sort_name": "Bo"}})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.comment_id, "99")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "author": {"commenter_name": "An"}, "publish_time": "2024-05-01"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2024-05-01")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/products/99887766/"),
            "99887766",
        )
        imported = _from_mapping({"Comment body": "xin", "Tên hiển thị": "lan", "Created ms": "1710000014"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "1710000014")

    def test_actor_name_and_reaction_count(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nReactions · 4\nSee 4 reactions\nAll comments · Newest\n"
                "just now · Edited · Like\nHide · Unfollow · Report\nReact · Reply\n"
                "GIF · Photo · Sticker\nOnly you can see this comment\nReply in Messenger\n"
                "See edit history\nMute this thread"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nReply in Messenger about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Reply in Messenger about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nReactions · 4\nReact · Reply", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"actor_name":"An"},"commentCreatedAt":"2024-07-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2024-07-01")
        named = _one({"text": "xin", "commentLegacyId": "77", "author": {"from_name": "Bo"}})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.comment_id, "77")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "from_name": "An", "commented_at": "2024-08-01"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2024-08-01")
        self.assertEqual(post_id_from_url("https://www.facebook.com/share/p/99887766/"), "99887766")
        self.assertEqual(post_id_from_url("https://www.facebook.com/share/v/99887766/"), "99887766")
        self.assertEqual(post_id_from_url("https://www.facebook.com/shop/product/99887766"), "99887766")
        threaded = _from_items({"data": {"comment_thread": {"comments": [{"text": "xin", "id": "8"}]}}}, 5)
        self.assertEqual(threaded[0].comment_id, "8")
        managed = _comments_from_json(
            {"comment_thread": {"comments": [{"text": "xin", "actor_name": "Bo", "published_time": "2024-09-01"}]}},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "2024-09-01")
        imported = _from_mapping({"Nội dung gốc": "xin", "From name": "lan", "Ngày tạo": "2024-04-02"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2024-04-02")

    def test_poster_name_and_private_reply_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nSend a private reply\nFollowing this comment\nPhoto · Video · GIF\n"
                "1 reaction · 2 replies\nJoined · Message\nCollapse replies\n"
                "See translation · See original\nPin to top\nOpen in Messenger\n"
                "Quote this comment\nCouldn't load comments"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSend a private reply about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Send a private reply about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nSend a private reply\nPhoto · Video · GIF", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"poster_name":"An"},"createdUnix":1710000021}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "1710000021")
        named = _one({"text": "xin", "author": {"owner_full_name": "Bo"}, "comment_timestamp": "1710000020"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "1710000020")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "poster_name": "An", "created_unix": "171"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "171")
        self.assertEqual(post_id_from_url("https://www.facebook.com/share/r/99887766/"), "99887766")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "user_display_name": "Bo", "posted_at_ms": "1710000023"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "1710000023")
        imported = _from_mapping({"Message text": "xin", "Poster name": "lan", "Comment timestamp": "1710000020"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "1710000020")

    def test_writer_name_and_page_action_bars(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nLike Page · Follow · Message\nPending · Cancel request\n"
                "Accept · Decline\nPublic · Friends · Custom\nSave post · Hide post\n"
                "Interested · Going\nAnswer as Page\nNotify me about replies\n"
                "Show 1 more reply\nJoin group to comment\nSave comment\n"
                "Replies are turned off\nEdit · Delete · Report"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nJoin group to comment about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Join group to comment about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nLike Page · Follow · Message\nSave comment", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"writer_name":"An"},"timeCreated":"2024-10-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2024-10-01")
        named = _one({"text": "xin", "author": {"display_title": "Bo"}, "edited_time": "2024-10-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2024-10-02")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "writer_name": "An", "time_created": "2024-10-03"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2024-10-03")
        self.assertEqual(post_id_from_url("https://www.facebook.com/notes/99887766"), "99887766")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=a.99887766"),
            "99887766",
        )
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "profile_title": "Bo", "edited_time": "2024-10-04"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "2024-10-04")
        imported = _from_mapping({"Plain body": "xin", "Writer name": "lan", "Time created": "2024-10-05"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2024-10-05")

    def test_sender_name_and_suggestion_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nSuggested for you\nWhy am I seeing this post?\n"
                "Find support or report comment\nFriend request sent\nChoose a GIF\n"
                "Helpful · Not helpful\nWas this comment helpful?\nReload comments\n"
                "See more comments from Lan\nRequest to join\nHeld for review\n"
                "Moved to spam\nRestore comment\nFollow this thread\n"
                "Failed to send\nYour comment is pending\nAll-star contributor · Message\n"
                "Mutual friends · 3\nShare to a group\nView story"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSuggested for you to try the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Suggested for you to try the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nSuggested for you\nFriend request sent", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"sender_name":"An"},"publishAt":"2024-11-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2024-11-01")
        named = _one({"text": "xin", "author": {"entity_name": "Bo"}, "comment_date": "2024-11-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2024-11-02")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "sender_name": "An", "message_time": "2024-11-03"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2024-11-03")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "commenter_title": "Bo", "created_ts": "1710000030"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "1710000030")
        imported = _from_mapping({"Body html": "xin", "Sender name": "lan", "Comment date": "2024-11-04"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2024-11-04")

    def test_creator_name_and_review_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nWrite the first comment\nOriginal poster\nCommunity ambassador\n"
                "Top voice\nPaid partnership\nYou hid this post\nAuthor comments only\n"
                "Visible to you\nSlow mode is on\nFollowers only\nMembers only\n"
                "Comment ranking\nAll-star contributor\nJoined the group 2 years ago\n"
                "See all reviews\nHelpful · 2\nEdited review\nThis translation is automatic\n"
                "Đã chỉnh sửa · Vừa xong · Thích\nLọc · Tất cả bình luận"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nWrite the first comment about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Write the first comment about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nWrite the first comment\nSee all reviews", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"creator_name":"An"},"postedDate":"2024-12-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2024-12-01")
        named = _one({"text": "xin", "author": {"owner_title": "Bo"}, "sent_at": "2024-12-03"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2024-12-03")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "creator_name": "An", "comment_at": "2024-12-02"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2024-12-02")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/share/p/pfbid0ABC123/"),
            "pfbid0ABC123",
        )
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "user_label": "Bo", "created_epoch": "1710000040"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "1710000040")
        imported = _from_mapping({"Comment message": "xin", "Creator name": "lan", "Posted date": "2024-12-04"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2024-12-04")

    def test_publisher_name_and_composer_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nStart the conversation\nAdd a comment...\nAdd a public comment\n"
                "Switch to your profile\nUse Page identity\nPosting as Lan\nAlso comment as Page\n"
                "Double tap to like\nChoose a reaction\nUnlike · Reply\nSee likes\n"
                "People who reacted\n4 people reacted\n4 likes · 2 replies\n"
                "2 comments · 1 share\n3 shares · Like\nOpen thread\nView full thread\n"
                "Previous comments\nRefresh comments\nTop comments · Live"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nStart the conversation about the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Start the conversation about the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nStart the conversation\nUnlike · Reply", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"publisher_name":"An"},"writtenAt":"2025-01-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2025-01-01")
        named = _one(
            {"text": "xin", "commentKey": "66", "author": {"account_name": "Bo"}, "post_time": "2025-01-04"}
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.comment_id, "66")
        self.assertEqual(named.created_time, "2025-01-04")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "publisher_name": "An", "written_at": "2025-01-01"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-01-01")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "profile_display": "Bo", "created_ms": "1710000050"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "Bo")
        self.assertEqual(managed[0].created_time, "1710000050")
        threaded = _from_items({"payload": {"comment_thread": {"nodes": [{"text": "xin", "id": "15"}]}}}, 5)
        self.assertEqual(threaded[0].comment_id, "15")
        imported = _from_mapping(
            {"Comment body text": "xin", "Publisher name": "lan", "Written at": "2025-01-05"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-01-05")

    def test_byline_and_preview_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nAdmins only\nApproved users only\nThis is a preview\n"
                "Preview comment\nScheduled comment\nDraft comment\nCouldn't send\n"
                "Hidden from others\nSpam folder\nThis comment was hidden by the page\n"
                "Unmute this thread\n1 person reacted\nAI info\nComment insights\n"
                "Posted by a Page\nVerified Page\nBusiness · Message\nRequested · Cancel\n"
                "Feature · Unfeature\nMore · Edit · Hide\nSee more · See original\n"
                "Translate · Hide translation\nEdited 2d · Like · Reply"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nThis is a preview of the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "This is a preview of the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nAdmins only\nSpam folder", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"byline":"An"},"authoredAt":"2025-02-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2025-02-01")
        named = _one({"text": "xin", "author": {"signed_by": "Bo"}, "submitted_at": "2025-02-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2025-02-02")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "byline": "An", "authored_at": "2025-02-01"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-02-01")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "handle_name": "bo", "unix_ms": "1710000060"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "1710000060")
        imported = _from_mapping({"Nội dung bình luận": "xin", "Byline": "lan", "Authored at": "2025-02-05"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-02-05")

    def test_contributor_name_and_composer_tools(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nabout a minute ago · Like\nan hour ago · Reply\n"
                "Show replies · 4\nReplies · 4 · Like\nThread · 2 replies\n"
                "Continue reading\nRead more · Translate\nShow more · See original\n"
                "Facebook ambassador\nMessage · Wave\nStickers · GIFs · Photos\n"
                "Feeling · Activity\nTag people\nPoll · 2 options\nYes · No · Maybe\n"
                "Report a problem\nTry again\nMute Lan\nWhy am I seeing ads?\n"
                "Sponsored · Like"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nMute Lan for the soup tonight"], 5)
        self.assertEqual(sentence[0].text, "Mute Lan for the soup tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nabout a minute ago · Like\nContinue reading", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"4","body":{"text":"xin"},'
            '"author":{"contributor_name":"An"},"recordedAt":"2025-03-01"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "2025-03-01")
        named = _one({"text": "xin", "author": {"member_name": "Bo"}, "event_time": "2025-03-02"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2025-03-02")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "contributor_name": "An", "occurred_at": "2025-03-03"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-03-03")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "user_alias": "bo", "epoch_ms": "1710000070"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "1710000070")
        threaded = _from_items({"wrapper": {"comment_thread": {"nodes": [{"text": "xin", "id": "17"}]}}}, 5)
        self.assertEqual(threaded[0].comment_id, "17")
        imported = _from_mapping({"Nội dung": "xin", "Member name": "lan", "Recorded at": "2025-03-05"})
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-03-05")

    def test_composer_chrome_and_author_aliases(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nInsert emoji\nAdd a GIF\nAdd a photo or video\n"
                "Follow post\nGive feedback or report this comment\n"
                "Snooze for 30 days\nUnhide comment\nOpen in new tab\n"
                "Go to profile\nShared with Public\nCommenting is off\n"
                "This comment was deleted.\nComment unavailable\n"
                "Suggest an edit\nReport translation\nVerified account\n"
                "People you may know\n1 mutual friend\nLeave a comment…\n"
                "Say something...\nWhat's on your mind?\nPost a comment\n"
                "Choose audience\nComment controls\nRanking settings\n"
                "Sort comments\nSend reaction\nLeave a reply\nMention someone\n"
                "Tag someone\nCreate poll\nJoin conversation\nLeave conversation\n"
                "Start a conversation\nShow more replies\nView replies\n"
                "Load more replies\nJump to comments\n"
                "This content isn't available right now\nPlease try again later\n"
                "Download the app\nContinue with browser\nLog in to reply\n"
                "Create an account\nThêm ảnh hoặc video\nTheo dõi bài viết\n"
                "Đăng nhập để trả lời"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nLeave a comment under this recipe tonight"], 5)
        self.assertEqual(sentence[0].text, "Leave a comment under this recipe tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nInsert emoji\nFollow post\nLog in to reply", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","id":"5","body":{"text":"xin"},'
            '"author":{"author_alias":"An"},"createdTimestamp":"1710000090"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].created_time, "1710000090")
        named = _one({"text": "xin", "author": {"posted_by": "Bo"}, "time_posted": "2025-04-01"})
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2025-04-01")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "writer": "An", "published_on": "2025-04-02"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-04-02")
        managed = _comments_from_json(
            {"comments": [{"text": "xin", "comment_owner": "bo", "comment_created_time": "2025-04-03"}]},
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "2025-04-03")
        imported = _from_mapping(
            {"Nội dung": "xin", "Author display": "lan", "Date posted": "2025-04-05"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.text, "xin")
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-04-05")


if __name__ == "__main__":
    unittest.main()
