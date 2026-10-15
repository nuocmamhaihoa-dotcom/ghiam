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
_MATCHES = {"any": "KEYWORD_UNORDERED", "exact": "KEYWORD_EXACT_PHRASE"}
_KEYWORD_LIMIT = 100

Fetch = Callable[[str, float], tuple[int, str]]


class MetaApiError(Exception):
    """Meta từ chối hoặc trả về dữ liệu không đọc được."""


class InputError(MetaApiError):
    """Người dùng gửi bộ lọc không hợp lệ. Chưa gọi Meta."""


@dataclass(frozen=True)
class PageInfo:
    id: str
    name: str
    link: str


@dataclass(frozen=True)
class SearchResult:
    page: PageInfo | None
    keyword: str
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


def normalize_keyword(raw: str) -> str:
    text = " ".join(raw.split())
    if any(ord(char) < 32 for char in text):
        raise InputError("Từ khóa chứa ký tự không hợp lệ.")
    if len(text) > _KEYWORD_LIMIT:
        raise InputError("Từ khóa tối đa 100 ký tự.")
    return text


def search_ads(
    *,
    page_id: str | None,
    keyword: str,
    match: str,
    country: str,
    status: str,
    limit: int,
    token: str,
    fetch: Fetch,
    timeout: float = 20,
) -> list[dict[str, Any]]:
    validate_filters(country, status, limit, keyword=keyword, match=match, has_page=bool(page_id))
    params = {
        "access_token": token,
        "ad_reached_countries": json.dumps([country]),
        "ad_active_status": status,
        "ad_type": "ALL",
        "fields": _FIELDS,
        "limit": str(min(25, limit)),
    }
    if page_id:
        params["search_page_ids"] = page_id
    if keyword:
        params["search_terms"] = keyword
        params["search_type"] = _MATCHES[match]
    ads: list[dict[str, Any]] = []
    url: str | None = _graph_url("ads_archive", params)
    while url and len(ads) < limit:
        payload = _read_json(url, fetch, timeout, token)
        batch = payload.get("data")
        if isinstance(batch, list):
            ads.extend(item for item in batch if isinstance(item, dict))
        paging = payload.get("paging")
        next_url = paging.get("next") if isinstance(paging, dict) else None
        url = next_url if isinstance(next_url, str) and _allowed_graph_url(next_url) else None
    return ads[:limit]


def validate_filters(
    country: str,
    status: str,
    limit: int,
    *,
    keyword: str = "",
    match: str = "any",
    has_page: bool = True,
) -> None:
    if not _COUNTRY.fullmatch(country):
        raise InputError("Mã quốc gia phải là hai chữ cái, ví dụ VN.")
    if status not in _STATUSES:
        raise InputError("Trạng thái quảng cáo không hợp lệ.")
    if limit < 1 or limit > 100:
        raise InputError("Số quảng cáo phải từ 1 đến 100.")
    if keyword and match not in _MATCHES:
        raise InputError("Cách khớp từ khóa không hợp lệ.")
    if not has_page and not keyword:
        raise InputError("Nhập từ khóa hoặc link Fanpage.")


def lookup(
    ref: PageRef | None = None,
    *,
    keyword: str = "",
    match: str = "any",
    country: str,
    status: str,
    limit: int,
    token: str,
    fetch: Fetch,
) -> SearchResult:
    text = normalize_keyword(keyword)
    validate_filters(country, status, limit, keyword=text, match=match, has_page=ref is not None)
    if not token.strip():
        raise MetaApiError("Chưa cấu hình META_ACCESS_TOKEN. Máy chủ chưa được Meta cấp quyền đọc thư viện quảng cáo.")
    page = resolve_page(ref, token, fetch) if ref is not None else None
    ads = search_ads(
        page_id=page.id if page else None,
        keyword=text,
        match=match,
        country=country,
        status=status,
        limit=limit,
        token=token,
        fetch=fetch,
    )
    return SearchResult(page=page, keyword=text, ads=ads)
