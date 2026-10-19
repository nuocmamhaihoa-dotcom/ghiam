"""Rank 4 — run one Apify actor and read its dataset. Isolated from other engines."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from fb_poller.engines.guard import (
    connection_rows,
    first_comment_id,
    first_person_name,
    invalid_limit,
    nested_text,
    public_error,
    timestamp_of,
    redact_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

_ACTOR_ID = re.compile(r"^[A-Za-z0-9_.~\-]{1,200}$")

RANK = 4
NAME = "apify"


def _token() -> str:
    return os.environ.get("APIFY_TOKEN", "").strip()


def _actor() -> str | None:
    raw = os.environ.get("APIFY_ACTOR_ID", "apify~facebook-comments-scraper").strip()
    raw = raw.replace("/", "~")
    if not _ACTOR_ID.match(raw):
        return None
    return raw


async def scan(url: str, *, limit: int = 50, timeout: float = 180.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    token = _token()
    if not token:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_APIFY_TOKEN",
            note="Create a run at Apify and set APIFY_TOKEN plus APIFY_ACTOR_ID.",
        )
    actor = _actor()
    if actor is None:
        return EngineResult(NAME, RANK, False, error="invalid_APIFY_ACTOR_ID")
    payload = {
        "startUrls": [{"url": url}],
        "resultsLimit": limit,
        "maxComments": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(
                f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                params={"token": token},
                json=payload,
            )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    if resp.status_code >= 400:
        return EngineResult(NAME, RANK, False, error=redact_text(f"http_{resp.status_code}: {resp.text}", 220))
    try:
        payload = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments = _from_items(payload, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "empty_actor_dataset",
        note=f"Actor {actor}. Input shape varies; override the actor id if the default rejects it.",
    )


def _from_items(payload: Any, limit: int) -> list[PublicComment]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        nested_rows: list[Any] | None = None
        for key in (
            "items",
            "data",
            "comments",
            "comment",
            "comment_list",
            "commentList",
            "comment_thread",
            "results",
            "result",
            "feedback",
        ):
            value = payload.get(key)
            linked = connection_rows(value)
            if not linked and isinstance(value, dict):
                for inner_key in (
                    "comments",
                    "comment_list",
                    "commentList",
                    "comment_thread",
                    "items",
                    "data",
                    "results",
                    "feedback",
                ):
                    child = value.get(inner_key)
                    linked = connection_rows(child)
                    if not linked and isinstance(child, dict):
                        for deeper in (
                            "comments",
                            "comment_list",
                            "commentList",
                            "comment_thread",
                            "items",
                            "data",
                            "nodes",
                        ):
                            linked = connection_rows(child.get(deeper))
                            if linked:
                                break
                    if linked:
                        break
            if linked:
                nested_rows = linked
                break
        if _one(payload) or nested_rows is None:
            rows = [payload]
        else:
            rows = nested_rows
    else:
        rows = []
    out: list[PublicComment] = []
    seen: set[str] = set()
    _collect(rows, out, seen, limit, 0)
    return out


def _collect(
    rows: list[Any],
    out: list[PublicComment],
    seen: set[str],
    limit: int,
    depth: int,
) -> None:
    """Keep a comment and replies nested under it. An empty list does not hide a later list."""
    if depth > 6 or len(out) >= limit:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        unwrapped = connection_rows([row])
        row = unwrapped[0] if unwrapped else row
        if not isinstance(row, dict):
            continue
        item = _one(row)
        if item:
            key = item.comment_id or f"{item.author_name}|{item.text}"
            if key not in seen:
                seen.add(key)
                out.append(item)
        if len(out) >= limit:
            return
        for name in ("comments", "replies"):
            nested = _connection_rows(row.get(name))
            if nested:
                _collect(nested, out, seen, limit, depth + 1)
            if len(out) >= limit:
                return


def _connection_rows(value: object) -> list[Any]:
    """An empty list does not count. ``{data: [...]}`` and ``{edges: [{node}]}`` do."""
    rows = connection_rows(value)
    if rows:
        return rows
    if isinstance(value, dict) and (
        nested_text(value.get("text")) or nested_text(value.get("message")) or nested_text(value.get("body"))
    ):
        return [value]
    return []


def _row_time(row: dict[str, Any] | None) -> str | None:
    return timestamp_of(row)


def _one(row: Any) -> PublicComment | None:
    if not isinstance(row, dict):
        return None
    text = None
    inner = None
    for key in (
        "text",
        "message",
        "comment",
        "commentText",
        "comment_text",
        "commentBody",
        "body",
        "bodyText",
        "preferred_body",
        "content",
        "message_text",
        "plain_body",
        "plaintext",
        "comment_message",
        "body_html",
    ):
        value = row.get(key)
        text = nested_text(value)
        if text:
            if isinstance(value, dict):
                inner = value
            break
    if not text:
        return None
    author_keys = (
        "author",
        "profileName",
        "userName",
        "commenter_name",
        "commenter",
        "name",
        "from",
        "user",
        "profile",
        "owner",
        "owner_name",
        "display_name",
        "full_name",
        "screen_name",
        "short_name",
        "author_title",
        "user_full_name",
        "profile_url",
        "author_nickname",
        "commenter_full_name",
        "i18n_name",
        "alternate_name",
        "localized_name",
        "user_handle",
        "commenter_handle",
        "profile_handle",
        "author_sort_name",
        "page_name",
        "actor",
        "actor_name",
        "from_name",
    )
    return PublicComment(
        text=text,
        author_name=first_person_name(row, author_keys)
        or (first_person_name(inner, author_keys) if inner else None),
        comment_id=first_comment_id(row) or (first_comment_id(inner) if inner else None),
        created_time=_row_time(row) or (_row_time(inner) if inner else None),
    )
