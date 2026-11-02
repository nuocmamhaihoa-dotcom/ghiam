"""Pull and push desktop macros to the VPS store. Record/play stay on the PC."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.request import Request

from pc_macro.store import MacroStore

Fetcher = Callable[[Request, float], tuple[int, bytes]]


class HubError(Exception):
    """The VPS address is missing, or the store rejected the request."""


def hub_url() -> str:
    return os.environ.get("PC_MACRO_HUB", "").strip().rstrip("/")


def hub_token() -> str:
    return os.environ.get("PC_MACRO_TOKEN", "").strip()


def configured() -> bool:
    return bool(hub_url())


def describe() -> str:
    url = hub_url()
    if not url:
        return "Chưa đặt PC_MACRO_HUB — ví dụ http://103.200.20.245:8088"
    token = "có mã" if hub_token() else "chưa có mã (PC_MACRO_TOKEN)"
    return f"{url} ({token})"


def get_run(*, fetch: Fetcher | None = None) -> dict[str, Any]:
    body = _call("GET", "/v1/pc-macro/run", fetch=fetch)
    return body if isinstance(body, dict) else {}


def set_run(payload: dict[str, Any], *, fetch: Fetcher | None = None) -> dict[str, Any]:
    body = _call("POST", "/v1/pc-macro/run", payload, fetch=fetch)
    return body if isinstance(body, dict) else {}


def list_remote(*, fetch: Fetcher | None = None) -> list[dict[str, Any]]:
    body = _call("GET", "/v1/macros", fetch=fetch)
    items = body.get("items") if isinstance(body, dict) else None
    return list(items or [])


def pull(store: MacroStore, slug: str | None = None, *, fetch: Fetcher | None = None) -> dict[str, Any]:
    slugs = [slug] if slug else [str(row.get("slug") or "") for row in list_remote(fetch=fetch)]
    saved: list[dict[str, Any]] = []
    for name in slugs:
        if not name:
            continue
        item = _call("GET", f"/v1/macros/{name}", fetch=fetch)
        if not isinstance(item, dict):
            continue
        saved.append(
            store.save(
                str(item.get("name") or name),
                item.get("events") or [],
                slug=str(item.get("slug") or name),
                numbers=item.get("numbers"),
                steps=item.get("steps") or [],
            )
        )
    return {"count": len(saved), "items": saved}


def push(store: MacroStore, slug: str | None = None, *, fetch: Fetcher | None = None) -> dict[str, Any]:
    if slug:
        item = store.get(slug)
        items = [item] if item else []
    else:
        items = store.list()
    if not items:
        raise HubError("Không có kịch bản local để đưa lên VPS.")
    uploaded: list[dict[str, Any]] = []
    for item in items:
        body = _call(
            "POST",
            "/v1/macros",
            {
                "name": item.get("name") or "",
                "slug": item.get("slug"),
                "steps": item.get("steps") or [],
                "events": item.get("events") or [],
                "numbers": item.get("numbers"),
            },
            fetch=fetch,
        )
        if isinstance(body, dict):
            uploaded.append(body)
    return {"count": len(uploaded), "items": uploaded}


def _call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    fetch: Fetcher | None = None,
) -> Any:
    base = hub_url()
    if not base:
        raise HubError("Chưa đặt PC_MACRO_HUB. Trên Windows: $env:PC_MACRO_HUB='http://IP:8088'")
    data = None
    headers = {"Accept": "application/json"}
    token = hub_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base}{path}", data=data, headers=headers, method=method)
    timeout = 30.0
    try:
        if fetch is not None:
            status, raw = fetch(request, timeout)
            if status >= 400:
                raise HubError(_http_message(status, raw))
        else:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        raise HubError(_http_message(exc.code, raw)) from exc
    except urllib.error.URLError as exc:
        raise HubError(f"Không tới được VPS: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HubError("VPS trả về dữ liệu không đọc được.") from exc


def _http_message(status: int, raw: bytes) -> str:
    detail = ""
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            detail = str(parsed.get("detail") or "")
    if status == 401:
        return "VPS từ chối mã. Kiểm tra PC_MACRO_TOKEN."
    if status == 404:
        return detail or "Không thấy kịch bản trên VPS."
    return detail or f"VPS trả lỗi {status}."
