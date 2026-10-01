from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from commentscope_agent.commentnorm import clean_text, parse_likes, parse_time
from commentscope_agent.facebook import parse_html, parse_json_text
from commentscope_agent.reader import ReadOptions, read_public_facebook

NOW = datetime(2026, 9, 27, 8, 0, tzinfo=UTC)

PAGE = """
<html><head><title>Bài viết</title></head><body>
<div id="ufi_9">
  <div>
    <h3><a href="/profile.php?id=100001">Nguyễn Văn A</a></h3>
    <div>Bình luận công khai một</div>
    <div><abbr>2 giờ</abbr> · <a href="/ufi/reaction/?id=1">1,2K</a>
      · <a href="/comment/replies/?comment_id=c1">3 phản hồi</a></div>
  </div>
  <div>
    <h3><a href="/profile.php?id=100002&eav=tracking">Trần Thị B</a></h3>
    <div>Bình luận\u200b công khai hai</div>
    <div><abbr>Hôm qua</abbr></div>
  </div>
  <div><a href="/story.php?story_fbid=1&id=2&p=1">Xem thêm bình luận</a></div>
  <div><a href="/login.php">Đăng nhập</a></div>
</div>
<p>Viết bình luận</p>
</body></html>
"""

PAGE_TWO = """
<html><body>
<div>
  <h3><a href="/profile.php?id=100003">Lê Văn C</a></h3>
  <div>Bình luận trang hai</div>
  <div><abbr>5 phút</abbr> · <a href="/ufi/reaction/?id=3">3 N</a></div>
</div>
<p>Viết bình luận</p>
</body></html>
"""

LOGIN = """
<html><body>
<form method="post" action="/login/device-based/regular/login/"><input name="email"></form>
<p>Bạn phải đăng nhập để xem bình luận này</p>
</body></html>
"""

UNAVAILABLE = "<html><body><p>Nội dung này hiện không khả dụng</p></body></html>"


def test_reads_public_comments_and_ignores_the_login_link() -> None:
    parsed = parse_html(PAGE, "https://mbasic.facebook.com/story.php?story_fbid=1&id=2", now=NOW)
    assert parsed.kind == "comments"
    assert parsed.next_url is not None
    assert "login" not in urlsplit(parsed.next_url).path
    assert [item.author for item in parsed.comments] == ["Nguyễn Văn A", "Trần Thị B"]
    first, second = parsed.comments
    assert first.text == "Bình luận công khai một"
    assert first.external_id == "c1"
    assert first.author_id == "100001"
    assert first.likes == 1200
    assert first.likes_raw == "1,2K"
    assert first.reply_count == 3
    assert first.time == datetime(2026, 9, 27, 6, 0, tzinfo=UTC)
    assert second.text == "Bình luận công khai hai"
    assert second.author_url == "https://www.facebook.com/profile.php?id=100002"
    assert second.time == datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    assert second.likes is None


def test_login_wall_and_missing_post_are_not_treated_as_comments() -> None:
    login = parse_html(LOGIN, "https://mbasic.facebook.com/story.php?story_fbid=1&id=2", now=NOW)
    missing = parse_html(UNAVAILABLE, "https://mbasic.facebook.com/story.php?story_fbid=1&id=2", now=NOW)
    assert login.kind == "blocked"
    assert login.comments == ()
    assert missing.kind == "not_available"


def test_json_comment_is_read_and_a_story_is_not() -> None:
    payload = """
    {"data": {"node": {"__typename": "Story", "body": {"text": "Nội dung bài"}, "author": {"name": "Trang"},
      "created_time": 1710000000},
      "comments": {"total_count": 12},
      "edge": {"node": {"__typename": "Comment", "legacy_fbid": "c9", "body": {"text": "Từ JSON"},
        "author": {"id": "55", "name": "Lê C", "url": "https://www.facebook.com/profile.php?id=55"},
        "created_time": 1710000000,
        "feedback": {"reactors": {"count": 4}, "replies": {"count": 1}}}}}}
    """
    comments, reported = parse_json_text(payload, now=NOW)
    assert reported == 12
    assert len(comments) == 1
    assert comments[0].text == "Từ JSON"
    assert comments[0].external_id == "c9"
    assert comments[0].likes == 4
    assert comments[0].reply_count == 1
    assert comments[0].time == datetime.fromtimestamp(1710000000, UTC)


def test_like_and_time_formats() -> None:
    assert parse_likes("1,2K") == 1200
    assert parse_likes("3 N") == 3000
    assert parse_likes("1,5 Tr") == 1_500_000
    assert parse_likes("1.5M") == 1_500_000
    assert parse_likes("1.200") == 1200
    assert parse_likes("1,200") == 1200
    assert clean_text("a\u200bb") == "ab"
    assert parse_time("2h", NOW) == datetime(2026, 9, 27, 6, 0, tzinfo=UTC)


class FakePage:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.url = ""
        self.visited: list[str] = []
        self._html = ""

    def on(self, _event: str, _handler: object) -> None:
        return None

    async def goto(self, url: str, **_kwargs: object) -> None:
        path = urlsplit(url).path
        self.visited.append(path)
        self.url = url
        self._html = self.pages[path]

    async def content(self) -> str:
        return self._html


async def test_reader_follows_more_comments_and_stops_at_a_login_wall() -> None:
    page = FakePage({"/post": PAGE, "/story.php": PAGE_TWO})
    result = await read_public_facebook(
        page, "http://127.0.0.1/post", ReadOptions(max_comments=10, time_budget_sec=30, now=NOW)
    )
    assert result.outcome == "done"
    assert result.complete is True
    assert [item.author for item in result.comments] == ["Nguyễn Văn A", "Trần Thị B", "Lê Văn C"]
    assert result.comments[2].likes == 3000
    assert all("login" not in path for path in page.visited)

    blocked = await read_public_facebook(
        FakePage({"/post": LOGIN}),
        "http://127.0.0.1/post",
        ReadOptions(now=NOW),
    )
    assert blocked.outcome == "blocked"
    assert blocked.comments == ()
