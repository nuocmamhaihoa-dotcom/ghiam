from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page, Response


@dataclass
class ExtractedComment:
    comment_id: str
    text: str | None
    author_name: str | None = None
    author_id: str | None = None
    created_time: datetime | None = None
    parent_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "text": self.text,
            "author_name": self.author_name,
            "author_id": self.author_id,
            "created_time": self.created_time,
            "parent_id": self.parent_id,
            "raw": self.raw,
        }


@dataclass
class ExtractResult:
    ok: bool
    comments: list[ExtractedComment] = field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    guest_visible: bool | None = None
    blocked: bool = False


_COMMENT_ID_RE = re.compile(r"comment_id=(\d+)")
_PFBID_RE = re.compile(r"pfbid[0-9A-Za-z]+")


def _stable_id(*parts: str) -> str:
    base = "|".join(p for p in parts if p)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:24]


def _parse_graphql_comments(payload: Any, out: list[ExtractedComment], limit: int) -> None:
    """Best-effort walk of GraphQL JSON for comment-like nodes."""
    if len(out) >= limit:
        return

    if isinstance(payload, dict):
        # Common shapes: {id, body:{text}, author:{name,id}, created_time}
        cid = payload.get("id") or payload.get("legacy_fbid") or payload.get("comment_id")
        body = payload.get("body")
        text = None
        if isinstance(body, dict):
            text = body.get("text")
        text = text or payload.get("text") or payload.get("message")
        author = payload.get("author") or payload.get("user") or {}
        author_name = None
        author_id = None
        if isinstance(author, dict):
            author_name = author.get("name") or author.get("short_name")
            author_id = str(author.get("id")) if author.get("id") else None

        created = payload.get("created_time") or payload.get("created_at")
        created_time = None
        if isinstance(created, (int, float)):
            # seconds or ms
            ts = float(created)
            if ts > 1e12:
                ts /= 1000.0
            created_time = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(created, str) and created.isdigit():
            created_time = datetime.fromtimestamp(int(created), tz=timezone.utc)

        looks_comment = bool(text) and (
            "comment" in json.dumps({k: type(v).__name__ for k, v in payload.items()}).lower()
            or payload.get("__typename") in {"Comment", "FeedbackComment", "XFBComment"}
            or ("author" in payload and "body" in payload)
        )
        if looks_comment and text:
            comment_id = str(cid) if cid else _stable_id(author_name or "", text[:80])
            if not any(c.comment_id == comment_id for c in out):
                out.append(
                    ExtractedComment(
                        comment_id=comment_id,
                        text=text.strip() if isinstance(text, str) else str(text),
                        author_name=author_name,
                        author_id=author_id,
                        created_time=created_time,
                        raw={"source": "graphql"},
                    )
                )

        for v in payload.values():
            _parse_graphql_comments(v, out, limit)
    elif isinstance(payload, list):
        for item in payload:
            _parse_graphql_comments(item, out, limit)


class NetworkCommentSniffer:
    def __init__(self, limit: int = 50):
        self.limit = limit
        self.comments: list[ExtractedComment] = []
        self.saw_login_wall = False

    async def on_response(self, response: Response) -> None:
        try:
            url = response.url
            if "graphql" not in url and "comment" not in url.lower():
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            text = await response.text()
            if not text:
                return
            # Facebook sometimes returns JSON lines
            chunks = [text]
            if "\n" in text[:200]:
                chunks = [ln for ln in text.splitlines() if ln.strip().startswith("{")]
            for chunk in chunks:
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                _parse_graphql_comments(data, self.comments, self.limit)
        except Exception:
            return


async def dismiss_overlays(page: Page) -> None:
    selectors = [
        '[aria-label="Close"]',
        '[aria-label="Đóng"]',
        'div[role="button"][aria-label="Close"]',
        'div[role="dialog"] [aria-label="Close"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=800)
        except Exception:
            continue

    # Cookie / consent buttons (best effort)
    for label in ("Allow all cookies", "Accept all", "Chấp nhận tất cả", "Decline optional cookies"):
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if await btn.count():
                await btn.first.click(timeout=800)
        except Exception:
            continue


async def try_switch_newest(page: Page) -> bool:
    """Attempt to switch comment sort to Newest / All comments."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"Most relevant|Phù hợp nhất|Top comments", re.I)),
        page.locator('[aria-label*="Most relevant"]'),
        page.locator('[aria-label*="Phù hợp nhất"]'),
    ]
    for loc in candidates:
        try:
            if await loc.count() == 0:
                continue
            await loc.first.click(timeout=1500)
            for name in (
                r"^Newest$",
                r"Mới nhất",
                r"All comments",
                r"Tất cả bình luận",
                r"Recent",
            ):
                opt = page.get_by_role("menuitem", name=re.compile(name, re.I))
                if await opt.count() == 0:
                    opt = page.get_by_text(re.compile(name, re.I))
                if await opt.count():
                    await opt.first.click(timeout=1500)
                    return True
        except Exception:
            continue
    return False


_EXTRACT_DOM_JS = """
(maxItems) => {
  const results = [];
  const seen = new Set();

  const push = (obj) => {
    if (!obj.text || results.length >= maxItems) return;
    const key = obj.comment_id || (obj.author_name + '|' + obj.text.slice(0, 80));
    if (seen.has(key)) return;
    seen.add(key);
    results.push(obj);
  };

  // Strategy A: anchors with comment_id=
  for (const a of document.querySelectorAll('a[href*="comment_id="]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/comment_id=(\\d+)/);
    if (!m) continue;
    let node = a.closest('[role="article"]') || a.parentElement;
    let text = '';
    let author = '';
    if (node) {
      const t = node.innerText || '';
      const lines = t.split('\\n').map(s => s.trim()).filter(Boolean);
      if (lines.length) {
        author = lines[0].slice(0, 200);
        text = lines.slice(1).join(' ').slice(0, 2000);
      }
    }
    push({
      comment_id: m[1],
      text: text || null,
      author_name: author || null,
      source: 'dom_comment_id'
    });
  }

  // Strategy B: role=article blocks that look like comments
  if (results.length < maxItems) {
    for (const art of document.querySelectorAll('[role="article"]')) {
      const t = (art.innerText || '').trim();
      if (!t || t.length < 2) continue;
      // Skip main post body heuristically (very long first article)
      const lines = t.split('\\n').map(s => s.trim()).filter(Boolean);
      if (lines.length < 2) continue;
      const author = lines[0].slice(0, 200);
      const text = lines.slice(1).join(' ').slice(0, 2000);
      if (text.length < 1) continue;
      const aid = art.getAttribute('aria-labelledby') || '';
      const comment_id = 'dom_' + btoa(unescape(encodeURIComponent(author + '|' + text.slice(0, 60)))).slice(0, 24);
      push({ comment_id, text, author_name: author, source: 'dom_article', aria: aid });
    }
  }

  const bodyText = document.body ? document.body.innerText : '';
  const loginWall = /log in|đăng nhập|create new account|tạo tài khoản mới/i.test(bodyText.slice(0, 2000));
  return { comments: results, loginWall, title: document.title || '' };
}
"""


async def extract_comments(page: Page, *, max_comments: int, sniffer: NetworkCommentSniffer) -> ExtractResult:
    await dismiss_overlays(page)
    await try_switch_newest(page)
    # small settle for comment list paint
    try:
        await page.wait_for_timeout(800)
    except Exception:
        pass

    try:
        dom = await page.evaluate(_EXTRACT_DOM_JS, max_comments)
    except Exception as exc:
        return ExtractResult(ok=False, error_code="parse", error_detail=str(exc))

    comments: list[ExtractedComment] = []
    for item in dom.get("comments") or []:
        comments.append(
            ExtractedComment(
                comment_id=str(item.get("comment_id")),
                text=item.get("text"),
                author_name=item.get("author_name"),
                raw={"source": item.get("source")},
            )
        )

    # Merge network sniff (prefer graphql ids)
    for c in sniffer.comments:
        if not any(x.comment_id == c.comment_id for x in comments):
            comments.append(c)
        if len(comments) >= max_comments:
            break

    comments = comments[:max_comments]
    login_wall = bool(dom.get("loginWall")) or sniffer.saw_login_wall

    if not comments and login_wall:
        return ExtractResult(
            ok=False,
            error_code="blocked",
            error_detail="login_wall_or_empty",
            guest_visible=False,
            blocked=True,
        )

    if not comments:
        # Could be true zero comments
        return ExtractResult(ok=True, comments=[], guest_visible=True, error_code=None)

    return ExtractResult(ok=True, comments=comments, guest_visible=True)
