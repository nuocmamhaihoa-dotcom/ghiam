"""Rank 6 — official Graph API for public Page posts (Page Public Content Access)."""

from __future__ import annotations

import os
import re

import httpx

from fb_poller.engines.guard import (
    first_comment_id,
    invalid_limit,
    nested_text,
    person_name,
    public_error,
    redact_text,
    scalar_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

_VERSION = re.compile(r"^v\d+\.\d+$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_]{1,128}$")

RANK = 6
NAME = "graph-ppca"


def graph_version() -> str:
    return os.environ.get("GRAPH_VERSION", "v21.0").strip() or "v21.0"


def access_token() -> str:
    return os.environ.get("GRAPH_ACCESS_TOKEN", "").strip()


def accepted_object_id(value: object) -> str | None:
    """Graph path segment. Only a plain id is inserted into the comments URL."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if _OBJECT_ID.match(text):
        return text
    return None


def comments_from_graph_payload(data: object, limit: int) -> tuple[list[PublicComment], str | None]:
    """Read a Graph comments payload. A bare list is accepted. Comments win over an error object."""
    if limit < 1:
        return [], "invalid_limit"
    if isinstance(data, list):
        rows: list[object] = data
    elif isinstance(data, dict):
        rows = _graph_rows(data)
        if not rows and "error" in data:
            err = data.get("error")
            message = err.get("message") if isinstance(err, dict) else err
            return [], redact_text(str(message), 300)
    else:
        return [], "unexpected_graph_payload"
    comments: list[PublicComment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = nested_text(row.get("message"))
        if not text:
            continue
        comments.append(
            PublicComment(
                text=text,
                author_name=person_name(row.get("from")),
                comment_id=first_comment_id(row),
                created_time=scalar_text(row.get("created_time")),
            )
        )
        if len(comments) >= limit:
            break
    if comments:
        return comments, None
    return [], "empty_comment_edge"


def _graph_rows(data: dict[str, object]) -> list[object]:
    value = data.get("data")
    if isinstance(value, list) and value:
        return list(value)
    message = data.get("message")
    if isinstance(message, dict) or scalar_text(message):
        return [data]
    return []


def post_id_from_url(url: str) -> str | None:
    raw = url.strip()
    if raw.isdigit() or ("_" in raw and raw.replace("_", "").isdigit()):
        return raw
    match = re.search(r"(?:posts|permalink|videos|reel)/(\d+)", raw)
    if match:
        return match.group(1)
    query = re.search(r"(?:story_fbid|fbid)=(\d+)", raw)
    if query:
        return query.group(1)
    return None


async def scan(url: str, *, limit: int = 50, timeout: float = 30.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    token = access_token()
    if not token:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_GRAPH_ACCESS_TOKEN",
            note="Needs a Page or system-user token with Page Public Content Access.",
        )
    version = graph_version()
    if not _VERSION.match(version):
        return EngineResult(NAME, RANK, False, error="invalid_GRAPH_VERSION")
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            object_id = accepted_object_id(await _resolve_id(client, version, token, url))
            if not object_id:
                return EngineResult(NAME, RANK, False, error="unresolved_post_id")
            resp = await client.get(
                f"https://graph.facebook.com/{version}/{object_id}/comments",
                params={
                    "fields": "message,from,created_time",
                    "limit": str(min(limit, 100)),
                    "access_token": token,
                },
            )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    try:
        data = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments, error = comments_from_graph_payload(data, limit)
    if resp.status_code >= 400 and not comments:
        if error in {None, "empty_comment_edge", "unexpected_graph_payload"}:
            error = redact_text(f"http_{resp.status_code}: {resp.text}", 220)
        return EngineResult(NAME, RANK, False, error=error)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else error,
        note="PPCA may omit commenter ids unless the token can moderate the Page.",
    )


async def _resolve_id(client: httpx.AsyncClient, version: str, token: str, url: str) -> str | None:
    direct = post_id_from_url(url)
    resp = await client.get(
        f"https://graph.facebook.com/{version}/",
        params={"id": url.strip(), "access_token": token},
    )
    if resp.status_code < 400:
        try:
            body = resp.json()
        except Exception:
            body = None
        og = body.get("og_object") if isinstance(body, dict) else None
        if isinstance(og, dict):
            found = accepted_object_id(og.get("id"))
            if found:
                return found
        if isinstance(body, dict) and "error" not in body:
            found = accepted_object_id(body.get("id"))
            if found:
                return found
    return accepted_object_id(direct)
