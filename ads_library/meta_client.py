"""Khách hàng Meta Ad Library API. Chỉ gọi graph.facebook.com."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

from ads_library.page_ref import PageRef

GRAPH_HOST = "graph.facebook.com"
GRAPH_ROOT = f"https://{GRAPH_HOST}/v21.0"
_FIELDS = ",".join(
    [
        "id",
        "page_id",
        "page_name",
        "ad_creation_time",
        "ad_delivery_start_time",
        "ad_delivery_stop_time",
        "ad_creative_bodies",
        "ad_creative_link_titles",
        "ad_creative_link_captions",
        "ad_creative_link_descriptions",
        "ad_snapshot_url",
        "publisher_platforms",
        "languages",
        "bylines",
    ]
)
_TOKEN_IN_TEXT = re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE)
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_STATUSES = {"ACTIVE", "INACTIVE", "ALL"}

Fetch = Callable[[str, float], tuple[int, str]]


class MetaApiError(Exception):
    """Meta từ chối hoặc trả về dữ liệu không đọc được."""


@dataclass(frozen=True)
class PageInfo:
    id: str
    name: str
    link: str


@dataclass(frozen=True)
class SearchResult:
    page: PageInfo
    ads: list[dict[str, Any]]


def redact(text: str, token: str = "") -> str:
    cleaned = _TOKEN_IN_TEXT.sub(r"\1[token]", text)
    if token:
        cleaned = cleaned.replace(token, "[token]")
    return cleaned


def _graph_url(path: str, params: dict[str, str]) -> str:
    return f"{GRAPH_ROOT}/{path.lstrip('/')}?{urlencode(params)}"


def _allowed_graph_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == GRAPH_HOST and parsed.path.startswith("/v21.0/")


def _read_json(url: str, fetch: Fetch, timeout: float, token: str) -> dict[str, Any]:
    if not _allowed_graph_url(url):
        raise MetaApiError("Từ chối gọi máy chủ khác graph.facebook.com.")
    status, body = fetch(url, timeout)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise MetaApiError(f"Meta trả về dữ liệu không phải JSON (HTTP {status}).") from exc
    if not isinstance(payload, dict):
        raise MetaApiError("Meta trả về dữ liệu không đúng dạng.")
    error = payload.get("error")
    if status >= 400 or isinstance(error, dict):
        message = "Meta từ chối yêu cầu."
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        raise MetaApiError(redact(message, token))
    return payload


def _page_from_payload(payload: dict[str, Any]) -> PageInfo:
    page_id = str(payload.get("id") or "")
    if not page_id.isdigit():
        raise MetaApiError("Meta không trả về id Fanpage.")
    name = payload.get("name")
    link = payload.get("link")
    return PageInfo(
        id=page_id,
        name=name if isinstance(name, str) and name else page_id,
        link=link if isinstance(link, str) else f"https://www.facebook.com/{page_id}",
    )


def resolve_page(ref: PageRef, token: str, fetch: Fetch, timeout: float = 20) -> PageInfo:
    key = ref.value if ref.kind == "id" else ref.value
    payload = _read_json(
        _graph_url(key, {"fields": "id,name,link", "access_token": token}),
        fetch,
        timeout,
        token,
    )
    return _page_from_payload(payload)


def search_ads(
    page: PageInfo,
    *,
    country: str,
    status: str,
    limit: int,
    token: str,
    fetch: Fetch,
    timeout: float = 20,
) -> list[dict[str, Any]]:
    validate_filters(country, status, limit)
    ads: list[dict[str, Any]] = []
    url: str | None = _graph_url(
        "ads_archive",
        {
            "access_token": token,
            "search_page_ids": page.id,
            "ad_reached_countries": json.dumps([country]),
            "ad_active_status": status,
            "ad_type": "ALL",
            "fields": _FIELDS,
            "limit": str(min(25, limit)),
        },
    )
    while url and len(ads) < limit:
        payload = _read_json(url, fetch, timeout, token)
        batch = payload.get("data")
        if isinstance(batch, list):
            ads.extend(item for item in batch if isinstance(item, dict))
        paging = payload.get("paging")
        next_url = paging.get("next") if isinstance(paging, dict) else None
        url = next_url if isinstance(next_url, str) and _allowed_graph_url(next_url) else None
    return ads[:limit]


def validate_filters(country: str, status: str, limit: int) -> None:
    if not _COUNTRY.fullmatch(country):
        raise MetaApiError("Mã quốc gia phải là hai chữ cái, ví dụ VN.")
    if status not in _STATUSES:
        raise MetaApiError("Trạng thái quảng cáo không hợp lệ.")
    if limit < 1 or limit > 100:
        raise MetaApiError("Số quảng cáo phải từ 1 đến 100.")


def lookup(ref: PageRef, *, country: str, status: str, limit: int, token: str, fetch: Fetch) -> SearchResult:
    if not token.strip():
        raise MetaApiError("Chưa cấu hình META_ACCESS_TOKEN.")
    validate_filters(country, status, limit)
    page = resolve_page(ref, token, fetch)
    ads = search_ads(page, country=country, status=status, limit=limit, token=token, fetch=fetch)
    return SearchResult(page=page, ads=ads)
