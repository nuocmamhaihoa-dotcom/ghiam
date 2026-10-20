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

    def test_photo_fbid_prefers_query_over_album_set(self) -> None:
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/photo/?fbid=111&set=a.222"),
            "111",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/photo.php?fbid=333&set=a.444"),
            "333",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=a.99887766"),
            "99887766",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nAdd to your story\nCreate a reel\nGo live\n"
                "Switch accounts\nContinue as guest\nDon't show again\n"
                "Remind me later\nMaybe later\nNot now\nGive feedback\n"
                "Help & support\nSettings & privacy\nSuper reaction\n"
                "Raise hand\nAsk a question\nFriends except...\n"
                "Embed post\nSave post\nTurn on notifications for this post\n"
                "Share in Messenger\nSend as message\nOpen original post\n"
                "View post insights\nBoost post\nEdit post\nDelete post\n"
                "Change audience\nInvite friends\nJoined Facebook\n"
                "Why am I seeing this ad?\nHide ad\nCan't go\n"
                "Thêm vào tin của bạn\nTạo reel\nChuyển tài khoản"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nSave post for later when you cook"], 5)
        self.assertEqual(sentence[0].text, "Save post for later when you cook")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nSave post\nBoost post\nNot now", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        html = (
            '<script>{"__typename":"Comment","fb_comment_id":"88","body":{"text":"xin"},'
            '"author":{"preferred_name":"An"},"postedTimestamp":"1710000200"}</script>'
        )
        found = comments_from_html(html)
        self.assertEqual(found[0].author_name, "An")
        self.assertEqual(found[0].comment_id, "88")
        self.assertEqual(found[0].created_time, "1710000200")
        named = _one(
            {"text": "xin", "author": {"commenter_alias": "Bo"}, "wrote_at": "2025-05-01"}
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "2025-05-01")
        rows, err = comments_from_graph_payload(
            {"data": [{"message": "xin", "signed_name": "An", "commented_on": "2025-05-02"}]},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-05-02")
        managed = _comments_from_json(
            {
                "comments": [
                    {"text": "xin", "credited_to": "bo", "cid": "77", "created_date": "2025-05-03"}
                ]
            },
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].comment_id, "77")
        self.assertEqual(managed[0].created_time, "2025-05-03")
        imported = _from_mapping(
            {"Nội dung": "xin", "Preferred name": "lan", "Wrote at": "2025-05-05"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-05-05")

    def test_comment_wrappers_and_tag_chrome(self) -> None:
        article = comments_from_articles(
            [
                "Lan\nhello\nYou were tagged\nMentioned you\nAlso commented\n"
                "Also replied\nTagged in this\nSee who shared this\n"
                "Shared by 2 people\nView 3 shares\nWrite a review\n"
                "Rate this page\nWas this review helpful?\nWrite a recommendation\n"
                "Ask for recommendations\nNot recommended\nYou're a top fan\n"
                "Become a top fan\nAbout this ad\nAd choices\nFind support\n"
                "Privacy Checkup\nSecurity and login\nLanguage and region\n"
                "Switch to the app\nKeep me logged in\nAlready have an account?\n"
                "Edit profile\nActivity log\nNews Feed\nLog out\n"
                "Reviews · 12\nPhotos · 5\nWorks at Acme\nLives in Hanoi\n"
                "Active 2h ago\nBạn đã được gắn thẻ\nXem ai đã chia sẻ\n"
                "Chỉnh sửa trang cá nhân\nĐăng xuất"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nYou were tagged in the photo last night"], 5)
        self.assertEqual(sentence[0].text, "You were tagged in the photo last night")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nYou were tagged\nSee who shared this", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        connected = _from_items(
            {
                "comments_connection": {
                    "edges": [{"node": {"text": "xin", "id": "3", "public_name": "An", "said_at": "2025-06-01"}}]
                }
            },
            5,
        )
        self.assertEqual(connected[0].text, "xin")
        self.assertEqual(connected[0].author_name, "An")
        self.assertEqual(connected[0].created_time, "2025-06-01")
        top = _comments_from_json(
            {
                "top_level_comments": {
                    "nodes": [{"text": "xin", "id": "8", "visible_name": "Bo", "ts_ms": "1710000300"}]
                }
            },
            5,
        )
        self.assertEqual(top[0].author_name, "Bo")
        self.assertEqual(top[0].created_time, "1710000300")
        threaded, err = comments_from_graph_payload(
            {"thread": {"comments": [{"message": "xin", "legal_name": "An", "epoch": "1710000301"}]}},
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(threaded[0].author_name, "An")
        self.assertEqual(threaded[0].created_time, "1710000301")
        edge = _from_items(
            {"comment_edge": {"node": {"text": "xin", "id": "9", "author": {"shown_name": "Cy"}}}},
            5,
        )
        self.assertEqual(edge[0].author_name, "Cy")
        self.assertEqual(edge[0].comment_id, "9")
        feedback = _comments_from_json(
            {
                "feedback": {
                    "comments": {
                        "edges": [
                            {
                                "node": {
                                    "body": {"text": "xin"},
                                    "id": "7",
                                    "author": {"stage_name": "Di"},
                                    "unix": "1710000302",
                                }
                            }
                        ]
                    }
                }
            },
            5,
        )
        self.assertEqual(feedback[0].author_name, "Di")
        self.assertEqual(feedback[0].created_time, "1710000302")
        imported = _from_mapping(
            {"Nội dung": "xin", "Public name": "lan", "Said at": "2025-06-05"}
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-06-05")

    def test_pcb_set_and_commerce_chrome(self) -> None:
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=pcb.12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=a.99887766"),
            "99887766",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nBuy now\nShop now\nOrder now\nVisit website\n"
                "Call now\nGet directions\nSend WhatsApp message\nBook now\n"
                "Follow Page\nInvite to group\nLeave group\nGroup rules\n"
                "About this group\nSee all members\nPending posts\n"
                "Member requests\nApprove post\nPin post\n"
                "Who can see your post?\nCreate post\nCreate story\n"
                "New message\nArchive chat\nNo internet connection\n"
                "You're offline\nDiscard draft\nKeep editing\nSchedule post\n"
                "Publish now\nAdd location\nUntag yourself\n"
                "Always translate\nNever translate\nComment sent\n"
                "Tap to retry\nMua ngay\nMời vào nhóm\nRời nhóm\n"
                "Tạo bài viết\nBạn đang ngoại tuyến\nĐã gửi bình luận\n"
                "Bỏ gắn thẻ khỏi bạn\nNhấn để thử lại"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nBuy now before the sale ends tonight"], 5)
        self.assertEqual(sentence[0].text, "Buy now before the sale ends tonight")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nBuy now\nInvite to group\nYou're offline", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        named = _one(
            {
                "text": "xin",
                "author": {"comment_by": "Bo"},
                "time_ms": "1710000500",
            }
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo")
        self.assertEqual(named.created_time, "1710000500")
        rows, err = comments_from_graph_payload(
            {
                "data": [
                    {
                        "message": "xin",
                        "written_by": "An",
                        "created_datetime": "2025-07-01",
                    }
                ]
            },
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-07-01")
        managed = _comments_from_json(
            {
                "comments": [
                    {
                        "text": "xin",
                        "posted_as": "bo",
                        "comment_created": "2025-07-02",
                    }
                ]
            },
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "2025-07-02")
        imported = _from_mapping(
            {
                "Nội dung": "xin",
                "Display label": "lan",
                "Post created at": "2025-07-05",
            }
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-07-05")

    def test_fundraiser_url_and_commerce_event_chrome(self) -> None:
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/fundraisers/123456/"),
            "123456",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/fundraiser/987654"),
            "987654",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nDraft saved\nReview post\nAdd to cart\nCheckout\n"
                "Join event\nAdd to calendar\nGet tickets\nDonate now\n"
                "Live video\nWas live\nWatch again\nPlay video\nSkip ad\n"
                "Sponsored · Shop now\nLike Page · Message\nChat on WhatsApp\n"
                "Product details\nCustomer reviews\nAdd to wishlist\n"
                "Out of stock\nOnly a few left\nPre-order\n"
                "Đã lưu bản nháp\nThêm vào giỏ\nTham gia sự kiện\n"
                "Quyên góp ngay\nVideo trực tiếp\nBỏ qua quảng cáo\n"
                "Được tài trợ · Mua ngay\nThích Trang · Nhắn tin\nHết hàng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(["Lan\nDonate now so the clinic can reopen"], 5)
        self.assertEqual(sentence[0].text, "Donate now so the clinic can reopen")
        healed, codes = heal_comments(
            [PublicComment(text="hello\nAdd to cart\nSkip ad\nDonate now", author_name="Lan")]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        named = _one(
            {
                "text": "xin",
                "author": {"business_name": "Bo Shop"},
                "reply_id": "55",
                "publish_datetime": "2025-08-01",
            }
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo Shop")
        self.assertEqual(named.comment_id, "55")
        self.assertEqual(named.created_time, "2025-08-01")
        rows, err = comments_from_graph_payload(
            {
                "data": [
                    {
                        "message": "xin",
                        "host_name": "An",
                        "comment_datetime": "2025-08-02",
                        "graph_comment_id": "66",
                    }
                ]
            },
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "2025-08-02")
        self.assertEqual(rows[0].comment_id, "66")
        managed = _comments_from_json(
            {
                "comments": [
                    {
                        "text": "xin",
                        "seller_name": "bo",
                        "creation_ms": "1710000700",
                        "comment_graph_id": "77",
                    }
                ]
            },
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "1710000700")
        self.assertEqual(managed[0].comment_id, "77")
        imported = _from_mapping(
            {
                "Nội dung": "xin",
                "Organizer name": "lan",
                "Posted datetime": "2025-08-05",
            }
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-08-05")

    def test_multi_permalinks_stories_and_page_chrome(self) -> None:
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/123/?multi_permalinks=456"),
            "456",
        )
        self.assertEqual(
            post_id_from_url(
                "https://www.facebook.com/groups/1/?multi_permalinks=99887766%2C112233"
            ),
            "99887766",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stories/1234567890"),
            "1234567890",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nSee all stories\nCreate a Story\nClose friends\n"
                "Story settings\nAdd music\nSave video\nDownload video\n"
                "Turn on captions\nNot interested in this video\nHide this Reel\n"
                "Show fewer posts like this\nLike page\nClaim this Page\n"
                "Manage Page\nInvite friends to like this Page\nShare Page\n"
                "This post is unavailable\nGo to News Feed\nNo posts yet\n"
                "Write something…\nShare your thoughts...\n"
                "Xem tất cả tin\nThêm nhạc\nẨn reel này\nViết gì đó…"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        sentence = comments_from_articles(
            ["Lan\nWrite something kind for the host tonight"],
            5,
        )
        self.assertEqual(sentence[0].text, "Write something kind for the host tonight")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nWrite something…\nHide this Reel\nLike page",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        named = _one(
            {
                "text": "xin",
                "author": {"brand_name": "Bo Co"},
                "reply_fbid": "81",
                "fb_time": "2025-09-01",
            }
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Bo Co")
        self.assertEqual(named.comment_id, "81")
        self.assertEqual(named.created_time, "2025-09-01")
        rows, err = comments_from_graph_payload(
            {
                "data": [
                    {
                        "message": "xin",
                        "admin_name": "An",
                        "comment_ts": "1710000900",
                        "legacy_reply_id": "82",
                    }
                ]
            },
            5,
        )
        self.assertIsNone(err)
        self.assertEqual(rows[0].author_name, "An")
        self.assertEqual(rows[0].created_time, "1710000900")
        self.assertEqual(rows[0].comment_id, "82")
        managed = _comments_from_json(
            {
                "comments": [
                    {
                        "text": "xin",
                        "company_name": "bo",
                        "posted_ts": "1710000901",
                        "comment_reply_id": "83",
                    }
                ]
            },
            5,
        )
        self.assertEqual(managed[0].author_name, "bo")
        self.assertEqual(managed[0].created_time, "1710000901")
        self.assertEqual(managed[0].comment_id, "83")
        imported = _from_mapping(
            {
                "Nội dung": "xin",
                "Moderator name": "lan",
                "Update time": "2025-09-05",
            }
        )
        self.assertIsNotNone(imported)
        assert imported is not None
        self.assertEqual(imported.author_name, "lan")
        self.assertEqual(imported.created_time, "2025-09-05")

    def test_octoparse_vn_keys_settings_chrome_and_lphp(self) -> None:
        named = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "tên tác giả": "Lan Nguyen",
                "thời gian tạo": "2024-01-01",
            }
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Lan Nguyen")
        self.assertEqual(named.created_time, "2024-01-01")
        poster = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người đăng": "Minh",
                "ngày giờ": "2 giờ",
            }
        )
        self.assertIsNotNone(poster)
        assert poster is not None
        self.assertEqual(poster.author_name, "Minh")
        self.assertEqual(poster.created_time, "2 giờ")
        scraped = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "speaker_name": "Bo",
                "thời điểm đăng": "hôm qua",
                "root_comment_id": "9911",
            }
        )
        self.assertIsNotNone(scraped)
        assert scraped is not None
        self.assertEqual(scraped.author_name, "Bo")
        self.assertEqual(scraped.created_time, "hôm qua")
        self.assertEqual(scraped.comment_id, "9911")
        fetched = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "page_owner": "Shop ABC",
                "fetched_at": "2024-05-01T10:00:00Z",
            }
        )
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.author_name, "Shop ABC")
        self.assertEqual(fetched.created_time, "2024-05-01T10:00:00Z")
        channel = _from_mapping(
            {
                "body": "long enough comment text here",
                "channel_name": "Channel X",
                "scraped_at": "2024-05-02T11:00:00Z",
            }
        )
        self.assertIsNotNone(channel)
        assert channel is not None
        self.assertEqual(channel.author_name, "Channel X")
        self.assertEqual(channel.created_time, "2024-05-02T11:00:00Z")
        self.assertEqual(person_name({"speaker_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"page_owner": "Shop"}), "Shop")
        self.assertEqual(person_name({"channel_name": "Ch"}), "Ch")
        self.assertEqual(timestamp_of({"fetched_at": "2024-01-01"}), "2024-01-01")
        self.assertEqual(timestamp_of({"imported_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"server_time": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"root_comment_id": "111"}), "111")
        self.assertEqual(first_comment_id({"top_level_comment_id": "222"}), "222")
        self.assertEqual(first_comment_id({"comment_object_id": "333"}), "333")
        self.assertEqual(
            post_id_from_url(
                "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.facebook.com"
                "%2Fpermalink.php%3Fstory_fbid%3D123456789%26id%3D456"
            ),
            "123456789",
        )
        self.assertEqual(
            post_id_from_url(
                "https://lm.facebook.com/l.php?u=https%3A%2F%2Fwww.facebook.com"
                "%2Fphoto.php%3Ffbid%3D999888777"
            ),
            "999888777",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nAccount settings\nPrivacy settings\nNotification settings\n"
                "Your time on Facebook\nFind friends\nCreate a group\nBrowse Marketplace\n"
                "Saved items\nAds Manager\nMeta Business Suite\nProfessional dashboard\n"
                "Creator Studio\nSee more profiles\nCài đặt tài khoản\n"
                "Cài đặt quyền riêng tư\nTìm bạn bè\nQuản lý quảng cáo\n"
                "Manage your account\nEdit profile picture\nPayment methods"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nAccount settings\nAds Manager\nCài đặt tài khoản",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        sentence = comments_from_articles(
            ["Lan\nI changed my account settings last night for privacy"],
            5,
        )
        self.assertEqual(
            sentence[0].text,
            "I changed my account settings last night for privacy",
        )

    def test_help_marketplace_chrome_and_more_vn_keys(self) -> None:
        writer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người viết": "Lan Nguyen",
                "thời gian bình luận": "3 giờ",
            }
        )
        self.assertIsNotNone(writer)
        assert writer is not None
        self.assertEqual(writer.author_name, "Lan Nguyen")
        self.assertEqual(writer.created_time, "3 giờ")
        named = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "tên người bình luận": "Minh",
                "ngày bình luận": "hôm nay",
                "thread_comment_id": "5511",
            }
        )
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.author_name, "Minh")
        self.assertEqual(named.created_time, "hôm nay")
        self.assertEqual(named.comment_id, "5511")
        timed = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "commenter_display": "Bo",
                "giờ đăng": "10:00",
                "edge_comment_id": "6622",
            }
        )
        self.assertIsNotNone(timed)
        assert timed is not None
        self.assertEqual(timed.author_name, "Bo")
        self.assertEqual(timed.created_time, "10:00")
        self.assertEqual(timed.comment_id, "6622")
        collected = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "participant_name": "An",
                "collected_at": "2024-06-01T12:00:00Z",
            }
        )
        self.assertIsNotNone(collected)
        assert collected is not None
        self.assertEqual(collected.author_name, "An")
        self.assertEqual(collected.created_time, "2024-06-01T12:00:00Z")
        self.assertEqual(person_name({"commenter_display": "Lan"}), "Lan")
        self.assertEqual(person_name({"participant_name": "Bo"}), "Bo")
        self.assertEqual(person_name({"người viết": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"collected_at": "2024-01-01"}), "2024-01-01")
        self.assertEqual(timestamp_of({"export_time": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"thời gian bình luận": "2h"}), "2h")
        self.assertEqual(first_comment_id({"thread_comment_id": "55"}), "55")
        self.assertEqual(first_comment_id({"edge_comment_id": "99"}), "99")
        self.assertEqual(first_comment_id({"object_fbid": "88"}), "88")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=o.12345"),
            "12345",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nHelp and support\nPassword and security\n"
                "Two-factor authentication\nOff-Facebook activity\n"
                "Download your information\nSee more in Marketplace\n"
                "Create new listing\nSee all groups\nDiscover groups\n"
                "Friend requests\nQuiet mode\nTerms of Service\n"
                "Privacy Policy\nRecent ad activity\nWhere you're logged in\n"
                "Deactivate or delete\nMark all as read\nSee all in Messenger\n"
                "Open Meta Business Suite\nPast events\nHosted by you\n"
                "Trợ giúp và hỗ trợ\nMật khẩu và bảo mật\nXác thực hai yếu tố\n"
                "Hoạt động ngoài Facebook\nTải thông tin của bạn xuống\n"
                "Xem tất cả nhóm\nKhám phá nhóm\nLời mời kết bạn\n"
                "Chế độ im lặng\nĐiều khoản dịch vụ\nChính sách quyền riêng tư\n"
                "Cài đặt bảo mật và đăng nhập\nNơi bạn đã đăng nhập\n"
                "Vô hiệu hóa hoặc xóa\nTạo tin đăng mới\nTin đăng của bạn"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nHelp and support\nFriend requests\nTrợ giúp và hỗ trợ",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nI need help and support from the shop tonight please"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "I need help and support from the shop tonight please",
        )

    def test_privacy_tagging_chrome_and_sender_vn_keys(self) -> None:
        sender = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người gửi": "Lan Nguyen",
                "thời điểm tạo": "1 giờ",
            }
        )
        self.assertIsNotNone(sender)
        assert sender is not None
        self.assertEqual(sender.author_name, "Lan Nguyen")
        self.assertEqual(sender.created_time, "1 giờ")
        member = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "tên thành viên": "Minh",
                "lúc đăng": "hôm qua",
                "feedback_comment_id": "7711",
            }
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(member.author_name, "Minh")
        self.assertEqual(member.created_time, "hôm qua")
        self.assertEqual(member.comment_id, "7711")
        full = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "họ tên": "Bo Co",
                "thời gian comment": "3h",
                "story_comment_id": "8822",
            }
        )
        self.assertIsNotNone(full)
        assert full is not None
        self.assertEqual(full.author_name, "Bo Co")
        self.assertEqual(full.created_time, "3h")
        self.assertEqual(full.comment_id, "8822")
        reply = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "reply_author_name": "An",
                "extracted_at": "2024-07-01T09:00:00Z",
            }
        )
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.author_name, "An")
        self.assertEqual(reply.created_time, "2024-07-01T09:00:00Z")
        self.assertEqual(person_name({"display_author": "Lan"}), "Lan")
        self.assertEqual(person_name({"người gửi": "Bo"}), "Bo")
        self.assertEqual(person_name({"họ tên": "An Be"}), "An Be")
        self.assertEqual(timestamp_of({"lúc đăng": "2h"}), "2h")
        self.assertEqual(timestamp_of({"synced_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"thời điểm tạo": "1 giờ"}), "1 giờ")
        self.assertEqual(first_comment_id({"feedback_comment_id": "66"}), "66")
        self.assertEqual(first_comment_id({"story_comment_id": "88"}), "88")
        self.assertEqual(first_comment_id({"comment_node_id": "55"}), "55")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=gm.12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=oa.998877"),
            "998877",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nProfile and tagging\nWho can see your future posts\n"
                "Limit who can see past posts\nPosts you're tagged in\n"
                "Timeline and tagging\nTag suggestions\nHow people find and contact you\n"
                "Blocked users\nBlocked Pages\nApps and websites\n"
                "Access your information\nClear search history\nClear watch history\n"
                "Video preferences\nAutoplay videos\nCellular data use\n"
                "Save login info\nLogin alerts\nMemorialization settings\n"
                "Payment settings\nShipping addresses\nYour activity\n"
                "See your activity\nManage notifications\nNotification preferences\n"
                "Push notifications\nGet notifications\nSell something\n"
                "Phím tắt bàn phím\nBáo cáo sự cố\nHoạt động của bạn\nBán gì đó"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nProfile and tagging\nYour activity\nHoạt động của bạn",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPlease report a problem with the delivery tonight"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Please report a problem with the delivery tonight",
        )

    def test_face_history_chrome_share_unwrap_and_commenter_vn(self) -> None:
        commenter = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người comment": "Lan Nguyen",
                "thời gian thu thập": "1h",
            }
        )
        self.assertIsNotNone(commenter)
        assert commenter is not None
        self.assertEqual(commenter.author_name, "Lan Nguyen")
        self.assertEqual(commenter.created_time, "1h")
        account = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "tên tài khoản": "Minh",
                "ngày comment": "hôm nay",
                "messaging_comment_id": "4411",
            }
        )
        self.assertIsNotNone(account)
        assert account is not None
        self.assertEqual(account.author_name, "Minh")
        self.assertEqual(account.created_time, "hôm nay")
        self.assertEqual(account.comment_id, "4411")
        author = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "tác giả bình luận": "Bo",
                "giờ bình luận": "09:30",
                "graph_comment_fbid": "5522",
            }
        )
        self.assertIsNotNone(author)
        assert author is not None
        self.assertEqual(author.author_name, "Bo")
        self.assertEqual(author.created_time, "09:30")
        self.assertEqual(author.comment_id, "5522")
        display = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "comment_author_name": "An",
                "harvested_at": "2024-08-01T08:00:00Z",
            }
        )
        self.assertIsNotNone(display)
        assert display is not None
        self.assertEqual(display.author_name, "An")
        self.assertEqual(display.created_time, "2024-08-01T08:00:00Z")
        self.assertEqual(person_name({"user_display": "Lan"}), "Lan")
        self.assertEqual(person_name({"poster_display": "Bo"}), "Bo")
        self.assertEqual(person_name({"tên tài khoản": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"retrieved_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày comment": "hôm nay"}), "hôm nay")
        self.assertEqual(timestamp_of({"thời gian thu thập": "2h"}), "2h")
        self.assertEqual(first_comment_id({"messaging_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"graph_comment_fbid": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=pb.12345.-2207520000"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=ms.998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url(
                "https://www.facebook.com/dialog/share?href=https%3A%2F%2Fwww.facebook.com%2Fposts%2F999888"
            ),
            "999888",
        )
        self.assertEqual(
            post_id_from_url(
                "https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fwww.facebook.com%2Fphoto.php%3Ffbid%3D777666"
            ),
            "777666",
        )
        self.assertEqual(
            post_id_from_url("https://m.facebook.com/groups/123/?view=permalink&id=456789"),
            "456789",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nFace recognition\nRestricted list\nLocation history\n"
                "Search history\nWatch history\nUse less data\nConfirm your identity\n"
                "Language settings\nPreferred language\nActive status\n"
                "Show when you're active\nMake profile picture\nMake cover photo\n"
                "Story archive\nArchive settings\nSave to archive\nDelete forever\n"
                "Recently deleted\nAdd to favorites\nSee friendship\n"
                "Delivery receipts\nRead receipts\nMessage settings\n"
                "Trình nhận dạng khuôn mặt\nDanh sách hạn chế\nLịch sử vị trí\n"
                "Lịch sử tìm kiếm\nLịch sử xem\nDùng ít dữ liệu hơn\n"
                "Xác nhận danh tính\nCài đặt ngôn ngữ\nNgôn ngữ ưa thích\n"
                "Trạng thái hoạt động\nĐặt làm ảnh đại diện\nLưu trữ tin\n"
                "Xóa vĩnh viễn\nThêm vào mục yêu thích\nCài đặt tin nhắn"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nFace recognition\nLanguage settings\nCài đặt ngôn ngữ",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPlease confirm your identity with the shop later"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Please confirm your identity with the shop later",
        )

    def test_live_room_chrome_reply_vn_keys_and_tagged_sets(self) -> None:
        reply = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người phản hồi": "Lan Nguyen",
                "thời gian crawl": "2h",
            }
        )
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.author_name, "Lan Nguyen")
        self.assertEqual(reply.created_time, "2h")
        profile = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "tên profile": "Minh",
                "thời điểm comment": "hôm qua",
                "comment_xid": "1133",
            }
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.author_name, "Minh")
        self.assertEqual(profile.created_time, "hôm qua")
        self.assertEqual(profile.comment_id, "1133")
        nick = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "tên nick": "Bo",
                "timestamp comment": "09:00",
                "fb_feedback_id": "2244",
            }
        )
        self.assertIsNotNone(nick)
        assert nick is not None
        self.assertEqual(nick.author_name, "Bo")
        self.assertEqual(nick.created_time, "09:00")
        self.assertEqual(nick.comment_id, "2244")
        writer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "writer_display": "An",
                "scraped_time": "2024-09-01T07:00:00Z",
            }
        )
        self.assertIsNotNone(writer)
        assert writer is not None
        self.assertEqual(writer.author_name, "An")
        self.assertEqual(writer.created_time, "2024-09-01T07:00:00Z")
        self.assertEqual(person_name({"owner_display": "Lan"}), "Lan")
        self.assertEqual(person_name({"người phản hồi": "Bo"}), "Bo")
        self.assertEqual(person_name({"tên nick": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"fetch_time": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"thời điểm comment": "1h"}), "1h")
        self.assertEqual(timestamp_of({"scan_time": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"comment_xid": "11"}), "11")
        self.assertEqual(first_comment_id({"fb_feedback_id": "22"}), "22")
        self.assertEqual(first_comment_id({"interaction_id": "33"}), "33")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=t.12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=go.998877"),
            "998877",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nRoom settings\nInvite to room\nShare screen\n"
                "Live settings\nSee live video\nEnd live video\n"
                "Start watch party\nInvite to watch party\nEnd watch party\n"
                "See poll results\nEnd poll\nCaptions settings\n"
                "Show captions\nHide captions\nQuality settings\n"
                "Open camera\nTake photo\nUpload photo\nCrop photo\n"
                "Business integrations\nContact uploading\nAuthorized logins\n"
                "Get alerts about unrecognized logins\nAdd to Watch later\n"
                "Cài đặt phòng\nMời vào phòng\nChia sẻ màn hình\n"
                "Cài đặt trực tiếp\nXem video trực tiếp\n"
                "Xem kết quả bình chọn\nCài đặt phụ đề\nHiện phụ đề\n"
                "Mở máy ảnh\nChụp ảnh\nTải ảnh lên\nTích hợp doanh nghiệp"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nRoom settings\nLive settings\nCài đặt phòng",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nAlways translate comments carefully for guests"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Always translate comments carefully for guests",
        )

    def test_shop_checkout_chrome_guest_vn_keys_and_ft_sets(self) -> None:
        guest = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người trả lời": "Lan Nguyen",
                "thời gian xuất": "3h",
            }
        )
        self.assertIsNotNone(guest)
        assert guest is not None
        self.assertEqual(guest.author_name, "Lan Nguyen")
        self.assertEqual(guest.created_time, "3h")
        customer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "tên khách": "Minh",
                "ngày xuất": "hôm nay",
                "social_comment_id": "4455",
            }
        )
        self.assertIsNotNone(customer)
        assert customer is not None
        self.assertEqual(customer.author_name, "Minh")
        self.assertEqual(customer.created_time, "hôm nay")
        self.assertEqual(customer.comment_id, "4455")
        fan = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "tên fan": "Bo",
                "giờ comment": "08:15",
                "fb_cid": "3366",
            }
        )
        self.assertIsNotNone(fan)
        assert fan is not None
        self.assertEqual(fan.author_name, "Bo")
        self.assertEqual(fan.created_time, "08:15")
        self.assertEqual(fan.comment_id, "3366")
        replier = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "replier_name": "An",
                "exported_at": "2024-10-01T06:00:00Z",
            }
        )
        self.assertIsNotNone(replier)
        assert replier is not None
        self.assertEqual(replier.author_name, "An")
        self.assertEqual(replier.created_time, "2024-10-01T06:00:00Z")
        self.assertEqual(person_name({"guest_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"visitor_name": "Bo"}), "Bo")
        self.assertEqual(person_name({"tên khách": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"queued_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"thời gian xuất": "2h"}), "2h")
        self.assertEqual(timestamp_of({"indexed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"comment_pk": "11"}), "11")
        self.assertEqual(first_comment_id({"comment_uuid": "22"}), "22")
        self.assertEqual(first_comment_id({"social_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=ft.12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=s.998877"),
            "998877",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nOrder details\nTrack package\nView receipt\n"
                "Proceed to checkout\nEnter promo code\nShipping options\n"
                "Delivery options\nEstimated delivery\nBilling address\n"
                "Add payment method\nPlace order\nCancel order\n"
                "Request refund\nContact seller\nLeave a review\n"
                "Filter reviews\nFollow seller\nVisit shop\n"
                "Chi tiết đơn hàng\nTheo dõi kiện hàng\nTiến hành thanh toán\n"
                "Nhập mã khuyến mãi\nTùy chọn vận chuyển\nYêu cầu hoàn tiền\n"
                "Liên hệ người bán\nĐể lại đánh giá\nGhé cửa hàng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nOrder details\nContact seller\nChi tiết đơn hàng",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPlease add to cart if the size is still available tonight"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Please add to cart if the size is still available tonight",
        )

    def test_jobs_dating_fundraiser_chrome_and_buyer_vn_keys(self) -> None:
        buyer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "tên người mua": "Lan Nguyen",
                "thời điểm xuất": "4h",
            }
        )
        self.assertIsNotNone(buyer)
        assert buyer is not None
        self.assertEqual(buyer.author_name, "Lan Nguyen")
        self.assertEqual(buyer.created_time, "4h")
        customer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "khách hàng": "Minh",
                "ngày thu thập": "hôm qua",
                "external_comment_id": "5566",
            }
        )
        self.assertIsNotNone(customer)
        assert customer is not None
        self.assertEqual(customer.author_name, "Minh")
        self.assertEqual(customer.created_time, "hôm qua")
        self.assertEqual(customer.comment_id, "5566")
        viewer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người xem": "Bo",
                "lúc comment": "07:00",
                "source_comment_id": "7788",
            }
        )
        self.assertIsNotNone(viewer)
        assert viewer is not None
        self.assertEqual(viewer.author_name, "Bo")
        self.assertEqual(viewer.created_time, "07:00")
        self.assertEqual(viewer.comment_id, "7788")
        fan = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "fan_name": "An",
                "mirrored_at": "2024-11-01T05:00:00Z",
            }
        )
        self.assertIsNotNone(fan)
        assert fan is not None
        self.assertEqual(fan.author_name, "An")
        self.assertEqual(fan.created_time, "2024-11-01T05:00:00Z")
        self.assertEqual(person_name({"buyer_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"customer_name": "Bo"}), "Bo")
        self.assertEqual(person_name({"khách hàng": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"copied_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày thu thập": "hôm nay"}), "hôm nay")
        self.assertEqual(timestamp_of({"listed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"comment_sid": "11"}), "11")
        self.assertEqual(first_comment_id({"remote_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"external_comment_id": "22"}), "22")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=r.12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=h.998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/donate/333444/"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url(
                "https://www.facebook.com/jobs_application/?job_opening_id=111222"
            ),
            "111222",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nSee all jobs\nApply now\nJob preferences\n"
                "Recommended jobs\nEasy apply\nWithdraw application\n"
                "Company page\nAbout the company\nDating settings\n"
                "Update preferences\nDiscover people\nPassed on you\n"
                "It's a match\nPause dating\nSee fundraiser\n"
                "Share fundraiser\nInvite donors\nFundraiser updates\n"
                "Xem tất cả việc làm\nỨng tuyển ngay\nTùy chọn việc làm\n"
                "Cài đặt hẹn hò\nKhám phá mọi người\nXem gây quỹ\n"
                "Chia sẻ gây quỹ\nMời người quyên góp"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nApply now\nDating settings\nỨng tuyển ngay",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPlease apply now if the position is still open this week"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Please apply now if the position is still open this week",
        )

    def test_gaming_clips_chrome_candidate_vn_keys_and_urls(self) -> None:
        candidate = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "tên ứng viên": "Lan Nguyen",
                "thời gian đồng bộ": "5h",
            }
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.author_name, "Lan Nguyen")
        self.assertEqual(candidate.created_time, "5h")
        employer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "nhà tuyển dụng": "Minh",
                "ngày đồng bộ": "hôm nay",
                "platform_comment_id": "1199",
            }
        )
        self.assertIsNotNone(employer)
        assert employer is not None
        self.assertEqual(employer.author_name, "Minh")
        self.assertEqual(employer.created_time, "hôm nay")
        self.assertEqual(employer.comment_id, "1199")
        applicant = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người ứng tuyển": "Bo",
                "lúc thu thập": "06:30",
                "provider_comment_id": "2288",
            }
        )
        self.assertIsNotNone(applicant)
        assert applicant is not None
        self.assertEqual(applicant.author_name, "Bo")
        self.assertEqual(applicant.created_time, "06:30")
        self.assertEqual(applicant.comment_id, "2288")
        recruiter = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "recruiter_name": "An",
                "replicated_at": "2024-12-01T04:00:00Z",
            }
        )
        self.assertIsNotNone(recruiter)
        assert recruiter is not None
        self.assertEqual(recruiter.author_name, "An")
        self.assertEqual(recruiter.created_time, "2024-12-01T04:00:00Z")
        self.assertEqual(person_name({"applicant_name": "Lan"}), "Lan")
        self.assertEqual(person_name({"candidate_name": "Bo"}), "Bo")
        self.assertEqual(person_name({"tên ứng viên": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"transferred_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày đồng bộ": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"synced_time": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"upstream_comment_id": "33"}), "33")
        self.assertEqual(first_comment_id({"origin_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"platform_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/play/12345/"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/games/app/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/clips/123456/"),
            "123456",
        )
        self.assertEqual(
            post_id_from_url("https://m.facebook.com/clips/play/654321/"),
            "654321",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=c.111222"),
            "111222",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nGame settings\nInvite players\nPlay with friends\n"
                "See leaderboard\nJoin tournament\nGaming activity\n"
                "Clip settings\nCreate clip\nShare clip\nWatch on Facebook\n"
                "Open in app\nContinue in browser\nDownload app\n"
                "Cài đặt trò chơi\nMời người chơi\nChơi với bạn bè\n"
                "Xem bảng xếp hạng\nCài đặt clip\nTạo clip\n"
                "Mở trong ứng dụng\nTải ứng dụng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nGame settings\nClip settings\nCài đặt trò chơi",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPlease play now with friends after dinner tonight"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Please play now with friends after dinner tonight",
        )

    def test_location_business_chrome_shop_vn_keys_and_place_urls(self) -> None:
        shop = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "tên cửa hàng": "Lan Shop",
                "thời gian check in": "6h",
            }
        )
        self.assertIsNotNone(shop)
        assert shop is not None
        self.assertEqual(shop.author_name, "Lan Shop")
        self.assertEqual(shop.created_time, "6h")
        owner = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "chủ quán": "Minh",
                "ngày check in": "hôm nay",
                "place_comment_id": "1357",
            }
        )
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner.author_name, "Minh")
        self.assertEqual(owner.created_time, "hôm nay")
        self.assertEqual(owner.comment_id, "1357")
        checkin = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người check in": "Bo",
                "lúc ghé": "05:45",
                "checkin_comment_id": "2468",
            }
        )
        self.assertIsNotNone(checkin)
        assert checkin is not None
        self.assertEqual(checkin.author_name, "Bo")
        self.assertEqual(checkin.created_time, "05:45")
        self.assertEqual(checkin.comment_id, "2468")
        merchant = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "merchant_name": "An",
                "checked_in_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(merchant)
        assert merchant is not None
        self.assertEqual(merchant.author_name, "An")
        self.assertEqual(merchant.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"business_owner": "Lan"}), "Lan")
        self.assertEqual(person_name({"venue_owner": "Bo"}), "Bo")
        self.assertEqual(person_name({"chủ quán": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"visited_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày check in": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"arrived_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"location_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"venue_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"place_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/places/thing/?id=12345"),
            "12345",
        )
        self.assertEqual(
            post_id_from_url("https://m.facebook.com/place/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/local/lists/555666/"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=e.111222"),
            "111222",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nWeather forecast\nSevere weather alert\n"
                "Traffic update\nShare location\nLive location\n"
                "Nearby places\nTemporarily closed\nPermanently closed\n"
                "Reserve a table\nOrder delivery\nOrder pickup\n"
                "Xem dự báo thời tiết\nChia sẻ vị trí\nĐịa điểm gần đây\n"
                "Đang đóng cửa tạm thời\nĐặt bàn\nĐặt giao hàng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nShare location\nOrder delivery\nChia sẻ vị trí",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nShare location only when you arrive at the gate"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Share location only when you arrive at the gate",
        )

    def test_events_friends_chrome_organizer_vn_keys_and_notes_urls(self) -> None:
        organizer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tổ chức": "Lan Event",
                "thời gian sự kiện": "6h",
            }
        )
        self.assertIsNotNone(organizer)
        assert organizer is not None
        self.assertEqual(organizer.author_name, "Lan Event")
        self.assertEqual(organizer.created_time, "6h")
        invitee = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người mời": "Minh",
                "ngày sự kiện": "hôm nay",
                "event_comment_id": "1357",
            }
        )
        self.assertIsNotNone(invitee)
        assert invitee is not None
        self.assertEqual(invitee.author_name, "Minh")
        self.assertEqual(invitee.created_time, "hôm nay")
        self.assertEqual(invitee.comment_id, "1357")
        attendee = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tham gia": "Bo",
                "giờ bắt đầu": "05:45",
                "invite_comment_id": "2468",
            }
        )
        self.assertIsNotNone(attendee)
        assert attendee is not None
        self.assertEqual(attendee.author_name, "Bo")
        self.assertEqual(attendee.created_time, "05:45")
        self.assertEqual(attendee.comment_id, "2468")
        host = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "event_organizer": "An",
                "starts_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(host)
        assert host is not None
        self.assertEqual(host.author_name, "An")
        self.assertEqual(host.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"event_host": "Lan"}), "Lan")
        self.assertEqual(person_name({"attendee_name": "Bo"}), "Bo")
        self.assertEqual(person_name({"người tổ chức": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"event_time": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày sự kiện": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"event_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"event_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"invite_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"rsvp_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/notes/foo-bar/111222/"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/offers/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url(
                "https://www.facebook.com/messenger_media/?thread_id=1&asset_id=555666"
            ),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=p.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=vb.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nCreate a public event\nInvite friends to this event\n"
                "View event details\nRespond to event\nPeople going\n"
                "People interested\nOn this day last year\nShare this memory\n"
                "Wish them a happy birthday\nSend friend request\n"
                "Accept friend request\nDecline friend request\n"
                "Cancel friend request\nSee all mutual friends\n"
                "Join this group\nLeave this group\nSee group rules\n"
                "Pending posts for review\nMember since yesterday\n"
                "Make an offer\nMessage the seller\nMark item as sold\n"
                "Save this listing\nOriginal audio\nUse this audio\n"
                "Watch more reels\nGo live now\nWatch live video\n"
                "Create a poll\nAdd a poll option\nOpen chat in Messenger\n"
                "Continue this chat in Messenger\n"
                "Tạo sự kiện công khai\nMời bạn bè đến sự kiện này\n"
                "Xem chi tiết sự kiện\nPhản hồi sự kiện\nNgười sẽ tham gia\n"
                "Người quan tâm\nNgày này năm ngoái\nChia sẻ kỷ niệm này\n"
                "Gửi lời mời kết bạn\nChấp nhận lời mời kết bạn\n"
                "Tham gia nhóm này\nRời khỏi nhóm này\nXem quy tắc nhóm\n"
                "Đặt một giá\nNhắn tin cho người bán\nĐánh dấu đã bán\n"
                "Âm thanh gốc\nDùng âm thanh này\nPhát trực tiếp ngay\n"
                "Tạo cuộc thăm dò ý kiến\nMở chat trong Messenger"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCreate a public event\nSend friend request\n"
                    "Tạo sự kiện công khai",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCreate a public event only after you confirm the guest list"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Create a public event only after you confirm the guest list",
        )

    def test_profile_saves_chrome_saver_vn_keys_and_catalog_urls(self) -> None:
        saver = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người lưu": "Lan Save",
                "thời gian lưu": "6h",
            }
        )
        self.assertIsNotNone(saver)
        assert saver is not None
        self.assertEqual(saver.author_name, "Lan Save")
        self.assertEqual(saver.created_time, "6h")
        reporter = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người báo cáo": "Minh",
                "ngày lưu": "hôm nay",
                "save_comment_id": "1357",
            }
        )
        self.assertIsNotNone(reporter)
        assert reporter is not None
        self.assertEqual(reporter.author_name, "Minh")
        self.assertEqual(reporter.created_time, "hôm nay")
        self.assertEqual(reporter.comment_id, "1357")
        blocker = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người chặn": "Bo",
                "lúc báo cáo": "05:45",
                "report_comment_id": "2468",
            }
        )
        self.assertIsNotNone(blocker)
        assert blocker is not None
        self.assertEqual(blocker.author_name, "Bo")
        self.assertEqual(blocker.created_time, "05:45")
        self.assertEqual(blocker.comment_id, "2468")
        page_mgr = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "page_manager": "An",
                "saved_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(page_mgr)
        assert page_mgr is not None
        self.assertEqual(page_mgr.author_name, "An")
        self.assertEqual(page_mgr.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"collection_owner": "Lan"}), "Lan")
        self.assertEqual(person_name({"group_creator": "Bo"}), "Bo")
        self.assertEqual(person_name({"người lưu": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"blocked_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày lưu": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"reported_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"save_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"report_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"block_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/catalog/products/111222/"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/menu/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/appointments/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/booking/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/rooms/777888/"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=n.121212"),
            "121212",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nFind friends from contacts\nBlock this user\n"
                "Report this profile\nEdit your profile\n"
                "Update your profile picture\nUpdate your cover photo\n"
                "Turn on post notifications\nTurn off post notifications\n"
                "Archive this chat\nMark conversation as unread\n"
                "Unfriend this person\nSay hi with a wave\nSend a poke\n"
                "Create your story\nShare to close friends\n"
                "Shop now on website\nForgot your password\n"
                "Change password\nDownload your information\n"
                "Unsave post\nAdd to collection\nCreate collection\n"
                "See your saves\nPages you manage\n"
                "Switch to professional mode\nCreate a fundraiser\n"
                "Blood donations\nMental health resources\n"
                "COVID-19 information\nVoting information\n"
                "Climate science center\nJoin group\nOn this day\n"
                "Share memory\nMake offer\nUse audio\n"
                "Tìm bạn bè từ danh bạ\nChặn người dùng này\n"
                "Báo cáo trang cá nhân này\nChỉnh sửa trang cá nhân của bạn\n"
                "Cập nhật ảnh đại diện của bạn\nBật thông báo bài viết\n"
                "Lưu trữ đoạn chat này\nChào hỏi bằng vẫy tay\n"
                "Tạo tin của bạn\nChia sẻ với bạn thân\n"
                "Mua ngay trên website\nQuên mật khẩu của bạn\n"
                "Đổi mật khẩu\nBỏ lưu bài viết\nThêm vào bộ sưu tập\n"
                "Tạo bộ sưu tập\nXem mục đã lưu\nTrang bạn quản lý\n"
                "Chuyển sang chế độ chuyên nghiệp\nHiến máu\n"
                "Thông tin COVID-19\nThông tin bỏ phiếu\n"
                "Trung tâm khoa học khí hậu\nTham gia nhóm\n"
                "Ngày này năm xưa\nChia sẻ kỷ niệm\nĐặt giá\n"
                "Dùng âm thanh"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nFind friends from contacts\nAdd to collection\n"
                    "Tìm bạn bè từ danh bạ",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nFind friends from contacts only if you already know them"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Find friends from contacts only if you already know them",
        )

    def test_watch_group_chrome_viewer_vn_keys_and_playlist_urls(self) -> None:
        viewer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người xem": "Lan Watch",
                "thời gian xem": "6h",
            }
        )
        self.assertIsNotNone(viewer)
        assert viewer is not None
        self.assertEqual(viewer.author_name, "Lan Watch")
        self.assertEqual(viewer.created_time, "6h")
        seller = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người bán hàng": "Minh",
                "ngày xem": "hôm nay",
                "watch_comment_id": "1357",
            }
        )
        self.assertIsNotNone(seller)
        assert seller is not None
        self.assertEqual(seller.author_name, "Minh")
        self.assertEqual(seller.created_time, "hôm nay")
        self.assertEqual(seller.comment_id, "1357")
        buyer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người mua hàng": "Bo",
                "lúc lưu video": "05:45",
                "listing_comment_id": "2468",
            }
        )
        self.assertIsNotNone(buyer)
        assert buyer is not None
        self.assertEqual(buyer.author_name, "Bo")
        self.assertEqual(buyer.created_time, "05:45")
        self.assertEqual(buyer.comment_id, "2468")
        manager = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "group_manager": "An",
                "watched_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(manager)
        assert manager is not None
        self.assertEqual(manager.author_name, "An")
        self.assertEqual(manager.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"playlist_owner": "Lan"}), "Lan")
        self.assertEqual(person_name({"watchlater_owner": "Bo"}), "Bo")
        self.assertEqual(person_name({"người xem": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"queued_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày xem": "hôm qua"}), "hôm qua")
        self.assertEqual(
            timestamp_of({"thời gian danh sách phát": "2024-01-03"}),
            "2024-01-03",
        )
        self.assertEqual(first_comment_id({"watch_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"listing_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"group_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/watch/playlist/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ads/library/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/live/producer/555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=q.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=v.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nCreate playlist\nStart a watch party\n"
                "Copy video link\nAutoplay settings\n"
                "Mark as available\nRenew listing\nBoost listing\n"
                "Delete listing\nSee similar items\nSeller details\n"
                "Buyer protection\nMeetup details\n"
                "Create a post in group\nSearch this group\n"
                "Group insights\nManage group\nPinned post\n"
                "Featured posts\nAnnouncements\nChats in group\n"
                "See all friends\nMutual friends\nGet directions now\n"
                "Call this business\nShare this location\nAdd a location\n"
                "Invite people to group\nMark as sold\nSave listing\n"
                "Add friend\nSee results\nLive now\nWatch live\n"
                "Member since\nHelp center\nRecent searches\n"
                "Trending searches\nSee all notifications\n"
                "Tạo danh sách phát\nBắt đầu buổi xem chung\n"
                "Sao chép liên kết video\nCài đặt tự động phát\n"
                "Đánh dấu còn hàng\nGia hạn tin đăng\nQuảng cáo tin đăng\n"
                "Xóa tin đăng\nXem mặt hàng tương tự\nChi tiết người bán\n"
                "Bảo vệ người mua\nChi tiết gặp mặt\n"
                "Tạo bài viết trong nhóm\nTìm trong nhóm này\n"
                "Thống kê nhóm\nQuản lý nhóm\nBài viết đã ghim\n"
                "Bài viết nổi bật\nThông báo nhóm\nTrò chuyện trong nhóm\n"
                "Xem tất cả bạn bè\nBạn chung\nChỉ đường ngay\n"
                "Gọi doanh nghiệp này\nChia sẻ vị trí này\nThêm một vị trí\n"
                "Mời mọi người vào nhóm\nĐánh dấu đã bán\nLưu tin đăng\n"
                "Thêm bạn bè\nXem kết quả\nĐang phát trực tiếp\n"
                "Xem trực tiếp\nThành viên từ\nTrung tâm trợ giúp\n"
                "Tìm kiếm gần đây\nTìm kiếm thịnh hành\n"
                "Xem tất cả thông báo"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCreate playlist\nManage group\n"
                    "Tạo danh sách phát",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCreate playlist only after you finish the video draft"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Create playlist only after you finish the video draft",
        )

    def test_effects_orders_chrome_buyer_vn_keys_and_effect_urls(self) -> None:
        buyer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người đặt hàng": "Lan Order",
                "thời gian đặt hàng": "6h",
            }
        )
        self.assertIsNotNone(buyer)
        assert buyer is not None
        self.assertEqual(buyer.author_name, "Lan Order")
        self.assertEqual(buyer.created_time, "6h")
        payer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người thanh toán": "Minh",
                "ngày thanh toán": "hôm nay",
                "order_comment_id": "1357",
            }
        )
        self.assertIsNotNone(payer)
        assert payer is not None
        self.assertEqual(payer.author_name, "Minh")
        self.assertEqual(payer.created_time, "hôm nay")
        self.assertEqual(payer.comment_id, "1357")
        creator = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tạo hiệu ứng": "Bo",
                "ordered_at": "05:45",
                "effect_comment_id": "2468",
            }
        )
        self.assertIsNotNone(creator)
        assert creator is not None
        self.assertEqual(creator.author_name, "Bo")
        self.assertEqual(creator.created_time, "05:45")
        self.assertEqual(creator.comment_id, "2468")
        sticker = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "effect_creator": "An",
                "paid_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(sticker)
        assert sticker is not None
        self.assertEqual(sticker.author_name, "An")
        self.assertEqual(sticker.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"sticker_sender": "Lan"}), "Lan")
        self.assertEqual(person_name({"order_buyer": "Bo"}), "Bo")
        self.assertEqual(person_name({"người đặt hàng": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"effect_created_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày thanh toán": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"ordered_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"order_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"effect_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"payment_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/effects/111222/"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/orders/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/payments/555666/"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/coupon/333444/"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=y.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nArchive story\nDelete story\nAllow replies\n"
                "Don't allow replies\nSave to camera roll\nRemix this reel\n"
                "Use this effect\nSee effect\nTry effect\nEffect by\n"
                "Track order\nView order\nShipping address\nEdit address\n"
                "Checkout now\nSave audio\nShare audio\nAudio details\n"
                "Ask Meta AI\nSearch with Meta AI\nTry Meta AI\n"
                "Edit with AI\nOpen emoji picker\nAdd a reaction\n"
                "Share video\nUpdate dating preferences\n"
                "Take a break from\nSee poll results\n"
                "Lưu trữ tin\nXóa tin\nCho phép trả lời\n"
                "Không cho phép trả lời\nLưu vào cuộn camera\n"
                "Phối lại reel này\nDùng hiệu ứng này\nXem hiệu ứng\n"
                "Thử hiệu ứng\nHiệu ứng của\nTheo dõi đơn hàng\n"
                "Xem đơn hàng\nHủy đơn hàng\nChỉnh sửa địa chỉ\n"
                "Áp dụng mã giảm giá\nThanh toán ngay\nLưu âm thanh\n"
                "Chia sẻ âm thanh\nChi tiết âm thanh\nHỏi Meta AI\n"
                "Tìm với Meta AI\nThử Meta AI\nĐược tạo bởi AI\n"
                "Chỉnh sửa bằng AI\nGửi nhãn dán\nChọn GIF\n"
                "Mở bảng biểu tượng cảm xúc\nXem ai đã bày tỏ cảm xúc\n"
                "Gỡ cảm xúc\nChia sẻ video\nCập nhật tùy chọn hẹn hò\n"
                "Tạm nghỉ với\nXem kết quả thăm dò\n"
                "Ứng phó khủng hoảng\nThêm vào Xem sau\n"
                "Xóa khỏi Xem sau\nHình trong hình\nBuổi xem chung\n"
                "Đánh dấu tất cả đã đọc"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nUse this effect\nTrack order\n"
                    "Dùng hiệu ứng này",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nUse this effect only when the lighting looks good"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Use this effect only when the lighting looks good",
        )

    def test_creator_security_chrome_creator_vn_keys_and_collab_urls(self) -> None:
        creator = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người sáng tạo": "Lan Creator",
                "thời gian bản nháp": "6h",
            }
        )
        self.assertIsNotNone(creator)
        assert creator is not None
        self.assertEqual(creator.author_name, "Lan Creator")
        self.assertEqual(creator.created_time, "6h")
        collab = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người hợp tác": "Minh",
                "ngày kiếm tiền": "hôm nay",
                "creator_comment_id": "1357",
            }
        )
        self.assertIsNotNone(collab)
        assert collab is not None
        self.assertEqual(collab.author_name, "Minh")
        self.assertEqual(collab.created_time, "hôm nay")
        self.assertEqual(collab.comment_id, "1357")
        earner = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người kiếm tiền": "Bo",
                "drafted_at": "05:45",
                "collab_comment_id": "2468",
            }
        )
        self.assertIsNotNone(earner)
        assert earner is not None
        self.assertEqual(earner.author_name, "Bo")
        self.assertEqual(earner.created_time, "05:45")
        self.assertEqual(earner.comment_id, "2468")
        voice = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "brand_partner": "An",
                "monetized_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(voice)
        assert voice is not None
        self.assertEqual(voice.author_name, "An")
        self.assertEqual(voice.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"voiceover_author": "Lan"}), "Lan")
        self.assertEqual(person_name({"draft_owner": "Bo"}), "Bo")
        self.assertEqual(person_name({"người sáng tạo": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"collab_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày kiếm tiền": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"drafted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"creator_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"collab_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"draft_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/brandedcontent/111222/"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stars/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/drafts/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=i.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=z.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nContent library\nStars balance\nFan subscriptions\n"
                "Bonus program\nIn-stream ads\nBrand collabs\n"
                "Request a collab\nApply to brand\nSee earnings\n"
                "Payout settings\nTrim video\nAdd text overlay\n"
                "Change thumbnail\nReorder clips\nGreen screen\n"
                "Align mode\nSave draft\nDiscard draft\n"
                "Unrecognized login\nThis wasn't me\nSecure your account\n"
                "Review login\nLog out of all sessions\n"
                "Paid partnership with\nEvent by\n"
                "Where you're logged in\n"
                "Thư viện nội dung\nSố dư Stars\nGói đăng ký fan\n"
                "Chương trình thưởng\nQuảng cáo trong luồng\n"
                "Hợp tác thương hiệu\nYêu cầu hợp tác\n"
                "Ứng tuyển thương hiệu\nXem thu nhập\n"
                "Cắt video\nThêm chữ chồng\nĐổi ảnh thu nhỏ\n"
                "Sắp xếp lại clip\nPhông xanh\nChế độ căn chỉnh\n"
                "Lưu bản nháp\nHủy bản nháp\n"
                "Đăng nhập không nhận dạng\nĐây không phải tôi\n"
                "Bảo mật tài khoản của bạn\nXem lại đăng nhập\n"
                "Đăng xuất tất cả phiên\nHợp tác trả phí với\n"
                "Sự kiện của\nNơi bạn đang đăng nhập"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nRequest a collab\nSecure your account\n"
                    "Yêu cầu hợp tác",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nRequest a collab only after you finish the brand brief"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Request a collab only after you finish the brand brief",
        )

    def test_boost_inbox_chrome_advertiser_vn_keys_and_boost_urls(self) -> None:
        advertiser = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người quảng cáo": "Lan Ads",
                "thời gian quảng cáo": "6h",
            }
        )
        self.assertIsNotNone(advertiser)
        assert advertiser is not None
        self.assertEqual(advertiser.author_name, "Lan Ads")
        self.assertEqual(advertiser.created_time, "6h")
        page_mgr = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người quản lý trang": "Minh",
                "ngày boost": "hôm nay",
                "boost_comment_id": "1357",
            }
        )
        self.assertIsNotNone(page_mgr)
        assert page_mgr is not None
        self.assertEqual(page_mgr.author_name, "Minh")
        self.assertEqual(page_mgr.created_time, "hôm nay")
        self.assertEqual(page_mgr.comment_id, "1357")
        messenger = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người gửi tin nhắn": "Bo",
                "boosted_at": "05:45",
                "inbox_comment_id": "2468",
            }
        )
        self.assertIsNotNone(messenger)
        assert messenger is not None
        self.assertEqual(messenger.author_name, "Bo")
        self.assertEqual(messenger.created_time, "05:45")
        self.assertEqual(messenger.comment_id, "2468")
        captioner = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "page_promoter": "An",
                "promoted_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(captioner)
        assert captioner is not None
        self.assertEqual(captioner.author_name, "An")
        self.assertEqual(captioner.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"alt_text_editor": "Lan"}), "Lan")
        self.assertEqual(person_name({"inbox_agent": "Bo"}), "Bo")
        self.assertEqual(person_name({"người quảng cáo": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"captioned_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày boost": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"boosted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"boost_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"inbox_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"promo_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/boost/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/yearinreview/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=u.555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=w.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=x.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nYour memories\nSee your year in review\n"
                "Manage activity\nClear activity\nDownload activity\n"
                "Filter by date\nFilter by category\nPage transparency\n"
                "See Page info\nPage settings\nInvite people to like Page\n"
                "Promote Page\nCreate a promotion\nBoost again\n"
                "See promotion results\nAd manager\nBilling and payments\n"
                "Payment history\nFiltered messages\nMessage requests\n"
                "Spam folder\nArchived chats\nChat themes\n"
                "Quick replies\nAway message\nIcebreakers\n"
                "Greeting message\nAutomatic alternative text\n"
                "Generate alt text\nEdit alt text\nAccessibility settings\n"
                "Kỷ niệm của bạn\nXem tổng kết năm của bạn\n"
                "Quản lý hoạt động\nXóa hoạt động\nTải nhật ký hoạt động\n"
                "Lọc theo ngày\nLọc theo danh mục\nMinh bạch trang\n"
                "Xem thông tin Trang\nCài đặt Trang\n"
                "Mời mọi người thích Trang\nQuảng cáo Trang\n"
                "Tạo chiến dịch quảng cáo\nQuảng cáo lại\n"
                "Xem kết quả quảng cáo\nTrình quản lý quảng cáo\n"
                "Thanh toán và hóa đơn\nLịch sử thanh toán\n"
                "Tin nhắn đã lọc\nYêu cầu tin nhắn\nThư mục spam\n"
                "Đoạn chat đã lưu trữ\nChủ đề chat\nTrả lời nhanh\n"
                "Tin nhắn vắng mặt\nCâu mở đầu\nTin nhắn chào mừng\n"
                "Văn bản thay thế tự động\nTạo văn bản thay thế\n"
                "Chỉnh sửa văn bản thay thế\nCài đặt trợ năng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nPromote Page\nGenerate alt text\n"
                    "Quảng cáo Trang",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPromote Page only after you finish the creative brief"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Promote Page only after you finish the creative brief",
        )

    def test_notes_rooms_chrome_noter_vn_keys_and_horizon_urls(self) -> None:
        noter = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người ghi chú": "Lan Note",
                "thời gian ghi chú": "6h",
            }
        )
        self.assertIsNotNone(noter)
        assert noter is not None
        self.assertEqual(noter.author_name, "Lan Note")
        self.assertEqual(noter.created_time, "6h")
        templater = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người tạo mẫu": "Minh",
                "ngày tạo mẫu": "hôm nay",
                "note_comment_id": "1357",
            }
        )
        self.assertIsNotNone(templater)
        assert templater is not None
        self.assertEqual(templater.author_name, "Minh")
        self.assertEqual(templater.created_time, "hôm nay")
        self.assertEqual(templater.comment_id, "1357")
        joiner = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tham gia phòng": "Bo",
                "noted_at": "05:45",
                "room_comment_id": "2468",
            }
        )
        self.assertIsNotNone(joiner)
        assert joiner is not None
        self.assertEqual(joiner.author_name, "Bo")
        self.assertEqual(joiner.created_time, "05:45")
        self.assertEqual(joiner.comment_id, "2468")
        rater = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "note_author": "An",
                "templated_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(rater)
        assert rater is not None
        self.assertEqual(rater.author_name, "An")
        self.assertEqual(rater.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"template_creator": "Lan"}), "Lan")
        self.assertEqual(person_name({"room_joiner": "Bo"}), "Bo")
        self.assertEqual(person_name({"người ghi chú": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"joined_room_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày tạo mẫu": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"noted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"note_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"room_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"template_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/horizon/world/111222/"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/communitynotes/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/templates/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/rooms/join/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=k.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nJoin Workplace\nWorkplace chat\nCreate a team\n"
                "Open Horizon World\nJoin World\nQuest settings\n"
                "Cast to Quest\nCreate a room\nRoom link\n"
                "Copy room link\nEnd room for all\nStop sharing\n"
                "Add a note\nCommunity note\nRate this note\n"
                "See fact check\nRelated articles\nMore about this\n"
                "Use template\nSee templates\nCreate from template\n"
                "Remix with template\nWrite on timeline\n"
                "Send birthday wish\nSee birthday posts\n"
                "Tham gia Workplace\nChat Workplace\nTạo nhóm làm việc\n"
                "Mở Horizon World\nTham gia World\nCài đặt Quest\n"
                "Chiếu lên Quest\nTạo phòng\nLiên kết phòng\n"
                "Sao chép liên kết phòng\nKết thúc phòng cho mọi người\n"
                "Dừng chia sẻ\nThêm ghi chú\nGhi chú cộng đồng\n"
                "Đánh giá ghi chú này\nXem kiểm chứng\n"
                "Bài viết liên quan\nThêm về điều này\nDùng mẫu\n"
                "Xem mẫu\nTạo từ mẫu\nPhối với mẫu\n"
                "Viết lên dòng thời gian\nGửi lời chúc sinh nhật\n"
                "Xem bài sinh nhật"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCommunity note\nUse template\n"
                    "Ghi chú cộng đồng",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCommunity note only after you read the source carefully"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Community note only after you read the source carefully",
        )

    def test_cart_stars_chrome_shopper_vn_keys_and_cart_urls(self) -> None:
        shopper = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người mua sắm": "Lan Cart",
                "thời gian thêm giỏ": "6h",
            }
        )
        self.assertIsNotNone(shopper)
        assert shopper is not None
        self.assertEqual(shopper.author_name, "Lan Cart")
        self.assertEqual(shopper.created_time, "6h")
        cheer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người cổ vũ": "Minh",
                "ngày cổ vũ": "hôm nay",
                "cart_comment_id": "1357",
            }
        )
        self.assertIsNotNone(cheer)
        assert cheer is not None
        self.assertEqual(cheer.author_name, "Minh")
        self.assertEqual(cheer.created_time, "hôm nay")
        self.assertEqual(cheer.comment_id, "1357")
        listed = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người trong danh sách": "Bo",
                "carted_at": "05:45",
                "stars_comment_id": "2468",
            }
        )
        self.assertIsNotNone(listed)
        assert listed is not None
        self.assertEqual(listed.author_name, "Bo")
        self.assertEqual(listed.created_time, "05:45")
        self.assertEqual(listed.comment_id, "2468")
        restricted = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "list_owner": "An",
                "cheered_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(restricted)
        assert restricted is not None
        self.assertEqual(restricted.author_name, "An")
        self.assertEqual(restricted.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"streamer_fan": "Lan"}), "Lan")
        self.assertEqual(person_name({"restricted_user": "Bo"}), "Bo")
        self.assertEqual(person_name({"người mua sắm": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"listed_friend_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày cổ vũ": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"carted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"cart_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"stars_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"list_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/cart/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/gifts/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/friends/lists/?list_id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=d.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=m.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nSpectate game\nFollow streamer\nSubscribe to channel\n"
                "Send Stars\nCheer with Stars\nOpen gift shop\n"
                "Claim reward\nDaily streak\nLevel up\n"
                "View cart\nContinue shopping\nSelect size\n"
                "Select color\nNotify me when available\nFree shipping\n"
                "Estimated delivery\nCreate friend list\nEdit friend list\n"
                "Add to list\nRemove from list\nSee friend lists\n"
                "Add to Restricted list\nRemove from Restricted\n"
                "See Restricted list\n"
                "Xem trận đấu\nTheo dõi streamer\nĐăng ký kênh\n"
                "Gửi Stars\nCổ vũ bằng Stars\nMở cửa hàng quà\n"
                "Nhận phần thưởng\nChuỗi ngày\nLên cấp\n"
                "Xem giỏ hàng\nTiếp tục mua sắm\nChọn kích cỡ\n"
                "Chọn màu\nBáo tôi khi có hàng\nMiễn phí vận chuyển\n"
                "Thời gian giao dự kiến\nTạo danh sách bạn bè\n"
                "Chỉnh sửa danh sách bạn bè\nThêm vào danh sách\n"
                "Xóa khỏi danh sách\nXem danh sách bạn bè\n"
                "Thêm vào danh sách Hạn chế\nXóa khỏi Hạn chế\n"
                "Xem danh sách Hạn chế"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCheer with Stars\nView cart\n"
                    "Cổ vũ bằng Stars",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCheer with Stars only after the stream starts tonight"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Cheer with Stars only after the stream starts tonight",
        )

    def test_family_verified_chrome_supervisor_vn_keys_and_capcut_urls(self) -> None:
        supervisor = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người giám sát": "Lan Parent",
                "thời gian giám sát": "6h",
            }
        )
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.author_name, "Lan Parent")
        self.assertEqual(supervisor.created_time, "6h")
        verified = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người xác minh": "Minh",
                "ngày xác minh": "hôm nay",
                "family_comment_id": "1357",
            }
        )
        self.assertIsNotNone(verified)
        assert verified is not None
        self.assertEqual(verified.author_name, "Minh")
        self.assertEqual(verified.created_time, "hôm nay")
        self.assertEqual(verified.comment_id, "1357")
        duet = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người song ca": "Bo",
                "supervised_at": "05:45",
                "verified_comment_id": "2468",
            }
        )
        self.assertIsNotNone(duet)
        assert duet is not None
        self.assertEqual(duet.author_name, "Bo")
        self.assertEqual(duet.created_time, "05:45")
        self.assertEqual(duet.comment_id, "2468")
        publisher = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "publication_follower": "An",
                "verified_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(publisher)
        assert publisher is not None
        self.assertEqual(publisher.author_name, "An")
        self.assertEqual(publisher.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"duet_partner": "Lan"}), "Lan")
        self.assertEqual(person_name({"family_supervisor": "Bo"}), "Bo")
        self.assertEqual(person_name({"người giám sát": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"dueted_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày xác minh": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"supervised_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"family_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"verified_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"duet_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/capcut/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/bulletin/998877/"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/familycenter/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_verified/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=l.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nDuet with\nStitch with\nCapCut edit\n"
                "Open in CapCut\nSee who likes you\nUpgrade to Premium\n"
                "Hide my age\nHide my distance\nBulletin article\n"
                "Follow publication\nSave article\nShare article\n"
                "Listen to article\nFamily Center\nSupervise account\n"
                "Set screen time\nSchedule breaks\nSee activity summary\n"
                "Parent controls\nGet Meta Verified\nManage subscription\n"
                "Cancel Meta Verified\nVerified badge\n"
                "Song ca với\nGhép với\nChỉnh sửa CapCut\n"
                "Mở trong CapCut\nXem ai thích bạn\nNâng cấp Premium\n"
                "Ẩn tuổi của tôi\nẨn khoảng cách của tôi\nBài Bulletin\n"
                "Theo dõi ấn phẩm\nLưu bài viết tin\nChia sẻ bài viết tin\n"
                "Nghe bài viết\nTrung tâm Gia đình\nGiám sát tài khoản\n"
                "Đặt thời gian sử dụng\nLên lịch nghỉ\n"
                "Xem tóm tắt hoạt động\nKiểm soát của phụ huynh\n"
                "Nhận Meta Verified\nQuản lý đăng ký\nHủy Meta Verified\n"
                "Huy hiệu đã xác minh"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nGet Meta Verified\nSupervise account\n"
                    "Nhận Meta Verified",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nGet Meta Verified only after you confirm the billing plan"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Get Meta Verified only after you confirm the billing plan",
        )

    def test_reviews_quiz_chrome_reviewer_vn_keys_and_shipment_urls(self) -> None:
        reviewer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người đánh giá": "Lan Review",
                "thời gian đánh giá": "6h",
            }
        )
        self.assertIsNotNone(reviewer)
        assert reviewer is not None
        self.assertEqual(reviewer.author_name, "Lan Review")
        self.assertEqual(reviewer.created_time, "6h")
        returner = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người trả hàng": "Minh",
                "ngày trả hàng": "hôm nay",
                "review_comment_id": "1357",
            }
        )
        self.assertIsNotNone(returner)
        assert returner is not None
        self.assertEqual(returner.author_name, "Minh")
        self.assertEqual(returner.created_time, "hôm nay")
        self.assertEqual(returner.comment_id, "1357")
        quizzer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người làm quiz": "Bo",
                "reviewed_at": "05:45",
                "return_comment_id": "2468",
            }
        )
        self.assertIsNotNone(quizzer)
        assert quizzer is not None
        self.assertEqual(quizzer.author_name, "Bo")
        self.assertEqual(quizzer.created_time, "05:45")
        self.assertEqual(quizzer.comment_id, "2468")
        donor = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "quiz_author": "An",
                "returned_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(donor)
        assert donor is not None
        self.assertEqual(donor.author_name, "An")
        self.assertEqual(donor.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"insights_viewer": "Lan"}), "Lan")
        self.assertEqual(person_name({"goal_setter": "Bo"}), "Bo")
        self.assertEqual(person_name({"người đánh giá": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"quizzed_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày trả hàng": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"reviewed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"review_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"return_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"quiz_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reviews/?review_id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/shipments/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/quiz/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/insights/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=b.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nRequest a return\nTrack shipment\nConfirm delivery\n"
                "Edit your review\nDelete your review\nHelpful review\n"
                "See reel insights\nAudience insights\nReach and impressions\n"
                "Average watch time\nTraffic sources\nCreate a quiz\n"
                "Add a question\nSee quiz results\nEnd quiz\n"
                "Set a goal\nUpdate goal\nThank donors\n"
                "Yêu cầu trả hàng\nTheo dõi lô hàng\nXác nhận đã nhận\n"
                "Chỉnh sửa đánh giá của bạn\nXóa đánh giá của bạn\n"
                "Đánh giá hữu ích\nXem thống kê reel\nThống kê đối tượng\n"
                "Phạm vi và số lần hiển thị\nThời gian xem trung bình\n"
                "Nguồn lưu lượng\nTạo câu đố\nThêm câu hỏi\n"
                "Xem kết quả câu đố\nKết thúc câu đố\nĐặt mục tiêu\n"
                "Cập nhật mục tiêu\nCảm ơn người quyên góp"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nRequest a return\nCreate a quiz\n"
                    "Yêu cầu trả hàng",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nRequest a return only after you open the package carefully"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Request a return only after you open the package carefully",
        )

    def test_avatar_offer_chrome_status_vn_keys_and_avatar_urls(self) -> None:
        avatar = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tạo avatar": "Lan Avatar",
                "thời gian trạng thái": "6h",
            }
        )
        self.assertIsNotNone(avatar)
        assert avatar is not None
        self.assertEqual(avatar.author_name, "Lan Avatar")
        self.assertEqual(avatar.created_time, "6h")
        offerer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người đề nghị": "Minh",
                "ngày đề nghị": "hôm nay",
                "avatar_comment_id": "1357",
            }
        )
        self.assertIsNotNone(offerer)
        assert offerer is not None
        self.assertEqual(offerer.author_name, "Minh")
        self.assertEqual(offerer.created_time, "hôm nay")
        self.assertEqual(offerer.comment_id, "1357")
        statuser = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người đặt trạng thái": "Bo",
                "status_at": "05:45",
                "offer_comment_id": "2468",
            }
        )
        self.assertIsNotNone(statuser)
        assert statuser is not None
        self.assertEqual(statuser.author_name, "Bo")
        self.assertEqual(statuser.created_time, "05:45")
        self.assertEqual(statuser.comment_id, "2468")
        member = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "status_author": "An",
                "offered_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(member.author_name, "An")
        self.assertEqual(member.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"offer_buyer": "Lan"}), "Lan")
        self.assertEqual(person_name({"membership_applicant": "Bo"}), "Bo")
        self.assertEqual(person_name({"người tạo avatar": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"avatar_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày đề nghị": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"status_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"avatar_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"offer_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"status_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/avatareditor/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/status/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/offer/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=j.333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/media/set/?set=f.777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nCreate avatar\nEdit avatar\nAvatar store\n"
                "Wear avatar\nTake selfie for avatar\nSet status\n"
                "Clear status\nCustomize status\nAuto captions\n"
                "Edit captions\nDownload reel\nRemix audio\n"
                "Agree to rules\nMembership questions\nAnswer questions\n"
                "Accept offer\nDecline offer\nCounter offer\n"
                "Withdraw offer\n"
                "Tạo avatar\nChỉnh sửa avatar\nCửa hàng avatar\n"
                "Đeo avatar\nChụp selfie cho avatar\nĐặt trạng thái\n"
                "Xóa trạng thái\nTùy chỉnh trạng thái\nPhụ đề tự động\n"
                "Chỉnh sửa phụ đề\nTải reel\nPhối âm thanh\n"
                "Đồng ý với quy tắc\nCâu hỏi thành viên\nTrả lời câu hỏi\n"
                "Chấp nhận đề nghị\nTừ chối đề nghị\nĐề nghị ngược\n"
                "Rút đề nghị"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCreate avatar\nAccept offer\n"
                    "Tạo avatar",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCreate avatar only after you finish the photo upload"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Create avatar only after you finish the photo upload",
        )

    def test_threads_privacy_chrome_crosspost_vn_keys_and_live_urls(self) -> None:
        crossposter = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người đăng Threads": "Lan Threads",
                "thời gian đăng chéo": "6h",
            }
        )
        self.assertIsNotNone(crossposter)
        assert crossposter is not None
        self.assertEqual(crossposter.author_name, "Lan Threads")
        self.assertEqual(crossposter.created_time, "6h")
        tagger = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người xem lại thẻ": "Minh",
                "ngày xem lại thẻ": "hôm nay",
                "threads_comment_id": "1357",
            }
        )
        self.assertIsNotNone(tagger)
        assert tagger is not None
        self.assertEqual(tagger.author_name, "Minh")
        self.assertEqual(tagger.created_time, "hôm nay")
        self.assertEqual(tagger.comment_id, "1357")
        scheduler = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người lên lịch live": "Bo",
                "crossposted_at": "05:45",
                "tag_comment_id": "2468",
            }
        )
        self.assertIsNotNone(scheduler)
        assert scheduler is not None
        self.assertEqual(scheduler.author_name, "Bo")
        self.assertEqual(scheduler.created_time, "05:45")
        self.assertEqual(scheduler.comment_id, "2468")
        blocker = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "live_scheduler": "An",
                "tag_reviewed_at": "2025-01-01T03:00:00Z",
            }
        )
        self.assertIsNotNone(blocker)
        assert blocker is not None
        self.assertEqual(blocker.author_name, "An")
        self.assertEqual(blocker.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(person_name({"threads_author": "Lan"}), "Lan")
        self.assertEqual(person_name({"listing_reporter": "Bo"}), "Bo")
        self.assertEqual(person_name({"người đăng Threads": "Minh"}), "Minh")
        self.assertEqual(timestamp_of({"live_scheduled_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ngày xem lại thẻ": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"crossposted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(first_comment_id({"threads_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"tag_comment_id": "44"}), "44")
        self.assertEqual(first_comment_id({"live_comment_id": "11"}), "11")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/share/threads/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/instagram/crosspost/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/effects/saved/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/live/schedule/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/privacy/tags/?id=777888"),
            "777888",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nShare to Threads\nCrosspost to Instagram\n"
                "Also share to Instagram\nPost to Threads\nView on Threads\n"
                "View on Instagram\nBrowse effects\nSaved effects\n"
                "Trending effects\nEffect details\nManage your privacy\n"
                "Who can see your posts\nWho can see your friends list\n"
                "Limit past posts\nReview tagged posts\nTimeline review\n"
                "Tag review\nMeet in a public place\nBring a friend\n"
                "Report this listing\nBlock seller\nBlock buyer\n"
                "Schedule live video\nGo live with a friend\n"
                "Invite to go live\nEnd live for viewers\n"
                "Chia sẻ lên Threads\nĐăng chéo lên Instagram\n"
                "Cũng chia sẻ lên Instagram\nĐăng lên Threads\n"
                "Xem trên Threads\nXem trên Instagram\nDuyệt hiệu ứng\n"
                "Hiệu ứng đã lưu\nHiệu ứng thịnh hành\nChi tiết hiệu ứng\n"
                "Quản lý quyền riêng tư của bạn\n"
                "Ai có thể xem danh sách bạn bè của bạn\n"
                "Giới hạn bài viết cũ\nXem lại bài viết được gắn thẻ\n"
                "Xem lại dòng thời gian\nXem lại gắn thẻ\n"
                "Gặp ở nơi công cộng\nMang theo bạn\n"
                "Báo cáo tin đăng này\nChặn người bán\nChặn người mua\n"
                "Lên lịch video trực tiếp\nPhát trực tiếp với bạn\n"
                "Mời phát trực tiếp\nKết thúc phát cho người xem"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nShare to Threads\nSchedule live video\n"
                    "Chia sẻ lên Threads",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nShare to Threads only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Share to Threads only after you finish editing the caption",
        )

    def test_fundraiser_poll_memorial_reel_share_vn_keys_and_urls(self) -> None:
        donor = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người quyên góp": "Lan Donor",
                "thời gian quyên góp": "6h",
            }
        )
        self.assertIsNotNone(donor)
        assert donor is not None
        self.assertEqual(donor.author_name, "Lan Donor")
        self.assertEqual(donor.created_time, "6h")
        voter = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người bình chọn": "Minh",
                "ngày bình chọn": "hôm nay",
                "poll_comment_id": "1357",
            }
        )
        self.assertIsNotNone(voter)
        assert voter is not None
        self.assertEqual(voter.author_name, "Minh")
        self.assertEqual(voter.created_time, "hôm nay")
        self.assertEqual(voter.comment_id, "1357")
        memorial = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tưởng niệm": "Bo",
                "donated_at": "05:45",
                "memorial_comment_id": "2468",
            }
        )
        self.assertIsNotNone(memorial)
        assert memorial is not None
        self.assertEqual(memorial.author_name, "Bo")
        self.assertEqual(memorial.created_time, "05:45")
        self.assertEqual(memorial.comment_id, "2468")
        gamer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người chơi game": "An",
                "ngày tưởng niệm": "2025-01-01T03:00:00Z",
                "game_comment_id": "9753",
            }
        )
        self.assertIsNotNone(gamer)
        assert gamer is not None
        self.assertEqual(gamer.author_name, "An")
        self.assertEqual(gamer.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(gamer.comment_id, "9753")
        self.assertEqual(person_name({"fundraiser_author": "Lan"}), "Lan")
        self.assertEqual(person_name({"poll_voter": "Bo"}), "Bo")
        self.assertEqual(person_name({"memorial_author": "Minh"}), "Minh")
        self.assertEqual(person_name({"gift_sender": "An"}), "An")
        self.assertEqual(person_name({"game_player": "Ha"}), "Ha")
        self.assertEqual(person_name({"người tạo quỹ": "Vy"}), "Vy")
        self.assertEqual(person_name({"người tạo thăm dò": "Tu"}), "Tu")
        self.assertEqual(person_name({"người gửi quà": "My"}), "My")
        self.assertEqual(timestamp_of({"voted_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"memorialized_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"gifted_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"played_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"fundraiser_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"gift_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/tournament/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/jobs/job/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/fundraisers/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/donate/?fundraiser_id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/polls/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/memorial/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/share/messenger/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/share/whatsapp/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/soundbites/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/podcasts/?id=909090"),
            "909090",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nSave reel\nShare reel\nRemix reel\n"
                "Mute conversation\nChange cover photo\n"
                "Update profile picture\nAsk for more info\n"
                "Send in WhatsApp\nOpen in browser\nCreate ad\n"
                "Edit privacy\nAudience restrictions\nContent preferences\n"
                "Follow back\nEdit public details\nManage sections\n"
                "Add life event\nPlay game\nXem cùng nhau\n"
                "Bắt đầu xem cùng nhau\nEnd poll\n"
                "Say happy birthday\nFriend suggestions\n"
                "Write a reply\nMost relevant\nComment as\n"
                "Go to memorial\nLeave a tribute\nSee tribute\n"
                "Continue fundraiser\nStop fundraiser\n"
                "Lưu Reels\nChia sẻ Reels\nPhối lại Reels\n"
                "Tắt tiếng cuộc trò chuyện\nĐổi ảnh bìa\n"
                "Cập nhật ảnh đại diện\nHỏi thêm thông tin\n"
                "Gửi trong WhatsApp\nMở trong trình duyệt\n"
                "Tạo quảng cáo\nChỉnh sửa quyền riêng tư\n"
                "Hạn chế đối tượng\nTuỳ chọn nội dung\n"
                "Theo dõi lại\nChỉnh sửa chi tiết công khai\n"
                "Quản lý các phần\nThêm sự kiện trong đời\n"
                "Chơi game\nKết thúc thăm dò\n"
                "Chúc mừng sinh nhật\nGợi ý bạn bè\n"
                "Viết trả lời\nLiên quan nhất\n"
                "Bình luận với tư cách\nĐến tưởng niệm\n"
                "Để lại lời tưởng niệm\nXem lời tưởng niệm\n"
                "Tiếp tục gây quỹ\nDừng gây quỹ"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nSave reel\nPlay game\nLưu Reels\nChơi game",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nSave reel only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Save reel only after you finish editing the caption",
        )

    def test_ar_qr_wishlist_profile_subscription_vn_keys_and_urls(self) -> None:
        filterer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người dùng bộ lọc": "Lan Filter",
                "ngày quét QR": "6h",
            }
        )
        self.assertIsNotNone(filterer)
        assert filterer is not None
        self.assertEqual(filterer.author_name, "Lan Filter")
        self.assertEqual(filterer.created_time, "6h")
        scanner = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người quét QR": "Minh",
                "wishlisted_at": "hôm nay",
                "qr_comment_id": "1357",
            }
        )
        self.assertIsNotNone(scanner)
        assert scanner is not None
        self.assertEqual(scanner.author_name, "Minh")
        self.assertEqual(scanner.created_time, "hôm nay")
        self.assertEqual(scanner.comment_id, "1357")
        locker = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người khóa hồ sơ": "Bo",
                "subscribed_at": "05:45",
                "subscription_comment_id": "2468",
            }
        )
        self.assertIsNotNone(locker)
        assert locker is not None
        self.assertEqual(locker.author_name, "Bo")
        self.assertEqual(locker.created_time, "05:45")
        self.assertEqual(locker.comment_id, "2468")
        collaber = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người hợp tác thương hiệu": "An",
                "ngày khóa hồ sơ": "2025-01-01T03:00:00Z",
                "collab_comment_id": "9753",
            }
        )
        self.assertIsNotNone(collaber)
        assert collaber is not None
        self.assertEqual(collaber.author_name, "An")
        self.assertEqual(collaber.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(collaber.comment_id, "9753")
        self.assertEqual(person_name({"wishlist_owner": "Lan"}), "Lan")
        self.assertEqual(person_name({"review_writer": "Bo"}), "Bo")
        self.assertEqual(person_name({"qr_scanner": "Minh"}), "Minh")
        self.assertEqual(person_name({"subscription_holder": "An"}), "An")
        self.assertEqual(person_name({"collab_applicant": "Ha"}), "Ha")
        self.assertEqual(person_name({"filter_user": "Vy"}), "Vy")
        self.assertEqual(person_name({"profile_locker": "Tu"}), "Tu")
        self.assertEqual(person_name({"người viết đánh giá": "My"}), "My")
        self.assertEqual(person_name({"người theo dõi đơn": "Na"}), "Na")
        self.assertEqual(person_name({"người đăng ký Meta": "Ki"}), "Ki")
        self.assertEqual(person_name({"người lên lịch bài": "Ly"}), "Ly")
        self.assertEqual(person_name({"caption_editor": "Do"}), "Do")
        self.assertEqual(timestamp_of({"scanned_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"filtered_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"collabed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"locked_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"wishlist_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"filter_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/wishlist/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/wishlist/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/commerce/shipping/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/feedback/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reviews/write/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ar/effect/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/camera_effects/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/qr/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/accounts_center/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/creator_marketplace/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/brandcollabs/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/professional_mode/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/lock_profile/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/subscriptions/?id=404040"),
            "404040",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/spark_ar/?id=505050"),
            "505050",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nUse this filter\nTry AR effect\nOpen camera effects\n"
                "Scan QR code\nShow QR code\nClear history\nManage history\n"
                "Take a break\nRemove as friend\nFocus mode\nQuiet mode\n"
                "Lock profile\nUnlock profile\nProfessional mode\n"
                "Switch to professional\nSwitch to personal\n"
                "Get Meta Verified\nRenew subscription\nSee subscription\n"
                "Creator Marketplace\nFind brand partners\nApply to collab\n"
                "Reuse content\nSchedule reel\nSee drafts\nDelete draft\n"
                "Publish draft\nAutomatically share to Instagram\n"
                "Connected experiences\nAccounts Center\n"
                "Why am I seeing this ad\nAbout this ad\nShop the look\n"
                "View products\nAdd to wishlist\nRemove from wishlist\n"
                "Track order\nMark as shipped\nConfirm delivery\n"
                "Leave seller feedback\nLeave buyer feedback\n"
                "Rate your experience\n"
                "Dùng bộ lọc này\nThử hiệu ứng AR\nMở hiệu ứng máy ảnh\n"
                "Quét mã QR\nHiện mã QR\nXóa lịch sử\nQuản lý lịch sử\n"
                "Tạm nghỉ\nHủy kết bạn\nChế độ tập trung\nChế độ yên tĩnh\n"
                "Khóa hồ sơ\nMở khóa hồ sơ\nChế độ chuyên nghiệp\n"
                "Chuyển sang chuyên nghiệp\nChuyển sang cá nhân\n"
                "Nhận Meta Đã xác minh\nGia hạn đăng ký\nXem đăng ký\n"
                "Thị trường nhà sáng tạo\nTìm đối tác thương hiệu\n"
                "Ứng tuyển hợp tác\nTái sử dụng nội dung\nLên lịch Reels\n"
                "Xem bản nháp\nXóa bản nháp\nĐăng bản nháp\n"
                "Tự động chia sẻ lên Instagram\nTrải nghiệm được kết nối\n"
                "Trung tâm tài khoản\nTại sao tôi thấy quảng cáo này\n"
                "Giới thiệu về quảng cáo này\nMua phong cách này\n"
                "Xem sản phẩm\nThêm vào danh sách mong muốn\n"
                "Xóa khỏi danh sách mong muốn\nTheo dõi đơn hàng\n"
                "Đánh dấu đã gửi\nXác nhận giao hàng\n"
                "Để lại phản hồi người bán\nĐể lại phản hồi người mua\n"
                "Đánh giá trải nghiệm của bạn"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nScan QR code\nLock profile\nQuét mã QR\nKhóa hồ sơ",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nScan QR code only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Scan QR code only after you finish editing the caption",
        )

    def test_events_dating_community_safety_vn_keys_and_urls(self) -> None:
        goer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tham gia sự kiện": "Lan Goer",
                "thời gian tham gia sự kiện": "6h",
            }
        )
        self.assertIsNotNone(goer)
        assert goer is not None
        self.assertEqual(goer.author_name, "Lan Goer")
        self.assertEqual(goer.created_time, "6h")
        buyer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người mua vé": "Minh",
                "ngày mua vé": "hôm nay",
                "ticket_comment_id": "1357",
            }
        )
        self.assertIsNotNone(buyer)
        assert buyer is not None
        self.assertEqual(buyer.author_name, "Minh")
        self.assertEqual(buyer.created_time, "hôm nay")
        self.assertEqual(buyer.comment_id, "1357")
        matcher = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người hẹn hò": "Bo",
                "matched_at": "05:45",
                "dating_comment_id": "2468",
            }
        )
        self.assertIsNotNone(matcher)
        assert matcher is not None
        self.assertEqual(matcher.author_name, "Bo")
        self.assertEqual(matcher.created_time, "05:45")
        self.assertEqual(matcher.comment_id, "2468")
        safer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người đánh dấu an toàn": "An",
                "ngày hẹn hò": "2025-01-01T03:00:00Z",
                "safety_comment_id": "9753",
            }
        )
        self.assertIsNotNone(safer)
        assert safer is not None
        self.assertEqual(safer.author_name, "An")
        self.assertEqual(safer.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(safer.comment_id, "9753")
        self.assertEqual(person_name({"event_goer": "Lan"}), "Lan")
        self.assertEqual(person_name({"ticket_buyer": "Bo"}), "Bo")
        self.assertEqual(person_name({"group_requester": "Minh"}), "Minh")
        self.assertEqual(person_name({"dating_matcher": "An"}), "An")
        self.assertEqual(person_name({"blood_donor": "Ha"}), "Ha")
        self.assertEqual(person_name({"safety_marker": "Vy"}), "Vy")
        self.assertEqual(person_name({"event_host": "Tu"}), "Tu")
        self.assertEqual(person_name({"climate_viewer": "My"}), "My")
        self.assertEqual(person_name({"người quan tâm sự kiện": "Na"}), "Na")
        self.assertEqual(person_name({"người tổ chức sự kiện": "Ki"}), "Ki")
        self.assertEqual(person_name({"người xin vào nhóm": "Ly"}), "Ly")
        self.assertEqual(person_name({"người hiến máu": "Do"}), "Do")
        self.assertEqual(timestamp_of({"rsvped_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ticketed_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"donated_blood_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"marked_safe_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"joined_group_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"event_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"blood_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/events/going/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/events/interested/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/events/create/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/tickets/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/join/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/rules/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/dating/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/dating/matches/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/climate/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/communityhelp/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/blooddonations/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/votinginformation/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/townhall/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/safetycheck/?id=404040"),
            "404040",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/crisisresponse/?id=505050"),
            "505050",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/workrooms/?id=606060"),
            "606060",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/horizon/home/?id=707070"),
            "707070",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/immersive/?id=808080"),
            "808080",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nInvite friends to event\nShare event\nSee ticket\n"
                "Transfer ticket\nCo-hosted by\nHosted by\nEvent details\n"
                "Upcoming events\nCancel event\nEdit event\nDuplicate event\n"
                "Export event\nGroup rules\nSee group rules\n"
                "Agree to group rules\nCreate announcement\nDating profile\n"
                "Send a like\nIt's a match\nKeep swiping\nHidden likes\n"
                "Your turns\nSee climate info\nCommunity Help\nOffer help\n"
                "Ask for help\nFind a blood drive\nI'm interested in donating\n"
                "Voting Information Center\nRegister to vote\n"
                "Check your registration\nFind your polling place\n"
                "Town Hall\nSafety Check\nMark yourself safe\nGet support\n"
                "Give support\nJoin Workrooms\nHorizon Worlds\nVisit world\n"
                "Create world\nEnter immersive\n"
                "Mời bạn bè đến sự kiện\nChia sẻ sự kiện\nXem vé\nChuyển vé\n"
                "Đồng tổ chức bởi\nĐược tổ chức bởi\nChi tiết sự kiện\n"
                "Sự kiện sắp tới\nHủy sự kiện\nChỉnh sửa sự kiện\n"
                "Nhân bản sự kiện\nXuất sự kiện\nNội quy nhóm\n"
                "Xem nội quy nhóm\nĐồng ý với nội quy nhóm\nTạo thông báo\n"
                "Hồ sơ Hẹn hò\nGửi lượt thích\nĐó là một cặp đôi\n"
                "Tiếp tục vuốt\nLượt thích ẩn\nLượt của bạn\n"
                "Xem thông tin khí hậu\nTrợ giúp cộng đồng\n"
                "Đề nghị giúp đỡ\nXin giúp đỡ\nTìm điểm hiến máu\n"
                "Tôi muốn hiến máu\nTrung tâm Thông tin Bầu cử\n"
                "Đăng ký bỏ phiếu\nKiểm tra đăng ký của bạn\n"
                "Tìm địa điểm bỏ phiếu\nĐối thoại\nKiểm tra An toàn\n"
                "Đánh dấu bạn an toàn\nNhận hỗ trợ\nĐưa hỗ trợ\n"
                "Tham gia Workrooms\nThăm thế giới\nTạo thế giới\n"
                "Vào chế độ đắm chìm"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nShare event\nSafety Check\nChia sẻ sự kiện\n"
                    "Kiểm tra An toàn",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nShare event only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Share event only after you finish editing the caption",
        )

    def test_marketplace_jobs_profile_favorites_vn_keys_and_urls(self) -> None:
        offerer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người đưa đề nghị": "Lan Offer",
                "thời gian đề nghị mua": "6h",
            }
        )
        self.assertIsNotNone(offerer)
        assert offerer is not None
        self.assertEqual(offerer.author_name, "Lan Offer")
        self.assertEqual(offerer.created_time, "6h")
        applicant = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người ứng tuyển việc": "Minh",
                "ngày ứng tuyển": "hôm nay",
                "job_comment_id": "1357",
            }
        )
        self.assertIsNotNone(applicant)
        assert applicant is not None
        self.assertEqual(applicant.author_name, "Minh")
        self.assertEqual(applicant.created_time, "hôm nay")
        self.assertEqual(applicant.comment_id, "1357")
        seller = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người bán marketplace": "Bo",
                "applied_at": "05:45",
                "listing_comment_id": "2468",
            }
        )
        self.assertIsNotNone(seller)
        assert seller is not None
        self.assertEqual(seller.author_name, "Bo")
        self.assertEqual(seller.created_time, "05:45")
        self.assertEqual(seller.comment_id, "2468")
        mutual = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người bạn chung": "An",
                "ngày lưu việc": "2025-01-01T03:00:00Z",
                "mutual_comment_id": "9753",
            }
        )
        self.assertIsNotNone(mutual)
        assert mutual is not None
        self.assertEqual(mutual.author_name, "An")
        self.assertEqual(mutual.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(mutual.comment_id, "9753")
        self.assertEqual(person_name({"offer_maker": "Lan"}), "Lan")
        self.assertEqual(person_name({"job_applicant": "Bo"}), "Bo")
        self.assertEqual(person_name({"mutual_friend": "Minh"}), "Minh")
        self.assertEqual(person_name({"listing_seller": "An"}), "An")
        self.assertEqual(person_name({"favorite_owner": "Ha"}), "Ha")
        self.assertEqual(person_name({"listing_buyer": "Vy"}), "Vy")
        self.assertEqual(person_name({"người mua marketplace": "Tu"}), "Tu")
        self.assertEqual(person_name({"người lưu việc": "My"}), "My")
        self.assertEqual(person_name({"người thêm trường": "Na"}), "Na")
        self.assertEqual(timestamp_of({"offered_buy_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"job_saved_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"friended_mutual_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"favorited_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"listed_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"offer_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"favorite_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/create/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/you/selling/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/you/buying/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/item/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/offers/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/jobs/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/jobs/application/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/jobs/saved/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/profile/education/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/profile/work/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/friends/mutual/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/favorites/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/feed/preferences/?id=303030"),
            "303030",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nMake an offer\nCounteroffer\nOffer details\n"
                "Pending offers\nSold items\nMark as pending\nShare listing\n"
                "Message about listing\nSeller information\nBuyer information\n"
                "Preferred meetup\nPublic meetup\nShipping available\n"
                "Local pickup\nCondition: New\nCondition: Used\n"
                "Price reduced\nFree stuff\nProperty rentals\n"
                "Jobs on Marketplace\nJob alerts\nSee company\n"
                "About the job\nJob details\nRequired qualifications\n"
                "Preferred qualifications\nWork location\nFull-time\n"
                "Part-time\nAdd school\nAdd college\nStudied at\n"
                "Works at\nLives in\nMarried to\nIn a relationship with\n"
                "Engaged to\nMutual friends\nSee mutual friends\n"
                "Add to favorites\nRemove from favorites\nEdit shortcuts\n"
                "Pin to top\nUnpin from top\nSee feed preferences\n"
                "Đưa ra đề nghị mua\nĐề nghị lại\nChi tiết đề nghị\n"
                "Đề nghị đang chờ\nMặt hàng đã bán\nĐánh dấu đang chờ\n"
                "Chia sẻ tin đăng\nNhắn tin về tin đăng\n"
                "Thông tin người bán\nThông tin người mua\n"
                "Nơi gặp mặt ưa thích\nGặp mặt công cộng\nCó giao hàng\n"
                "Nhận tại chỗ\nTình trạng: Mới\nTình trạng: Đã dùng\n"
                "Đã giảm giá\nĐồ miễn phí\nCho thuê nhà\n"
                "Việc làm trên Marketplace\nThông báo việc làm\n"
                "Xem công ty\nVề việc làm\nChi tiết việc làm\n"
                "Trình độ yêu cầu\nTrình độ ưu tiên\nNơi làm việc\n"
                "Toàn thời gian\nBán thời gian\nThêm trường\n"
                "Thêm đại học\nHọc tại\nLàm việc tại\nSống tại\n"
                "Kết hôn với\nĐang hẹn hò với\nĐính hôn với\n"
                "Bạn chung\nXem bạn chung\nThêm vào yêu thích\n"
                "Xóa khỏi yêu thích\nChỉnh sửa lối tắt\nGhim lên đầu\n"
                "Bỏ ghim khỏi đầu\nXem tùy chọn bảng tin"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nShare listing\nJob alerts\nChia sẻ tin đăng\n"
                    "Thông báo việc làm",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nShare listing only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Share listing only after you finish editing the caption",
        )


    def test_stories_reels_pages_inbox_stickers_vn_keys_and_urls(self) -> None:
        story = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tạo tin": "Lan Story",
                "thời gian tạo tin": "6h",
            }
        )
        self.assertIsNotNone(story)
        assert story is not None
        self.assertEqual(story.author_name, "Lan Story")
        self.assertEqual(story.created_time, "6h")
        sticker = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người gắn nhãn dán": "Minh",
                "ngày gắn nhãn": "hôm nay",
                "sticker_comment_id": "1357",
            }
        )
        self.assertIsNotNone(sticker)
        assert sticker is not None
        self.assertEqual(sticker.author_name, "Minh")
        self.assertEqual(sticker.created_time, "hôm nay")
        self.assertEqual(sticker.comment_id, "1357")
        admin = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người quản lý trang": "Bo",
                "reeled_at": "05:45",
                "page_role_comment_id": "2468",
            }
        )
        self.assertIsNotNone(admin)
        assert admin is not None
        self.assertEqual(admin.author_name, "Bo")
        self.assertEqual(admin.created_time, "05:45")
        self.assertEqual(admin.comment_id, "2468")
        inboxer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người trả lời hộp thư": "An",
                "ngày tạo Reels": "2025-01-01T03:00:00Z",
                "inbox_reply_comment_id": "9753",
            }
        )
        self.assertIsNotNone(inboxer)
        assert inboxer is not None
        self.assertEqual(inboxer.author_name, "An")
        self.assertEqual(inboxer.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(inboxer.comment_id, "9753")
        self.assertEqual(person_name({"story_creator": "Lan"}), "Lan")
        self.assertEqual(person_name({"reel_creator": "Bo"}), "Bo")
        self.assertEqual(person_name({"template_user": "Minh"}), "Minh")
        self.assertEqual(person_name({"sticker_user": "An"}), "An")
        self.assertEqual(person_name({"page_admin": "Ha"}), "Ha")
        self.assertEqual(person_name({"inbox_replier": "Vy"}), "Vy")
        self.assertEqual(person_name({"close_friend": "Tu"}), "Tu")
        self.assertEqual(person_name({"người tạo Reels": "My"}), "My")
        self.assertEqual(person_name({"người dùng mẫu": "Na"}), "Na")
        self.assertEqual(person_name({"người bạn thân": "Ki"}), "Ki")
        self.assertEqual(timestamp_of({"storied_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"stickered_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"paged_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"inbox_replied_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"close_friended_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"story_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"close_friend_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stories/create/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stories/archive/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stories/highlight/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/create/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/audio/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/sequence/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/transparency/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/roles/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/quality/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/cta/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/inbox/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/close_friends/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stickers/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/magic_editor/?id=404040"),
            "404040",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/saved_replies/?id=505050"),
            "505050",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/icebreakers/?id=606060"),
            "606060",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nStory privacy\nClose friends list\nEdit close friends\n"
                "See story insights\nHighlight story\nNew highlight\n"
                "Edit highlight\nDelete highlight\nDraft reels\n"
                "Remix with this reel\nSee original reel\nCreate sequence\n"
                "Add to sequence\nTry template\nSave template\n"
                "Text to speech\nAdd voiceover\nMagic editor\n"
                "Music sticker\nLocation sticker\nMention sticker\n"
                "Poll sticker\nQuiz sticker\nCountdown sticker\n"
                "Link sticker\nAdd sticker\nGIF sticker\nEmoji slider\n"
                "Questions sticker\nAdd yours sticker\nCollab sticker\n"
                "Page quality\nSee page transparency\nPage roles\n"
                "Assign page role\nRemove page role\nPage info\n"
                "Edit page info\nCall to action\nEdit call to action\n"
                "WhatsApp number\nContact us\nShop now button\n"
                "Sign up button\nWatch video button\nLearn more button\n"
                "Inbox settings\nAutomated responses\nIce breakers\n"
                "Frequently asked questions\nSaved replies\n"
                "Create saved reply\nLabel conversation\nMark as done\n"
                "Mark as unread\nSnooze conversation\nFollow up\n"
                "Quyền riêng tư của tin\nDanh sách bạn thân\n"
                "Chỉnh sửa bạn thân\nXem thông tin chi tiết tin\n"
                "Đưa tin vào nổi bật\nNổi bật mới\nChỉnh sửa nổi bật\n"
                "Xóa nổi bật\nBản nháp Reels\nPhối lại với Reels này\n"
                "Xem Reels gốc\nTạo chuỗi\nThêm vào chuỗi\nThử mẫu\n"
                "Lưu mẫu\nChữ thành giọng nói\nThêm lồng tiếng\n"
                "Trình chỉnh sửa ma thuật\nNhãn dán nhạc\n"
                "Nhãn dán vị trí\nNhãn dán nhắc đến\nNhãn dán thăm dò\n"
                "Nhãn dán đố vui\nNhãn dán đếm ngược\nNhãn dán liên kết\n"
                "Thêm nhãn dán\nNhãn dán GIF\nThanh trượt biểu tượng\n"
                "Nhãn dán câu hỏi\nNhãn dán thêm của bạn\n"
                "Nhãn dán cộng tác\nChất lượng Trang\nXem minh bạch Trang\n"
                "Vai trò Trang\nGán vai trò Trang\nGỡ vai trò Trang\n"
                "Thông tin Trang\nChỉnh sửa thông tin Trang\n"
                "Nút kêu gọi hành động\nChỉnh sửa nút kêu gọi hành động\n"
                "Số WhatsApp\nLiên hệ chúng tôi\nNút Mua ngay\n"
                "Nút Đăng ký\nNút Xem video\nNút Tìm hiểu thêm\n"
                "Cài đặt hộp thư\nPhản hồi tự động\nPhá băng\n"
                "Câu hỏi thường gặp\nCâu trả lời đã lưu\n"
                "Tạo câu trả lời đã lưu\nGắn nhãn cuộc trò chuyện\n"
                "Đánh dấu đã xong\nĐánh dấu chưa đọc\n"
                "Tạm ẩn cuộc trò chuyện\nTheo dõi tiếp"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nMagic editor\nPage quality\n"
                    "Trình chỉnh sửa ma thuật\nChất lượng Trang",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nMagic editor only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Magic editor only after you finish editing the caption",
        )


    def test_ads_notifications_search_security_vn_keys_and_urls(self) -> None:
        advertiser = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tạo quảng cáo": "Lan Ad",
                "thời gian tạo quảng cáo": "6h",
            }
        )
        self.assertIsNotNone(advertiser)
        assert advertiser is not None
        self.assertEqual(advertiser.author_name, "Lan Ad")
        self.assertEqual(advertiser.created_time, "6h")
        searcher = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người tìm kiếm": "Minh",
                "ngày tìm kiếm": "hôm nay",
                "search_comment_id": "1357",
            }
        )
        self.assertIsNotNone(searcher)
        assert searcher is not None
        self.assertEqual(searcher.author_name, "Minh")
        self.assertEqual(searcher.created_time, "hôm nay")
        self.assertEqual(searcher.comment_id, "1357")
        downloader = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tải dữ liệu": "Bo",
                "budgeted_at": "05:45",
                "data_comment_id": "2468",
            }
        )
        self.assertIsNotNone(downloader)
        assert downloader is not None
        self.assertEqual(downloader.author_name, "Bo")
        self.assertEqual(downloader.created_time, "05:45")
        self.assertEqual(downloader.comment_id, "2468")
        legacy = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người liên hệ di sản": "An",
                "ngày tải dữ liệu": "2025-01-01T03:00:00Z",
                "legacy_comment_id": "9753",
            }
        )
        self.assertIsNotNone(legacy)
        assert legacy is not None
        self.assertEqual(legacy.author_name, "An")
        self.assertEqual(legacy.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(legacy.comment_id, "9753")
        self.assertEqual(person_name({"ad_creator": "Lan"}), "Lan")
        self.assertEqual(person_name({"budget_setter": "Bo"}), "Bo")
        self.assertEqual(person_name({"audience_builder": "Minh"}), "Minh")
        self.assertEqual(person_name({"notif_viewer": "An"}), "An")
        self.assertEqual(person_name({"searcher": "Ha"}), "Ha")
        self.assertEqual(person_name({"data_downloader": "Vy"}), "Vy")
        self.assertEqual(person_name({"legacy_contact": "Tu"}), "Tu")
        self.assertEqual(person_name({"người đặt ngân sách": "My"}), "My")
        self.assertEqual(person_name({"người tạo đối tượng": "Na"}), "Na")
        self.assertEqual(person_name({"người xem thông báo": "Ki"}), "Ki")
        self.assertEqual(timestamp_of({"ad_created_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"searched_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"downloaded_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"notif_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"legacy_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"ad_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"audience_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ad_center/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/adsmanager/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ads/audience/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ads/billing/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/notifications/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/notifications/settings/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/search/recent/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/search/posts/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/activity_log/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/dyi/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/your_information/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/apps/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/security/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/legacy/?id=404040"),
            "404040",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/trusted_contacts/?id=505050"),
            "505050",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/security_checkup/?id=606060"),
            "606060",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nAd center\nBoost a post\nBoost a page\nAd budget\n"
                "Set budget\nDaily budget\nLifetime budget\nAd audience\n"
                "Create audience\nSaved audiences\nCustom audience\n"
                "Lookalike audience\nAd placements\nAutomatic placements\n"
                "Manual placements\nAd preview\nReview ad\nPublish ad\n"
                "Pause ad\nResume ad\nDuplicate ad\nAd insights\n"
                "See ad results\nBilling threshold\n"
                "See previous notifications\n"
                "Turn on notifications for post\n"
                "Turn off notifications for post\nSearch Facebook\n"
                "Clear recent searches\nFilter by people\nFilter by posts\n"
                "Filter by groups\nFilter by pages\nFilter by events\n"
                "See all results\nSee all mentions\nTransfer a copy\n"
                "Manage your information\nData about you\nRemove app\n"
                "Active sessions\nRecognize device\nDon't recognize\n"
                "Security checkup\nRun security checkup\n"
                "Choose friends to contact\nTrusted contacts\n"
                "Legacy contact\nChoose a legacy contact\n"
                "Trung tâm quảng cáo\nQuảng cáo bài viết\nQuảng cáo Trang\n"
                "Ngân sách quảng cáo\nĐặt ngân sách\nNgân sách hàng ngày\n"
                "Ngân sách tổng\nĐối tượng quảng cáo\nTạo đối tượng\n"
                "Đối tượng đã lưu\nĐối tượng tùy chỉnh\nĐối tượng tương tự\n"
                "Vị trí quảng cáo\nVị trí tự động\nVị trí thủ công\n"
                "Xem trước quảng cáo\nXem lại quảng cáo\nĐăng quảng cáo\n"
                "Tạm dừng quảng cáo\nTiếp tục quảng cáo\nNhân bản quảng cáo\n"
                "Thông tin chi tiết quảng cáo\nXem kết quả quảng cáo\n"
                "Ngưỡng thanh toán\nXem thông báo trước\n"
                "Bật thông báo cho bài viết\nTắt thông báo cho bài viết\n"
                "Tìm trên Facebook\nXóa tìm kiếm gần đây\nLọc theo người\n"
                "Lọc theo bài viết\nLọc theo nhóm\nLọc theo Trang\n"
                "Lọc theo sự kiện\nXem tất cả kết quả\n"
                "Xem tất cả lần nhắc đến\nChuyển bản sao\n"
                "Quản lý thông tin của bạn\nDữ liệu về bạn\nGỡ ứng dụng\n"
                "Phiên đang hoạt động\nNhận dạng thiết bị\nKhông nhận dạng\n"
                "Kiểm tra bảo mật\nChạy kiểm tra bảo mật\n"
                "Chọn bạn để liên hệ\nLiên hệ đáng tin\nLiên hệ di sản\n"
                "Chọn liên hệ di sản"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nAd center\nSecurity checkup\n"
                    "Trung tâm quảng cáo\nKiểm tra bảo mật",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nAd center only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Ad center only after you finish editing the caption",
        )


    def test_watch_gaming_stars_playlist_vn_keys_and_urls(self) -> None:
        watcher = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người xem sau": "Lan Watch",
                "thời gian xem sau": "6h",
            }
        )
        self.assertIsNotNone(watcher)
        assert watcher is not None
        self.assertEqual(watcher.author_name, "Lan Watch")
        self.assertEqual(watcher.created_time, "6h")
        star = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người gửi sao": "Minh",
                "ngày gửi sao": "hôm nay",
                "star_comment_id": "1357",
            }
        )
        self.assertIsNotNone(star)
        assert star is not None
        self.assertEqual(star.author_name, "Minh")
        self.assertEqual(star.created_time, "hôm nay")
        self.assertEqual(star.comment_id, "1357")
        streamer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người stream": "Bo",
                "challenged_at": "05:45",
                "stream_comment_id": "2468",
            }
        )
        self.assertIsNotNone(streamer)
        assert streamer is not None
        self.assertEqual(streamer.author_name, "Bo")
        self.assertEqual(streamer.created_time, "05:45")
        self.assertEqual(streamer.comment_id, "2468")
        gamer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người chơi instant": "An",
                "ngày stream": "2025-01-01T03:00:00Z",
                "challenge_comment_id": "9753",
            }
        )
        self.assertIsNotNone(gamer)
        assert gamer is not None
        self.assertEqual(gamer.author_name, "An")
        self.assertEqual(gamer.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(gamer.comment_id, "9753")
        self.assertEqual(person_name({"watch_later_user": "Lan"}), "Lan")
        self.assertEqual(person_name({"playlist_owner": "Bo"}), "Bo")
        self.assertEqual(person_name({"star_buyer": "Minh"}), "Minh")
        self.assertEqual(person_name({"streamer": "An"}), "An")
        self.assertEqual(person_name({"tournament_player": "Ha"}), "Ha")
        self.assertEqual(person_name({"challenger": "Vy"}), "Vy")
        self.assertEqual(person_name({"instant_gamer": "Tu"}), "Tu")
        self.assertEqual(person_name({"người tạo playlist": "My"}), "My")
        self.assertEqual(person_name({"người tham gia giải": "Na"}), "Na")
        self.assertEqual(person_name({"người thách đấu": "Ki"}), "Ki")
        self.assertEqual(timestamp_of({"watch_later_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"starred_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"streamed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"tournamented_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"instant_played_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"watch_later_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"tournament_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/watch/later/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/video/settings/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/captions/settings/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/stream/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/clips/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/leaderboard/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/challenge/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/instantgames/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/games/browse/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stars/buy/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stars/gift/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/live/badges/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/picture_in_picture/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/autoplay/?id=404040"),
            "404040",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nVideo settings\nPlayback settings\n"
                "Never autoplay videos\nWi-Fi only\nData saver\n"
                "Video quality\nHD quality\nStandard quality\n"
                "Always show captions\nDefault caption language\n"
                "Picture-in-picture\nAllow picture-in-picture\n"
                "Watch in original language\nDubbed audio\nAudio track\n"
                "Change audio track\nSave video to Watch later\n"
                "Watch later\nAdd to playlist\nYour playlists\n"
                "Edit playlist\nDelete playlist\nShare playlist\n"
                "Live badges\nSend a star\nStars balance\nBuy stars\n"
                "Gift stars\nFan badges\nBecome a supporter\n"
                "Subscription badges\nStreamer dashboard\n"
                "Go live on Gaming\nSchedule stream\nStream settings\n"
                "Clip this moment\nYour clips\nTournament details\n"
                "Leave tournament\nSee leaderboard\nChallenge a friend\n"
                "Accept challenge\nDecline challenge\nInstall game\n"
                "Open game\nGame requests\nIgnore game request\n"
                "Send game request\nInstant games\nBrowse games\n"
                "Trending games\nNew games\nYour games\n"
                "Cài đặt video\nCài đặt phát lại\n"
                "Không bao giờ tự động phát video\nChỉ Wi-Fi\n"
                "Tiết kiệm dữ liệu\nChất lượng video\nChất lượng HD\n"
                "Chất lượng tiêu chuẩn\nLuôn hiện phụ đề\n"
                "Ngôn ngữ phụ đề mặc định\nHình trong hình\n"
                "Cho phép hình trong hình\nXem bằng ngôn ngữ gốc\n"
                "Âm thanh lồng tiếng\nDải âm thanh\nĐổi dải âm thanh\n"
                "Lưu video vào Xem sau\nXem sau\n"
                "Thêm vào danh sách phát\nDanh sách phát của bạn\n"
                "Chỉnh sửa danh sách phát\nXóa danh sách phát\n"
                "Chia sẻ danh sách phát\nHuy hiệu trực tiếp\n"
                "Gửi một ngôi sao\nSố dư ngôi sao\nMua ngôi sao\n"
                "Tặng ngôi sao\nHuy hiệu người hâm mộ\n"
                "Trở thành người ủng hộ\nHuy hiệu đăng ký\n"
                "Bảng điều khiển streamer\nPhát trực tiếp trên Gaming\n"
                "Lên lịch stream\nCài đặt stream\nCắt khoảnh khắc này\n"
                "Clip của bạn\nChi tiết giải đấu\nRời giải đấu\n"
                "Xem bảng xếp hạng\nThách đấu bạn\nChấp nhận thách đấu\n"
                "Từ chối thách đấu\nCài game\nMở game\nLời mời game\n"
                "Bỏ qua lời mời game\nGửi lời mời game\nGame tức thì\n"
                "Duyệt game\nGame thịnh hành\nGame mới\nGame của bạn"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nWatch later\nBuy stars\nXem sau\nMua ngôi sao",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nWatch later only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Watch later only after you finish editing the caption",
        )


    def test_a11y_language_offline_upload_vn_keys_and_urls(self) -> None:
        a11y = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người chỉnh trợ năng": "Lan A11y",
                "thời gian đổi ngôn ngữ": "6h",
            }
        )
        self.assertIsNotNone(a11y)
        assert a11y is not None
        self.assertEqual(a11y.author_name, "Lan A11y")
        self.assertEqual(a11y.created_time, "6h")
        lang = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người đổi ngôn ngữ": "Minh",
                "ngày bật chế độ tối": "hôm nay",
                "language_comment_id": "1357",
            }
        )
        self.assertIsNotNone(lang)
        assert lang is not None
        self.assertEqual(lang.author_name, "Minh")
        self.assertEqual(lang.created_time, "hôm nay")
        self.assertEqual(lang.comment_id, "1357")
        beta = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tham gia beta": "Bo",
                "upload_retried_at": "05:45",
                "beta_comment_id": "2468",
            }
        )
        self.assertIsNotNone(beta)
        assert beta is not None
        self.assertEqual(beta.author_name, "Bo")
        self.assertEqual(beta.created_time, "05:45")
        self.assertEqual(beta.comment_id, "2468")
        uploader = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người tải lên lại": "An",
                "ngày tham gia beta": "2025-01-01T03:00:00Z",
                "upload_comment_id": "9753",
            }
        )
        self.assertIsNotNone(uploader)
        assert uploader is not None
        self.assertEqual(uploader.author_name, "An")
        self.assertEqual(uploader.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(uploader.comment_id, "9753")
        self.assertEqual(person_name({"accessibility_user": "Lan"}), "Lan")
        self.assertEqual(person_name({"language_setter": "Bo"}), "Bo")
        self.assertEqual(person_name({"dark_mode_user": "Minh"}), "Minh")
        self.assertEqual(person_name({"alt_text_editor": "An"}), "An")
        self.assertEqual(person_name({"beta_tester": "Ha"}), "Ha")
        self.assertEqual(person_name({"upload_retrier": "Vy"}), "Vy")
        self.assertEqual(person_name({"offline_poster": "Tu"}), "Tu")
        self.assertEqual(person_name({"người bật chế độ tối": "My"}), "My")
        self.assertEqual(person_name({"người thêm alt text": "Na"}), "Na")
        self.assertEqual(timestamp_of({"language_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"dark_mode_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"beta_joined_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"alt_texted_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"offline_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"a11y_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"alt_text_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/accessibility/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/language/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/dark_mode/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/text_size/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/media/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/data/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/beta/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/region/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/help/keyboard/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/alt_text/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/upload/queue/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/offline/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/composer/retry/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/settings/experiments/?id=404040"),
            "404040",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nAccessibility settings\nSee keyboard shortcuts\n"
                "Keyboard shortcuts\nScreen reader\nHigh contrast\n"
                "Reduce motion\nAutoplay control\nLink previews\n"
                "Alternative text\nAdd alt text\nEdit alt text\n"
                "Change language\nLanguage and region\nFacebook language\n"
                "Region format\nStart of week\nLight mode\nDark mode\n"
                "Use system setting\nCompact mode\nIncrease text size\n"
                "Decrease text size\nUpload HD\nUpload standard\n"
                "Data usage\nUse less data\nCellular data\nWi-Fi uploads\n"
                "Proxy settings\nDeveloper mode\nDebug logs\n"
                "Internal settings\nExperiment settings\nJoin beta\n"
                "Leave beta\nSomething went wrong\nTry again later\n"
                "Refresh feed\nPull to refresh\nNo internet connection\n"
                "You're offline\nGo back online\nQueued posts\n"
                "Post when online\nFailed to post\nRetry upload\n"
                "Cancel upload\nUpload progress\nProcessing video\n"
                "Video ready\n"
                "Cài đặt trợ năng\nXem phím tắt\nPhím tắt\n"
                "Trình đọc màn hình\nĐộ tương phản cao\nGiảm chuyển động\n"
                "Điều khiển phát tự động\nXem trước liên kết\n"
                "Văn bản thay thế\nThêm văn bản thay thế\n"
                "Chỉnh sửa văn bản thay thế\nĐổi ngôn ngữ\n"
                "Ngôn ngữ và khu vực\nNgôn ngữ Facebook\nĐịnh dạng khu vực\n"
                "Ngày bắt đầu tuần\nChế độ sáng\nChế độ tối\n"
                "Dùng cài đặt hệ thống\nChế độ thu gọn\nTăng cỡ chữ\n"
                "Giảm cỡ chữ\nTải lên HD\nTải lên tiêu chuẩn\n"
                "Mức dùng dữ liệu\nDùng ít dữ liệu hơn\nDữ liệu di động\n"
                "Tải lên qua Wi-Fi\nCài đặt proxy\nChế độ nhà phát triển\n"
                "Nhật ký gỡ lỗi\nCài đặt nội bộ\nCài đặt thử nghiệm\n"
                "Tham gia beta\nRời beta\nĐã xảy ra lỗi\nThử lại sau\n"
                "Làm mới bảng tin\nKéo để làm mới\n"
                "Không có kết nối internet\nBạn đang ngoại tuyến\n"
                "Quay lại trực tuyến\nBài viết đang chờ\n"
                "Đăng khi trực tuyến\nKhông đăng được\nThử tải lên lại\n"
                "Hủy tải lên\nTiến trình tải lên\nĐang xử lý video\n"
                "Video sẵn sàng"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nDark mode\nRetry upload\nChế độ tối\nThử tải lên lại",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nDark mode only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Dark mode only after you finish editing the caption",
        )


    def test_meta_ai_imagine_rewrite_try_on_vn_keys_and_urls(self) -> None:
        asker = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người hỏi Meta AI": "Lan AI",
                "thời gian hỏi Meta AI": "6h",
            }
        )
        self.assertIsNotNone(asker)
        assert asker is not None
        self.assertEqual(asker.author_name, "Lan AI")
        self.assertEqual(asker.created_time, "6h")
        imager = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người tạo ảnh AI": "Minh",
                "ngày tạo ảnh AI": "hôm nay",
                "ai_image_comment_id": "1357",
            }
        )
        self.assertIsNotNone(imager)
        assert imager is not None
        self.assertEqual(imager.author_name, "Minh")
        self.assertEqual(imager.created_time, "hôm nay")
        self.assertEqual(imager.comment_id, "1357")
        rewriter = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người viết lại AI": "Bo",
                "ai_labeled_at": "05:45",
                "ai_rewrite_comment_id": "2468",
            }
        )
        self.assertIsNotNone(rewriter)
        assert rewriter is not None
        self.assertEqual(rewriter.author_name, "Bo")
        self.assertEqual(rewriter.created_time, "05:45")
        self.assertEqual(rewriter.comment_id, "2468")
        tryer = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người thử đồ AI": "An",
                "ngày viết lại AI": "2025-01-01T03:00:00Z",
                "ai_try_on_comment_id": "9753",
            }
        )
        self.assertIsNotNone(tryer)
        assert tryer is not None
        self.assertEqual(tryer.author_name, "An")
        self.assertEqual(tryer.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(tryer.comment_id, "9753")
        self.assertEqual(person_name({"meta_ai_user": "Lan"}), "Lan")
        self.assertEqual(person_name({"ai_image_creator": "Bo"}), "Bo")
        self.assertEqual(person_name({"ai_rewriter": "Minh"}), "Minh")
        self.assertEqual(person_name({"ai_labeler": "An"}), "An")
        self.assertEqual(person_name({"ai_try_on_user": "Ha"}), "Ha")
        self.assertEqual(person_name({"ai_video_creator": "Vy"}), "Vy")
        self.assertEqual(person_name({"người gắn nhãn AI": "Tu"}), "Tu")
        self.assertEqual(timestamp_of({"meta_ai_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ai_imaged_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"ai_rewrote_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"ai_tried_on_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"ai_videoed_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"meta_ai_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"ai_video_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_ai/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_ai/chat/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_ai/imagine/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/stickers/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/rewrite/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/edit/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/video/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/voice/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/try_on/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/search/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ai/label/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/edits/ai/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/imagine_me/?id=303030"),
            "303030",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nChat with Meta AI\nImagine with Meta AI\n"
                "Ask Meta AI\nSearch with Meta AI\nGenerate image\n"
                "Edit with AI\nAI stickers\nCreate AI sticker\n"
                "Rewrite with AI\nShorten with AI\nExpand with AI\n"
                "Change tone\nMore formal\nMore casual\nFix spelling\n"
                "AI writing assistant\nSuggested replies\nSmart replies\n"
                "Translate with AI\nSummarize thread\nGenerated with AI\n"
                "This was made with AI\nLabel AI content\nEdits with AI\n"
                "Background restyle\nExpand image\nErase object\n"
                "Restyle image\nAI voice\nClone voice\nText to video\n"
                "AI video\nGenerate video\nImagine me\nYour AI look\n"
                "Try on with AI\nVirtual try-on\nShop with AI\n"
                "Ask about this product\nFind similar with AI\nAI search\n"
                "See sources\nAI may make mistakes\nFeedback on AI\n"
                "Good response\nBad response\nStop generating\n"
                "Continue generating\n"
                "Trò chuyện với Meta AI\nTưởng tượng với Meta AI\n"
                "Hỏi Meta AI\nTìm bằng Meta AI\nTạo hình ảnh\n"
                "Chỉnh sửa bằng AI\nNhãn dán AI\nTạo nhãn dán AI\n"
                "Viết lại bằng AI\nRút ngắn bằng AI\nMở rộng bằng AI\n"
                "Đổi giọng điệu\nTrang trọng hơn\nThoải mái hơn\n"
                "Sửa chính tả\nTrợ lý viết AI\nGợi ý trả lời\n"
                "Trả lời thông minh\nDịch bằng AI\nTóm tắt chuỗi\n"
                "Được tạo bằng AI\nNội dung này được tạo bằng AI\n"
                "Gắn nhãn nội dung AI\nChỉnh sửa với AI\n"
                "Đổi phong cách nền\nMở rộng ảnh\nXóa đối tượng\n"
                "Đổi phong cách ảnh\nGiọng AI\nNhân bản giọng nói\n"
                "Chữ thành video\nVideo AI\nTạo video\nTưởng tượng tôi\n"
                "Diện mạo AI của bạn\nThử đồ bằng AI\nThử ảo\n"
                "Mua sắm bằng AI\nHỏi về sản phẩm này\n"
                "Tìm tương tự bằng AI\nTìm kiếm AI\nXem nguồn\n"
                "AI có thể sai\nPhản hồi về AI\nPhản hồi tốt\n"
                "Phản hồi kém\nDừng tạo\nTiếp tục tạo"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nChat with Meta AI\nRewrite with AI\n"
                    "Trò chuyện với Meta AI\nViết lại bằng AI",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nChat with Meta AI only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Chat with Meta AI only after you finish editing the caption",
        )


    def test_reels_audio_editor_effects_vn_keys_and_urls(self) -> None:
        audio = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người dùng âm thanh": "Lan Audio",
                "thời gian dùng âm thanh": "6h",
            }
        )
        self.assertIsNotNone(audio)
        assert audio is not None
        self.assertEqual(audio.author_name, "Lan Audio")
        self.assertEqual(audio.created_time, "6h")
        trimmer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người cắt video": "Minh",
                "ngày cắt video": "hôm nay",
                "trim_comment_id": "1357",
            }
        )
        self.assertIsNotNone(trimmer)
        assert trimmer is not None
        self.assertEqual(trimmer.author_name, "Minh")
        self.assertEqual(trimmer.created_time, "hôm nay")
        self.assertEqual(trimmer.comment_id, "1357")
        effector = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người thêm hiệu ứng": "Bo",
                "green_screened_at": "05:45",
                "effect_comment_id2": "2468",
            }
        )
        self.assertIsNotNone(effector)
        assert effector is not None
        self.assertEqual(effector.author_name, "Bo")
        self.assertEqual(effector.created_time, "05:45")
        self.assertEqual(effector.comment_id, "2468")
        beauty = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người làm đẹp": "An",
                "ngày tải hiệu ứng": "2025-01-01T03:00:00Z",
                "beauty_comment_id": "9753",
            }
        )
        self.assertIsNotNone(beauty)
        assert beauty is not None
        self.assertEqual(beauty.author_name, "An")
        self.assertEqual(beauty.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(beauty.comment_id, "9753")
        self.assertEqual(person_name({"audio_user": "Lan"}), "Lan")
        self.assertEqual(person_name({"trim_editor": "Bo"}), "Bo")
        self.assertEqual(person_name({"effect_downloader": "Minh"}), "Minh")
        self.assertEqual(person_name({"green_screen_user": "An"}), "An")
        self.assertEqual(person_name({"beauty_user": "Ha"}), "Ha")
        self.assertEqual(person_name({"layout_editor": "Vy"}), "Vy")
        self.assertEqual(person_name({"người dùng phông xanh": "Tu"}), "Tu")
        self.assertEqual(timestamp_of({"audio_used_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"trimmed_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"effect_downloaded_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"beautified_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(timestamp_of({"layouted_at": "2024-01-05"}), "2024-01-05")
        self.assertEqual(first_comment_id({"audio_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"layout_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/audio/library/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/audio/trending/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/audio/saved/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/trim/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/speed/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/transitions/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/layout/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/timer/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/voice_effects/?id=787878"),
            "787878",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reels/green_screen/?id=909090"),
            "909090",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/effects/gallery/?id=101010"),
            "101010",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/effects/download/?id=202020"),
            "202020",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/camera/beauty/?id=303030"),
            "303030",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/camera/flash/?id=404040"),
            "404040",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nReels audio library\nTrending audio\nSaved audio\n"
                "Audio pages\nView audio credits\nManage audio\nRemove audio\n"
                "Mute audio\nUnmute audio\nCamera roll\nSelect multiple\n"
                "Trim video\nSplit clip\nReorder clips\nAdd clip\nDelete clip\n"
                "Adjust clips\nAlign clips\nAdd transition\nEffects gallery\n"
                "Download effect\nSave effect\nEffect info\nBy creator\n"
                "Follow creator\nReport effect\nVoice effects\nDeep voice\n"
                "Voice filter\nGreen screen gallery\nUpload green screen\n"
                "Reorder layout\nLeft right\nTop bottom\n3 second timer\n"
                "10 second timer\nHands-free\nHold to record\nTap to record\n"
                "Flip camera\nFlash on\nFlash off\nTouch up\n"
                "Thư viện âm thanh Reels\nÂm thanh thịnh hành\n"
                "Âm thanh đã lưu\nTrang âm thanh\nXem ghi công âm thanh\n"
                "Quản lý âm thanh\nGỡ âm thanh\nTắt tiếng âm thanh\n"
                "Bật tiếng âm thanh\nCuộn camera\nChọn nhiều\nCắt video\n"
                "Tách clip\nSắp xếp lại clip\nThêm clip\nXóa clip\n"
                "Điều chỉnh clip\nCăn chỉnh clip\nThêm chuyển cảnh\n"
                "Thư viện hiệu ứng\nTải hiệu ứng\nLưu hiệu ứng\n"
                "Thông tin hiệu ứng\nBởi nhà sáng tạo\nTheo dõi nhà sáng tạo\n"
                "Báo cáo hiệu ứng\nHiệu ứng giọng nói\nGiọng trầm\n"
                "Bộ lọc giọng\nThư viện phông xanh\nTải phông xanh lên\n"
                "Sắp xếp lại bố cục\nTrái phải\nTrên dưới\nHẹn giờ 3 giây\n"
                "Hẹn giờ 10 giây\nRảnh tay\nGiữ để quay\nChạm để quay\n"
                "Lật camera\nBật đèn flash\nTắt đèn flash\nChỉnh sửa nhẹ"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nTrending audio\nFlip camera\n"
                    "Âm thanh thịnh hành\nLật camera",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nTrending audio only after you finish editing the caption"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Trending audio only after you finish editing the caption",
        )

    def test_notes_podcasts_albums_collections_vn_keys_and_urls(self) -> None:
        note = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người viết ghi chú": "Lan Note",
                "ngày viết ghi chú": "6h",
            }
        )
        self.assertIsNotNone(note)
        assert note is not None
        self.assertEqual(note.author_name, "Lan Note")
        self.assertEqual(note.created_time, "6h")
        podcaster = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người nghe podcast": "Minh",
                "thời gian nghe podcast": "hôm nay",
                "podcast_comment_id": "1357",
            }
        )
        self.assertIsNotNone(podcaster)
        assert podcaster is not None
        self.assertEqual(podcaster.author_name, "Minh")
        self.assertEqual(podcaster.created_time, "hôm nay")
        self.assertEqual(podcaster.comment_id, "1357")
        albumer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tạo album": "Bo",
                "album_created_at": "05:45",
                "album_comment_id": "2468",
            }
        )
        self.assertIsNotNone(albumer)
        assert albumer is not None
        self.assertEqual(albumer.author_name, "Bo")
        self.assertEqual(albumer.created_time, "05:45")
        self.assertEqual(albumer.comment_id, "2468")
        collector = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người tạo bộ sưu tập": "An",
                "ngày lưu bộ sưu tập": "2025-01-01T03:00:00Z",
                "collection_comment_id": "9753",
            }
        )
        self.assertIsNotNone(collector)
        assert collector is not None
        self.assertEqual(collector.author_name, "An")
        self.assertEqual(collector.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(collector.comment_id, "9753")
        self.assertEqual(person_name({"note_author": "Lan"}), "Lan")
        self.assertEqual(person_name({"podcast_listener": "Bo"}), "Bo")
        self.assertEqual(person_name({"album_creator": "Minh"}), "Minh")
        self.assertEqual(person_name({"collection_saver": "An"}), "An")
        self.assertEqual(person_name({"người lưu bài": "Ha"}), "Ha")
        self.assertEqual(person_name({"người viết ghi chú": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"noted_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"podcast_listened_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"album_created_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"collection_saved_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"note_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"collection_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/notes/library/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/notes/drafts/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/podcasts/episodes/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/episodes/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/albums/create/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/albums/edit/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/collections/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/collections/view/?id=565656"),
            "565656",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/saved/collections/?id=787878"),
            "787878",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nWrite a note\nPublish note\nDraft note\n"
                "Note settings\nListen now\nSubscribe to podcast\n"
                "Episode details\nPlay episode\nPause episode\n"
                "Skip forward\nSkip back\nCreate album\n"
                "Add photos to album\nAlbum privacy\nEdit album\n"
                "Delete album\nView collection\n"
                "Viết ghi chú\nXuất bản ghi chú\nBản nháp ghi chú\n"
                "Cài đặt ghi chú\nNghe ngay\nĐăng ký podcast\n"
                "Chi tiết tập\nPhát tập\nTạm dừng tập\n"
                "Tua tới\nTua lùi\nTạo album\n"
                "Thêm ảnh vào album\nQuyền riêng tư album\n"
                "Chỉnh sửa album\nXóa album\nXem bộ sưu tập"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nListen now\nCreate album\n"
                    "Nghe ngay\nTạo album",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nListen now only after you finish writing the review"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Listen now only after you finish writing the review",
        )

    def test_gift_cards_community_chat_quiet_mode_vn_keys_and_urls(self) -> None:
        gifter = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người gửi thẻ quà": "Lan Gift",
                "ngày gửi thẻ quà": "6h",
            }
        )
        self.assertIsNotNone(gifter)
        assert gifter is not None
        self.assertEqual(gifter.author_name, "Lan Gift")
        self.assertEqual(gifter.created_time, "6h")
        chatter = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người tham gia chat cộng đồng": "Minh",
                "thời gian tham gia chat": "hôm nay",
                "chat_comment_id": "1357",
            }
        )
        self.assertIsNotNone(chatter)
        assert chatter is not None
        self.assertEqual(chatter.author_name, "Minh")
        self.assertEqual(chatter.created_time, "hôm nay")
        self.assertEqual(chatter.comment_id, "1357")
        quieter = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người bật chế độ yên tĩnh": "Bo",
                "quiet_mode_at": "05:45",
                "quiet_mode_comment_id": "2468",
            }
        )
        self.assertIsNotNone(quieter)
        assert quieter is not None
        self.assertEqual(quieter.author_name, "Bo")
        self.assertEqual(quieter.created_time, "05:45")
        self.assertEqual(quieter.comment_id, "2468")
        supervisor = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người giám sát": "An",
                "ngày giám sát": "2025-01-01T03:00:00Z",
                "supervision_comment_id": "9753",
            }
        )
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.author_name, "An")
        self.assertEqual(supervisor.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(supervisor.comment_id, "9753")
        self.assertEqual(person_name({"gift_sender": "Lan"}), "Lan")
        self.assertEqual(person_name({"chat_member": "Bo"}), "Bo")
        self.assertEqual(person_name({"quiet_mode_user": "Minh"}), "Minh")
        self.assertEqual(person_name({"supervisor": "An"}), "An")
        self.assertEqual(person_name({"người gửi thẻ quà": "Ha"}), "Ha")
        self.assertEqual(person_name({"parent_dashboard_user": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"gift_sent_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"chat_joined_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"quiet_mode_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"supervised_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"gift_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"supervision_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/giftcards/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/giftcards/send/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/communitychats/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/chats/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/quiet_mode/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/teen_accounts/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/supervision/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/take_a_break/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nBuy gift card\nRedeem gift card\nSend gift card\n"
                "Community chat\nChat settings\nMute chat\nLeave chat\n"
                "Invite to chat\nTurn on Quiet mode\nTurn off Quiet mode\n"
                "Schedule Quiet mode\nTeen Accounts\nSupervision tools\n"
                "Parent dashboard\nManage your time\nDaily limit\nReminder set\n"
                "Mua thẻ quà\nĐổi thẻ quà\nGửi thẻ quà\n"
                "Chat cộng đồng\nCài đặt chat\nTắt tiếng chat\nRời chat\n"
                "Mời vào chat\nBật chế độ yên tĩnh\nTắt chế độ yên tĩnh\n"
                "Lên lịch chế độ yên tĩnh\nTài khoản thanh thiếu niên\n"
                "Công cụ giám sát\nBảng điều khiển phụ huynh\n"
                "Quản lý thời gian của bạn\nGiới hạn hàng ngày\nĐã đặt lời nhắc"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nCommunity chat\nTurn on Quiet mode\n"
                    "Chat cộng đồng\nBật chế độ yên tĩnh",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nCommunity chat only after you finish the group review"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Community chat only after you finish the group review",
        )

    def test_whatsapp_collab_captions_templates_vn_keys_and_urls(self) -> None:
        wa = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người chia sẻ WhatsApp": "Lan WA",
                "ngày chia sẻ WhatsApp": "6h",
            }
        )
        self.assertIsNotNone(wa)
        assert wa is not None
        self.assertEqual(wa.author_name, "Lan WA")
        self.assertEqual(wa.created_time, "6h")
        collaber = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người collab": "Minh",
                "thời gian collab": "hôm nay",
                "collab_comment_id": "1357",
            }
        )
        self.assertIsNotNone(collaber)
        assert collaber is not None
        self.assertEqual(collaber.author_name, "Minh")
        self.assertEqual(collaber.created_time, "hôm nay")
        self.assertEqual(collaber.comment_id, "1357")
        captioner = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người chỉnh phụ đề": "Bo",
                "captioned_at": "05:45",
                "caption_comment_id": "2468",
            }
        )
        self.assertIsNotNone(captioner)
        assert captioner is not None
        self.assertEqual(captioner.author_name, "Bo")
        self.assertEqual(captioner.created_time, "05:45")
        self.assertEqual(captioner.comment_id, "2468")
        templater = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người dùng mẫu": "An",
                "ngày dùng mẫu": "2025-01-01T03:00:00Z",
                "template_comment_id": "9753",
            }
        )
        self.assertIsNotNone(templater)
        assert templater is not None
        self.assertEqual(templater.author_name, "An")
        self.assertEqual(templater.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(templater.comment_id, "9753")
        self.assertEqual(person_name({"whatsapp_sharer": "Lan"}), "Lan")
        self.assertEqual(person_name({"collaborator": "Bo"}), "Bo")
        self.assertEqual(person_name({"caption_editor": "Minh"}), "Minh")
        self.assertEqual(person_name({"template_user": "An"}), "An")
        self.assertEqual(person_name({"người mở Instagram": "Ha"}), "Ha")
        self.assertEqual(person_name({"instagram_opener": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"whatsapp_shared_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"collabed_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"captioned_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"template_used_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"whatsapp_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"template_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/whatsapp/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/whatsapp/share/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/instagram/crosspost/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/remix/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/collab/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/captions/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/templates/browse/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/templates/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nContinue on WhatsApp\nOpen WhatsApp\nShare to WhatsApp\n"
                "Open Instagram\nCollab invite\nAccept collab\nDecline collab\n"
                "Download captions\nSync to beat\nBeat matcher\n"
                "Browse templates\nTemplate details\n"
                "Tiếp tục trên WhatsApp\nMở WhatsApp\nChia sẻ lên WhatsApp\n"
                "Mở Instagram\nLời mời collab\nChấp nhận collab\nTừ chối collab\n"
                "Tải phụ đề\nĐồng bộ theo nhịp\nGhép nhịp\n"
                "Duyệt mẫu\nChi tiết mẫu"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nOpen WhatsApp\nAccept collab\n"
                    "Mở WhatsApp\nChấp nhận collab",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nOpen WhatsApp only after you finish the product review"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Open WhatsApp only after you finish the product review",
        )

    def test_security_newsletter_tags_friendship_vn_keys_and_urls(self) -> None:
        security = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người phê duyệt đăng nhập": "Lan Sec",
                "ngày phê duyệt đăng nhập": "6h",
            }
        )
        self.assertIsNotNone(security)
        assert security is not None
        self.assertEqual(security.author_name, "Lan Sec")
        self.assertEqual(security.created_time, "6h")
        subscriber = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người đăng ký bản tin": "Minh",
                "thời gian đăng ký bản tin": "hôm nay",
                "newsletter_comment_id": "1357",
            }
        )
        self.assertIsNotNone(subscriber)
        assert subscriber is not None
        self.assertEqual(subscriber.author_name, "Minh")
        self.assertEqual(subscriber.created_time, "hôm nay")
        self.assertEqual(subscriber.comment_id, "1357")
        tagger = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người gắn thẻ": "Bo",
                "tagged_at": "05:45",
                "tag_comment_id": "2468",
            }
        )
        self.assertIsNotNone(tagger)
        assert tagger is not None
        self.assertEqual(tagger.author_name, "Bo")
        self.assertEqual(tagger.created_time, "05:45")
        self.assertEqual(tagger.comment_id, "2468")
        friend = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người xem tình bạn": "An",
                "ngày xem tình bạn": "2025-01-01T03:00:00Z",
                "friendship_comment_id": "9753",
            }
        )
        self.assertIsNotNone(friend)
        assert friend is not None
        self.assertEqual(friend.author_name, "An")
        self.assertEqual(friend.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(friend.comment_id, "9753")
        self.assertEqual(person_name({"login_approver": "Lan"}), "Lan")
        self.assertEqual(person_name({"newsletter_subscriber": "Bo"}), "Bo")
        self.assertEqual(person_name({"tag_reviewer": "Minh"}), "Minh")
        self.assertEqual(person_name({"friendship_viewer": "An"}), "An")
        self.assertEqual(person_name({"người gắn thẻ": "Ha"}), "Ha")
        self.assertEqual(person_name({"security_key_user": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"login_approved_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"newsletter_subscribed_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"tagged_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"friendship_viewed_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"security_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"friendship_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/security/login/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/login_alerts/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/newsletters/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/newsletters/subscribe/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/tags/review/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/friendship/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/privacy_checkup/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/avatars/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nUse security key\nGenerate codes\nApprove login\n"
                "This wasn't me\nSubscribe to newsletter\nUnsubscribe from newsletter\n"
                "Bulletin home\nExpress yourself\nWho are you with\nPhoto tags\n"
                "Claim Page\nSee Page insights\nConfirm friend\nFriends since\n"
                "Review privacy\n"
                "Dùng khóa bảo mật\nTạo mã\nPhê duyệt đăng nhập\n"
                "Đây không phải tôi\nĐăng ký bản tin\nHủy đăng ký bản tin\n"
                "Trang chủ Bulletin\nThể hiện bản thân\nBạn đang ở cùng ai\n"
                "Thẻ ảnh\nYêu cầu quyền Page\nXem thông tin chi tiết Page\n"
                "Xác nhận bạn bè\nBạn bè từ\nXem lại quyền riêng tư"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nApprove login\nPhoto tags\n"
                    "Phê duyệt đăng nhập\nThẻ ảnh",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nApprove login only after you finish verifying the device"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Approve login only after you finish verifying the device",
        )

    def test_meta_verified_group_announce_dating_roses_vn_keys_and_urls(self) -> None:
        verified = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người quản lý Meta Verified": "Lan Verified",
                "ngày quản lý Meta Verified": "6h",
            }
        )
        self.assertIsNotNone(verified)
        assert verified is not None
        self.assertEqual(verified.author_name, "Lan Verified")
        self.assertEqual(verified.created_time, "6h")
        announcer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người ghim thông báo": "Minh",
                "thời gian ghim thông báo": "hôm nay",
                "announce_comment_id": "1357",
            }
        )
        self.assertIsNotNone(announcer)
        assert announcer is not None
        self.assertEqual(announcer.author_name, "Minh")
        self.assertEqual(announcer.created_time, "hôm nay")
        self.assertEqual(announcer.comment_id, "1357")
        inviter = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người mời vào nhóm": "Bo",
                "group_invited_at": "05:45",
                "group_invite_comment_id": "2468",
            }
        )
        self.assertIsNotNone(inviter)
        assert inviter is not None
        self.assertEqual(inviter.author_name, "Bo")
        self.assertEqual(inviter.created_time, "05:45")
        self.assertEqual(inviter.comment_id, "2468")
        rose = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người gửi hoa hồng": "An",
                "ngày gửi hoa hồng": "2025-01-01T03:00:00Z",
                "rose_comment_id": "9753",
            }
        )
        self.assertIsNotNone(rose)
        assert rose is not None
        self.assertEqual(rose.author_name, "An")
        self.assertEqual(rose.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(rose.comment_id, "9753")
        self.assertEqual(person_name({"meta_verified_manager": "Lan"}), "Lan")
        self.assertEqual(person_name({"announcement_pinner": "Bo"}), "Bo")
        self.assertEqual(person_name({"group_inviter": "Minh"}), "Minh")
        self.assertEqual(person_name({"rose_sender": "An"}), "An")
        self.assertEqual(person_name({"người gửi hoa hồng": "Ha"}), "Ha")
        self.assertEqual(person_name({"anonymous_poster": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"meta_verified_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"announcement_pinned_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"group_invited_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"rose_sent_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"verified_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"rose_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_verified/manage/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/meta_verified/benefits/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/announcements/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/pending/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/invite/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/dating/roses/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/dating/pass/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/roses/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nManage Meta Verified\nVerification badge\n"
                "Meta Verified benefits\nInvite friends to group\n"
                "Pending member requests\nAnonymous post\nPin announcement\n"
                "Unpin announcement\nUnschedule post\nSend rose\nPass for now\n"
                "Favorite this Reel\n"
                "Quản lý Meta Verified\nHuy hiệu xác minh\n"
                "Quyền lợi Meta Verified\nMời bạn bè vào nhóm\n"
                "Yêu cầu thành viên đang chờ\nBài đăng ẩn danh\n"
                "Ghim thông báo\nBỏ ghim thông báo\nHủy lên lịch bài viết\n"
                "Gửi hoa hồng\nBỏ qua tạm thời\nYêu thích Reel này"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nSend rose\nPin announcement\n"
                    "Gửi hoa hồng\nGhim thông báo",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nSend rose only after you finish the dating profile"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Send rose only after you finish the dating profile",
        )

    def test_bag_video_call_polling_spectate_vn_keys_and_urls(self) -> None:
        bagger = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người thêm vào túi": "Lan Bag",
                "ngày thêm vào túi": "6h",
            }
        )
        self.assertIsNotNone(bagger)
        assert bagger is not None
        self.assertEqual(bagger.author_name, "Lan Bag")
        self.assertEqual(bagger.created_time, "6h")
        caller = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người bắt đầu cuộc gọi": "Minh",
                "thời gian bắt đầu cuộc gọi": "hôm nay",
                "call_comment_id": "1357",
            }
        )
        self.assertIsNotNone(caller)
        assert caller is not None
        self.assertEqual(caller.author_name, "Minh")
        self.assertEqual(caller.created_time, "hôm nay")
        self.assertEqual(caller.comment_id, "1357")
        voter = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tìm điểm bỏ phiếu": "Bo",
                "polling_found_at": "05:45",
                "polling_comment_id": "2468",
            }
        )
        self.assertIsNotNone(voter)
        assert voter is not None
        self.assertEqual(voter.author_name, "Bo")
        self.assertEqual(voter.created_time, "05:45")
        self.assertEqual(voter.comment_id, "2468")
        spectator = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người xem giải đấu": "An",
                "ngày xem giải đấu": "2025-01-01T03:00:00Z",
                "spectate_comment_id": "9753",
            }
        )
        self.assertIsNotNone(spectator)
        assert spectator is not None
        self.assertEqual(spectator.author_name, "An")
        self.assertEqual(spectator.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(spectator.comment_id, "9753")
        self.assertEqual(person_name({"bag_adder": "Lan"}), "Lan")
        self.assertEqual(person_name({"call_starter": "Bo"}), "Bo")
        self.assertEqual(person_name({"polling_finder": "Minh"}), "Minh")
        self.assertEqual(person_name({"spectate_user": "An"}), "An")
        self.assertEqual(person_name({"người thêm vào túi": "Ha"}), "Ha")
        self.assertEqual(person_name({"monetization_manager": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"bag_added_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"call_started_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"polling_found_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"spectated_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"bag_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"spectate_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/bag/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/checkout/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/orders/track/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/rooms/call/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pollingplace/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/elections/reminders/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gaming/spectate/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/tournament/bracket/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nAdd to bag\nStart a video call\nFind polling place\n"
                "Election day reminders\nTournament bracket\nSpectate live\n"
                "Donate with\nCrossposting settings\nMonetization status\n"
                "Thêm vào túi\nBắt đầu cuộc gọi video\nTìm điểm bỏ phiếu\n"
                "Nhắc nhở ngày bầu cử\nBảng đấu giải\nXem trực tiếp giải\n"
                "Quyên góp bằng\nCài đặt đăng chéo\nTrạng thái kiếm tiền"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nAdd to bag\nSpectate live\n"
                    "Thêm vào túi\nXem trực tiếp giải",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nAdd to bag only after you finish comparing the sizes"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Add to bag only after you finish comparing the sizes",
        )

    def test_music_text_sticker_weather_work_vn_keys_and_urls(self) -> None:
        musician = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người chọn nhạc": "Lan Music",
                "ngày chọn nhạc": "6h",
            }
        )
        self.assertIsNotNone(musician)
        assert musician is not None
        self.assertEqual(musician.author_name, "Lan Music")
        self.assertEqual(musician.created_time, "6h")
        texter = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người chỉnh chữ": "Minh",
                "thời gian chỉnh chữ": "hôm nay",
                "text_comment_id": "1357",
            }
        )
        self.assertIsNotNone(texter)
        assert texter is not None
        self.assertEqual(texter.author_name, "Minh")
        self.assertEqual(texter.created_time, "hôm nay")
        self.assertEqual(texter.comment_id, "1357")
        stickerer = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người chọn nhãn dán": "Bo",
                "sticker_picked_at": "05:45",
                "sticker_store_comment_id": "2468",
            }
        )
        self.assertIsNotNone(stickerer)
        assert stickerer is not None
        self.assertEqual(stickerer.author_name, "Bo")
        self.assertEqual(stickerer.created_time, "05:45")
        self.assertEqual(stickerer.comment_id, "2468")
        weather = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người xem thời tiết": "An",
                "ngày xem thời tiết": "2025-01-01T03:00:00Z",
                "weather_comment_id": "9753",
            }
        )
        self.assertIsNotNone(weather)
        assert weather is not None
        self.assertEqual(weather.author_name, "An")
        self.assertEqual(weather.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(weather.comment_id, "9753")
        self.assertEqual(person_name({"music_picker": "Lan"}), "Lan")
        self.assertEqual(person_name({"text_editor": "Bo"}), "Bo")
        self.assertEqual(person_name({"sticker_shopper": "Minh"}), "Minh")
        self.assertEqual(person_name({"weather_viewer": "An"}), "An")
        self.assertEqual(person_name({"người chọn nhạc": "Ha"}), "Ha")
        self.assertEqual(person_name({"work_account_switcher": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"music_picked_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"text_edited_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"sticker_picked_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"weather_viewed_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"music_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"weather_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/music/library/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/sound/original/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/text/style/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stickers/store/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/gifs/store/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/weather/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/weather/forecast/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/work/accounts/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nUse this sound\nOriginal sound\nMusic library\n"
                "Apply effect\nRemove effect\nGenerate captions\n"
                "Edit text\nText style\nSticker store\nGIF store\n"
                "Local weather\nSee forecast\nWork accounts\nSwitch work account\n"
                "Dùng âm thanh này\nÂm thanh gốc\nThư viện nhạc\n"
                "Áp dụng hiệu ứng\nGỡ hiệu ứng\nTạo phụ đề\n"
                "Chỉnh chữ\nKiểu chữ\nCửa hàng nhãn dán\nCửa hàng GIF\n"
                "Thời tiết địa phương\nXem dự báo\nTài khoản công việc\n"
                "Chuyển tài khoản công việc"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nMusic library\nLocal weather\n"
                    "Thư viện nhạc\nThời tiết địa phương",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nMusic library only after you finish picking the beat"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Music library only after you finish picking the beat",
        )

    def test_profile_life_events_seller_poll_vn_keys_and_urls(self) -> None:
        lifer = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người thêm sự kiện cuộc sống": "Lan Life",
                "ngày thêm sự kiện cuộc sống": "6h",
            }
        )
        self.assertIsNotNone(lifer)
        assert lifer is not None
        self.assertEqual(lifer.author_name, "Lan Life")
        self.assertEqual(lifer.created_time, "6h")
        seller = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người nhắn người bán": "Minh",
                "thời gian nhắn người bán": "hôm nay",
                "seller_comment_id": "1357",
            }
        )
        self.assertIsNotNone(seller)
        assert seller is not None
        self.assertEqual(seller.author_name, "Minh")
        self.assertEqual(seller.created_time, "hôm nay")
        self.assertEqual(seller.comment_id, "1357")
        collector = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người xem bộ sưu tập kỹ thuật số": "Bo",
                "collectible_viewed_at": "05:45",
                "collectible_comment_id": "2468",
            }
        )
        self.assertIsNotNone(collector)
        assert collector is not None
        self.assertEqual(collector.author_name, "Bo")
        self.assertEqual(collector.created_time, "05:45")
        self.assertEqual(collector.comment_id, "2468")
        poller = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người thêm lựa chọn khảo sát": "An",
                "ngày thêm lựa chọn": "2025-01-01T03:00:00Z",
                "poll_option_comment_id": "9753",
            }
        )
        self.assertIsNotNone(poller)
        assert poller is not None
        self.assertEqual(poller.author_name, "An")
        self.assertEqual(poller.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(poller.comment_id, "9753")
        self.assertEqual(person_name({"life_event_adder": "Lan"}), "Lan")
        self.assertEqual(person_name({"seller_messenger": "Bo"}), "Bo")
        self.assertEqual(person_name({"collectible_viewer": "Minh"}), "Minh")
        self.assertEqual(person_name({"poll_option_adder": "An"}), "An")
        self.assertEqual(person_name({"người thêm sự kiện cuộc sống": "Ha"}), "Ha")
        self.assertEqual(person_name({"guest_inviter": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"life_event_added_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"seller_messaged_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"collectible_viewed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"poll_option_added_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"life_event_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"poll_option_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/life_events/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/about/contact/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/digital_collectibles/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/marketplace/seller/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/polls/create/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/events/invite/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/greenscreen/remix/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/profile/view_as/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nLife events\nWork and education\nPlaces lived\n"
                "Family and relationships\nContact and basic info\n"
                "Details about you\nFeatured photos\nView profile picture\n"
                "Digital collectibles\nMessage seller\nAdd option\n"
                "Not going\nGuest invite\nGreen screen remix\n"
                "How are you feeling\nFeeling/activity\n"
                "Sự kiện cuộc sống\nCông việc và học vấn\nNơi đã sống\n"
                "Gia đình và mối quan hệ\nLiên hệ và thông tin cơ bản\n"
                "Chi tiết về bạn\nẢnh nổi bật\nXem ảnh đại diện\n"
                "Bộ sưu tập kỹ thuật số\nNhắn tin người bán\nThêm lựa chọn\n"
                "Không tham gia\nMời khách\nRemix phông xanh\n"
                "Bạn đang cảm thấy thế nào\nCảm xúc/hoạt động"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nLife events\nMessage seller\n"
                    "Sự kiện cuộc sống\nNhắn tin người bán",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nLife events only after you finish updating the timeline"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Life events only after you finish updating the timeline",
        )

    def test_group_admin_messenger_media_reactions_vn_keys_and_urls(self) -> None:
        admin = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người đặt quản trị viên": "Lan Admin",
                "ngày đặt quản trị viên": "6h",
            }
        )
        self.assertIsNotNone(admin)
        assert admin is not None
        self.assertEqual(admin.author_name, "Lan Admin")
        self.assertEqual(admin.created_time, "6h")
        voicer = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người gửi tin nhắn thoại": "Minh",
                "thời gian gửi tin nhắn thoại": "hôm nay",
                "voice_comment_id": "1357",
            }
        )
        self.assertIsNotNone(voicer)
        assert voicer is not None
        self.assertEqual(voicer.author_name, "Minh")
        self.assertEqual(voicer.created_time, "hôm nay")
        self.assertEqual(voicer.comment_id, "1357")
        reactor = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người xem tất cả cảm xúc": "Bo",
                "reactions_viewed_at": "05:45",
                "reactions_comment_id": "2468",
            }
        )
        self.assertIsNotNone(reactor)
        assert reactor is not None
        self.assertEqual(reactor.author_name, "Bo")
        self.assertEqual(reactor.created_time, "05:45")
        self.assertEqual(reactor.comment_id, "2468")
        translator = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người dịch bài viết": "An",
                "ngày dịch bài viết": "2025-01-01T03:00:00Z",
                "translate_comment_id": "9753",
            }
        )
        self.assertIsNotNone(translator)
        assert translator is not None
        self.assertEqual(translator.author_name, "An")
        self.assertEqual(translator.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(translator.comment_id, "9753")
        self.assertEqual(person_name({"group_admin_maker": "Lan"}), "Lan")
        self.assertEqual(person_name({"voice_sender": "Bo"}), "Bo")
        self.assertEqual(person_name({"reactions_viewer": "Minh"}), "Minh")
        self.assertEqual(person_name({"post_translator": "An"}), "An")
        self.assertEqual(person_name({"người đặt quản trị viên": "Ha"}), "Ha")
        self.assertEqual(person_name({"group_follower": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"admin_made_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"voice_sent_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"reactions_viewed_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"translated_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"admin_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"translate_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/insights/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/snooze/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/groups/admins/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/messenger/voice/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/messenger/video/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/reactions/all/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/translate/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/save/link/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nSnooze group for 30 days\nFollow group\nUnfollow group\n"
                "Invite as admin\nMake admin\nRemove admin\n"
                "Voice message\nVideo message\nPhoto message\n"
                "React with\nSee all reactions\nTranslate post\nSave link\n"
                "Tạm ẩn nhóm trong 30 ngày\nTheo dõi nhóm\nBỏ theo dõi nhóm\n"
                "Mời làm quản trị viên\nĐặt làm quản trị viên\nGỡ quản trị viên\n"
                "Tin nhắn thoại\nTin nhắn video\nTin nhắn ảnh\n"
                "Thả cảm xúc\nXem tất cả cảm xúc\nDịch bài viết\nLưu liên kết"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nMake admin\nSee all reactions\n"
                    "Đặt làm quản trị viên\nXem tất cả cảm xúc",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nMake admin only after you finish reviewing the requests"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Make admin only after you finish reviewing the requests",
        )

    def test_profile_pin_shops_watching_support_vn_keys_and_urls(self) -> None:
        pinner = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người ghim trang cá nhân": "Lan Pin",
                "ngày ghim trang cá nhân": "6h",
            }
        )
        self.assertIsNotNone(pinner)
        assert pinner is not None
        self.assertEqual(pinner.author_name, "Lan Pin")
        self.assertEqual(pinner.created_time, "6h")
        shopper = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người gắn thẻ sản phẩm": "Minh",
                "thời gian gắn thẻ sản phẩm": "hôm nay",
                "product_tag_comment_id": "1357",
            }
        )
        self.assertIsNotNone(shopper)
        assert shopper is not None
        self.assertEqual(shopper.author_name, "Minh")
        self.assertEqual(shopper.created_time, "hôm nay")
        self.assertEqual(shopper.comment_id, "1357")
        watcher = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tiếp tục xem": "Bo",
                "continue_watched_at": "05:45",
                "watching_comment_id": "2468",
            }
        )
        self.assertIsNotNone(watcher)
        assert watcher is not None
        self.assertEqual(watcher.author_name, "Bo")
        self.assertEqual(watcher.created_time, "05:45")
        self.assertEqual(watcher.comment_id, "2468")
        supporter = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người mở hộp thư hỗ trợ": "An",
                "ngày mở hộp thư hỗ trợ": "2025-01-01T03:00:00Z",
                "support_comment_id": "9753",
            }
        )
        self.assertIsNotNone(supporter)
        assert supporter is not None
        self.assertEqual(supporter.author_name, "An")
        self.assertEqual(supporter.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(supporter.comment_id, "9753")
        self.assertEqual(person_name({"profile_pinner": "Lan"}), "Lan")
        self.assertEqual(person_name({"product_tagger": "Bo"}), "Bo")
        self.assertEqual(person_name({"continue_watcher": "Minh"}), "Minh")
        self.assertEqual(person_name({"support_inbox_user": "An"}), "An")
        self.assertEqual(person_name({"người ghim trang cá nhân": "Ha"}), "Ha")
        self.assertEqual(person_name({"brand_content_creator": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"profile_pinned_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"product_tagged_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"continue_watched_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"support_opened_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"pin_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"support_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/profile/pin/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/highlights/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/shops/collection/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/branded_content/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/collaboration/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/watch/continue/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/support_inbox/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/adpreferences/topics/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nPin to profile\nUnpin from profile\nFundraiser goal\n"
                "Stories archive\nSave story\nAd topics\nBrand content\n"
                "Collaboration post\nTag a product\nAdd products\n"
                "Shops collection\nBrowse shop\nContinue watching\nCast to TV\n"
                "Report problem\nSupport inbox\nAccount status\nAccount Center\n"
                "How others can find and contact you\nPosts you're tagged in\n"
                "Ghim lên trang cá nhân\nBỏ ghim khỏi trang cá nhân\n"
                "Mục tiêu gây quỹ\nKho lưu trữ tin\nLưu tin\nChủ đề quảng cáo\n"
                "Nội dung thương hiệu\nBài viết hợp tác\nGắn thẻ sản phẩm\n"
                "Thêm sản phẩm\nBộ sưu tập cửa hàng\nDuyệt cửa hàng\n"
                "Tiếp tục xem\nPhát lên TV\nBáo cáo sự cố\nHộp thư hỗ trợ\n"
                "Trạng thái tài khoản\nTrung tâm tài khoản\n"
                "Cách người khác tìm và liên hệ với bạn\n"
                "Bài viết bạn được gắn thẻ"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nPin to profile\nContinue watching\n"
                    "Ghim lên trang cá nhân\nTiếp tục xem",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nPin to profile only after you finish the album layout"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Pin to profile only after you finish the album layout",
        )

    def test_feed_control_ad_library_watch_party_vn_keys_and_urls(self) -> None:
        feeder = _from_mapping(
            {
                "bình luận": "Sản phẩm rất tốt nhé mọi người",
                "người tạm ẩn bài": "Lan Feed",
                "ngày tạm ẩn bài": "6h",
            }
        )
        self.assertIsNotNone(feeder)
        assert feeder is not None
        self.assertEqual(feeder.author_name, "Lan Feed")
        self.assertEqual(feeder.created_time, "6h")
        advert = _from_mapping(
            {
                "Nội dung": "Mình đã mua rồi bạn ơi",
                "người xem thư viện quảng cáo": "Minh",
                "thời gian xem thư viện quảng cáo": "hôm nay",
                "ad_library_comment_id": "1357",
            }
        )
        self.assertIsNotNone(advert)
        assert advert is not None
        self.assertEqual(advert.author_name, "Minh")
        self.assertEqual(advert.created_time, "hôm nay")
        self.assertEqual(advert.comment_id, "1357")
        party = _from_mapping(
            {
                "text": "cảm ơn shop nhiều lắm luôn",
                "người tổ chức xem chung": "Bo",
                "watch_party_at": "05:45",
                "watch_party_comment_id": "2468",
            }
        )
        self.assertIsNotNone(party)
        assert party is not None
        self.assertEqual(party.author_name, "Bo")
        self.assertEqual(party.created_time, "05:45")
        self.assertEqual(party.comment_id, "2468")
        activity = _from_mapping(
            {
                "message": "sản phẩm đẹp quá đi ạ",
                "người quản lý hoạt động": "An",
                "ngày quản lý hoạt động": "2025-01-01T03:00:00Z",
                "activity_comment_id": "9753",
            }
        )
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.author_name, "An")
        self.assertEqual(activity.created_time, "2025-01-01T03:00:00Z")
        self.assertEqual(activity.comment_id, "9753")
        self.assertEqual(person_name({"feed_snoozer": "Lan"}), "Lan")
        self.assertEqual(person_name({"ad_library_viewer": "Bo"}), "Bo")
        self.assertEqual(person_name({"watch_party_host": "Minh"}), "Minh")
        self.assertEqual(person_name({"activity_manager": "An"}), "An")
        self.assertEqual(person_name({"người tạm ẩn bài": "Ha"}), "Ha")
        self.assertEqual(person_name({"friend_request_viewer": "Vy"}), "Vy")
        self.assertEqual(timestamp_of({"feed_snoozed_at": "2024-01-02"}), "2024-01-02")
        self.assertEqual(timestamp_of({"ad_library_viewed_at": "hôm qua"}), "hôm qua")
        self.assertEqual(timestamp_of({"watch_party_at": "2024-01-03"}), "2024-01-03")
        self.assertEqual(timestamp_of({"activity_managed_at": "2024-01-04"}), "2024-01-04")
        self.assertEqual(first_comment_id({"snooze_comment_id": "22"}), "22")
        self.assertEqual(first_comment_id({"activity_comment_id": "44"}), "44")
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/watch_party/?id=111222"),
            "111222",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/activity/manage/?id=998877"),
            "998877",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/search/history/?id=555666"),
            "555666",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/friends/requests/?id=333444"),
            "333444",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/off_facebook_activity/?id=777888"),
            "777888",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/ad_library/?id=121212"),
            "121212",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/stories/settings/?id=343434"),
            "343434",
        )
        self.assertEqual(
            post_id_from_url("https://www.facebook.com/pages/promote/?id=565656"),
            "565656",
        )
        article = comments_from_articles(
            [
                "Lan\nhello\nBirthdays\nUnrestrict\nSnooze for 30 days\n"
                "See fewer posts like this\nHide all from\nUnfollow page\n"
                "Message page\nCreate offer\nBoost this post\nPromote now\n"
                "See more about this Page\nAd library\nInfo and ads\n"
                "Friends of friends\nExcept friends\nCustom privacy\n"
                "Sinh nhật\nBỏ hạn chế\nTạm ẩn trong 30 ngày\n"
                "Xem ít bài tương tự hơn\nẨn tất cả từ\nBỏ theo dõi trang\n"
                "Nhắn tin trang\nTạo ưu đãi\nQuảng cáo bài viết này\n"
                "Quảng cáo ngay\nXem thêm về Trang này\nThư viện quảng cáo\n"
                "Thông tin và quảng cáo\nBạn của bạn bè\nNgoại trừ bạn bè\n"
                "Quyền riêng tư tùy chỉnh"
            ],
            5,
        )
        self.assertEqual(len(article), 1)
        self.assertEqual(article[0].author_name, "Lan")
        self.assertEqual(article[0].text, "hello")
        healed, codes = heal_comments(
            [
                PublicComment(
                    text="hello\nAd library\nSnooze for 30 days\n"
                    "Thư viện quảng cáo\nTạm ẩn trong 30 ngày",
                    author_name="Lan",
                )
            ]
        )
        self.assertEqual(healed[0].text, "hello")
        self.assertIn("strip_chrome_lines", codes)
        kept = comments_from_articles(
            ["Lan\nAd library only after you finish researching the brand"],
            5,
        )
        self.assertEqual(
            kept[0].text,
            "Ad library only after you finish researching the brand",
        )


if __name__ == "__main__":
    unittest.main()
