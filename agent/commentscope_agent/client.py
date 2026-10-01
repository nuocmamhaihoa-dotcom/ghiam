"""Gọi API agent trên VPS: ping, thuê / gia hạn / trả proxy."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Self
from urllib.parse import quote

import httpx

from commentscope_agent import __version__

Outcome = Literal["ok", "blocked", "failed", "cancelled"]
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)


class ControlPlaneError(Exception):
    """VPS không trả lời được hoặc trả lỗi."""

    def __init__(self, message: str, *, status: int | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class AgentAuthError(ControlPlaneError):
    """Token agent sai hoặc VPS chưa bật API cho agent."""


class LeaseGoneError(ControlPlaneError):
    """Lượt thuê đã kết thúc hoặc không còn trên VPS."""


@dataclass(frozen=True, slots=True)
class PingInfo:
    agent: str
    version: str
    server_time: datetime
    check_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeasedProxy:
    id: int
    kind: str
    protocol: str
    host: str
    port: int
    username: str | None
    password: str | None = field(repr=False)
    pool: str
    exit_ip: str | None
    country: str | None

    @property
    def label(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"#{self.id} {self.protocol}://{host}:{self.port} (pool {self.pool})"


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    expires_at: datetime
    proxy: LeasedProxy


@dataclass(frozen=True, slots=True)
class NoProxy:
    retry_after_sec: int
    message: str


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    released: bool
    rotation_scheduled: bool
    quarantined_until: datetime | None


@dataclass(frozen=True, slots=True)
class WorkAssignment:
    attempt_id: str
    expires_at: datetime
    post_id: int
    url: str
    platform: str
    max_comments: int
    include_replies: bool
    max_replies_per_comment: int
    time_budget_sec: int
    author_mode: str
    lease: Lease


@dataclass(frozen=True, slots=True)
class NoWork:
    retry_after_sec: int
    message: str


@dataclass(frozen=True, slots=True)
class ProgressResult:
    stored: int
    duplicate: bool
    expires_at: datetime


class ControlPlaneClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._retry_delays = tuple(retry_delays)
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"commentscope-agent/{__version__}"},
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ping(self) -> PingInfo:
        data = await self._call("GET", "/api/agent/ping", idempotent=True)
        return PingInfo(
            agent=_str(data, "agent"),
            version=_str(data, "version"),
            server_time=_time(data, "server_time"),
            check_urls=tuple(_str_list(data, "check_urls")),
        )

    async def lease(
        self,
        *,
        worker_id: str,
        pool: str | None = None,
        kind: str | None = None,
        ttl_sec: int | None = None,
        job_ref: str | None = None,
        exclude_ids: Sequence[int] = (),
    ) -> Lease | NoProxy:
        payload: dict[str, Any] = {"worker_id": worker_id}
        if pool:
            payload["pool"] = pool
        if kind:
            payload["kind"] = kind
        if ttl_sec is not None:
            payload["ttl_sec"] = ttl_sec
        if job_ref:
            payload["job_ref"] = job_ref
        if exclude_ids:
            payload["exclude_ids"] = list(exclude_ids)
        # Thuê không lặp lại an toàn: chỉ thử lại khi request chắc chắn chưa tới được server.
        data = await self._call("POST", "/api/agent/proxies/lease", json=payload, idempotent=False)
        if data.get("lease_id") is None:
            return NoProxy(
                retry_after_sec=_int(data, "retry_after_sec"),
                message=_opt_str(data, "message") or "Chưa có proxy rảnh",
            )
        return Lease(
            lease_id=_str(data, "lease_id"),
            expires_at=_time(data, "expires_at"),
            proxy=_leased_proxy(_obj(data.get("proxy"), "proxy")),
        )

    async def claim_work(self, *, worker_id: str, ttl_sec: int | None = None) -> WorkAssignment | NoWork:
        payload: dict[str, Any] = {"worker_id": worker_id}
        if ttl_sec is not None:
            payload["ttl_sec"] = ttl_sec
        data = await self._call("POST", "/api/agent/work/claim", json=payload, idempotent=False)
        if data.get("attempt_id") is None:
            return NoWork(
                retry_after_sec=_int(data, "retry_after_sec"),
                message=_opt_str(data, "message") or "Không có bài đang chờ",
            )
        return WorkAssignment(
            attempt_id=_str(data, "attempt_id"),
            expires_at=_time(data, "expires_at"),
            post_id=_int(data, "post_id"),
            url=_str(data, "url"),
            platform=_str(data, "platform"),
            max_comments=_int(data, "max_comments"),
            include_replies=_bool(data, "include_replies"),
            max_replies_per_comment=_int(data, "max_replies_per_comment"),
            time_budget_sec=_int(data, "time_budget_sec"),
            author_mode=_str(data, "author_mode"),
            lease=Lease(
                lease_id=_str(data, "lease_id"),
                expires_at=_time(data, "expires_at"),
                proxy=_leased_proxy(_obj(data.get("proxy"), "proxy")),
            ),
        )

    async def report_progress(self, attempt_id: str, seq: int, comments: Sequence[Mapping[str, Any]]) -> ProgressResult:
        data = await self._attempt_call(attempt_id, "progress", {"seq": seq, "comments": list(comments)})
        return ProgressResult(
            stored=_int(data, "stored"), duplicate=_bool(data, "duplicate"), expires_at=_time(data, "expires_at")
        )

    async def complete_attempt(self, attempt_id: str, payload: Mapping[str, Any]) -> str:
        data = await self._attempt_call(attempt_id, "complete", dict(payload))
        return _str(data, "post_status")

    async def renew(self, lease_id: str, ttl_sec: int | None = None) -> datetime:
        payload = {} if ttl_sec is None else {"ttl_sec": ttl_sec}
        data = await self._lease_call(lease_id, "renew", payload)
        return _time(data, "expires_at")

    async def release(
        self, lease_id: str, outcome: Outcome, *, detail: str | None = None, request_rotation: bool = False
    ) -> ReleaseResult:
        payload = {"outcome": outcome, "detail": detail, "request_rotation": request_rotation}
        data = await self._lease_call(lease_id, "release", payload)
        return ReleaseResult(
            released=_bool(data, "released"),
            rotation_scheduled=_bool(data, "rotation_scheduled"),
            quarantined_until=_opt_time(data, "quarantined_until"),
        )

    async def _attempt_call(self, attempt_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/api/agent/attempts/{quote(attempt_id, safe='')}/{action}"
        return await self._call("POST", path, json=payload, idempotent=True)

    async def _lease_call(self, lease_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/api/agent/leases/{quote(lease_id, safe='')}/{action}"
        try:
            return await self._call("POST", path, json=payload, idempotent=True)
        except ControlPlaneError as exc:
            if exc.status in (404, 410):
                raise LeaseGoneError(exc.detail or str(exc), status=exc.status, detail=exc.detail) from exc
            raise

    async def _call(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, idempotent: bool
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            error: ControlPlaneError
            try:
                response = await self._http.request(method, path, json=json)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                error = ControlPlaneError(f"Không kết nối được tới VPS {self.base_url}: {_short(exc)}")
                transient = True
            except httpx.TimeoutException:
                error = ControlPlaneError(f"VPS {self.base_url} không trả lời kịp (quá thời gian chờ)")
                transient = idempotent
            except httpx.TransportError as exc:
                error = ControlPlaneError(f"Lỗi mạng khi gọi VPS {self.base_url}: {_short(exc)}")
                transient = idempotent
            else:
                if response.is_success:
                    return self._json(response)
                error = _http_error(response)
                transient = response.status_code == 502 or (idempotent and response.status_code == 504)
            if not transient or attempt >= len(self._retry_delays):
                raise error
            await asyncio.sleep(self._retry_delays[attempt])
            attempt += 1

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ControlPlaneError(
                f"{self.base_url} không trả dữ liệu của CommentScope (kiểm tra lại COMMENTSCOPE_SERVER_URL)"
            )
        return data


def _http_error(response: httpx.Response) -> ControlPlaneError:
    status = response.status_code
    detail = _detail(response)
    if status == 401:
        return AgentAuthError(
            f"VPS từ chối token agent ({detail or 'HTTP 401'}). "
            "Kiểm tra COMMENTSCOPE_AGENT_TOKEN có nằm trong AGENT_TOKENS trên VPS không",
            status=status,
            detail=detail,
        )
    if status == 503 and detail and "AGENT_TOKENS" in detail:
        return AgentAuthError(f"VPS chưa bật API cho agent: {detail}", status=status, detail=detail)
    if detail is None:
        if status == 404:
            return ControlPlaneError(
                "Không tìm thấy API agent (kiểm tra lại COMMENTSCOPE_SERVER_URL, chỉ cần https://tên-miền)",
                status=status,
            )
        return ControlPlaneError(f"VPS trả lỗi HTTP {status} {response.reason_phrase}".rstrip(), status=status)
    return ControlPlaneError(f"VPS trả lỗi HTTP {status}: {detail}", status=status, detail=detail)


def _detail(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("detail"), str):
        return str(data["detail"])
    return None


def _short(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= limit else f"{text[:limit]}…"


def _bad(key: str) -> ControlPlaneError:
    return ControlPlaneError(f"Phản hồi từ VPS thiếu hoặc sai trường '{key}' (agent và server lệch phiên bản?)")


def _obj(value: object, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(key)
    return value


def _str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _bad(key)
    return value


def _opt_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise _bad(key)
    return value


def _int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(key)
    return value


def _bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise _bad(key)
    return value


def _time(data: dict[str, Any], key: str) -> datetime:
    try:
        value = datetime.fromisoformat(_str(data, key))
    except ValueError:
        raise _bad(key) from None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _opt_time(data: dict[str, Any], key: str) -> datetime | None:
    return None if data.get(key) is None else _time(data, key)


def _str_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _bad(key)
    return value


def _leased_proxy(data: dict[str, Any]) -> LeasedProxy:
    return LeasedProxy(
        id=_int(data, "id"),
        kind=_str(data, "kind"),
        protocol=_str(data, "protocol"),
        host=_str(data, "host"),
        port=_int(data, "port"),
        username=_opt_str(data, "username"),
        password=_opt_str(data, "password"),
        pool=_str(data, "pool"),
        exit_ip=_opt_str(data, "exit_ip"),
        country=_opt_str(data, "country"),
    )
