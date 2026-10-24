"""Xoay IP proxy 4G kq2 — mỗi key 5 phút, lệch nhau để quét không dừng hết."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlparse

import httpx

from fb_poller.config import Settings
from fb_poller.workers.proxy_manager import load_proxy_file

RotateHook = Callable[["RotateTarget", "RotateResult"], Awaitable[None] | None]


@dataclass(frozen=True)
class RotateTarget:
    index: int
    endpoint: str
    rotate_url: str
    name: str


@dataclass
class RotateResult:
    ok: bool
    index: int
    endpoint: str
    rotate_url: str
    http_status: int | None
    api_status: int | str | None
    message: str
    body: str = ""


@dataclass
class RotateState:
    last_ok_at: float = 0.0
    last_attempt_at: float = 0.0
    last_ok: bool = False
    last_message: str = ""


def load_rotate_targets(settings: Settings) -> list[RotateTarget]:
    endpoints = load_proxy_file(settings.proxies_4g_file)
    urls = load_proxy_file(settings.proxies_4g_rotate_file)
    n = min(len(endpoints), len(urls))
    targets: list[RotateTarget] = []
    for i in range(n):
        url = urls[i]
        parsed = urlparse(url)
        name = parsed.path.rstrip("/").split("/")[-1] or str(i + 1)
        targets.append(
            RotateTarget(
                index=i + 1,
                endpoint=endpoints[i],
                rotate_url=url,
                name=f"Key {name}",
            )
        )
    return targets


def stagger_gap_sec(count: int, interval_sec: float) -> float:
    if count <= 1:
        return interval_sec
    return interval_sec / count


def rotate_succeeded(http_status: int | None, body: str) -> tuple[bool, int | str | None, str]:
    """Chỉ thành công khi API báo đổi IP xong — HTTP 200 vẫn có thể là 'cần chờ thêm'."""
    raw = (body or "").strip()
    if http_status is None:
        return False, None, "no_response"
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        api_status = data.get("status")
        message = str(data.get("message") or "")
        text = message.casefold()
        if api_status in (200, "200"):
            if any(bad in text for bad in ("thất bại", "that bai", "cần chờ", "can cho", "fail")):
                return False, api_status, message or "api_200_but_failed"
            return True, api_status, message or "ok"
        return False, api_status, message or f"api_status={api_status}"
    if http_status == 200 and any(ok in raw.casefold() for ok in ("thành công", "thanh cong", "success")):
        if any(bad in raw.casefold() for bad in ("thất bại", "that bai", "cần chờ", "fail")):
            return False, http_status, raw[:200]
        return True, http_status, raw[:200]
    return False, http_status, raw[:200] or f"http_{http_status}"


async def press_rotate(target: RotateTarget, *, timeout_sec: float = 15.0) -> RotateResult:
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(target.rotate_url)
            body = resp.text or ""
            ok, api_status, message = rotate_succeeded(resp.status_code, body)
            return RotateResult(
                ok=ok,
                index=target.index,
                endpoint=target.endpoint,
                rotate_url=target.rotate_url,
                http_status=resp.status_code,
                api_status=api_status,
                message=message,
                body=body[:500],
            )
    except Exception as exc:
        return RotateResult(
            ok=False,
            index=target.index,
            endpoint=target.endpoint,
            rotate_url=target.rotate_url,
            http_status=None,
            api_status=None,
            message=f"{type(exc).__name__}: {exc}"[:240],
        )


def load_state(path: Path) -> dict[str, RotateState]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    out: dict[str, RotateState] = {}
    for key, row in items.items():
        if not isinstance(row, dict):
            continue
        out[str(key)] = RotateState(
            last_ok_at=float(row.get("last_ok_at") or 0),
            last_attempt_at=float(row.get("last_attempt_at") or 0),
            last_ok=bool(row.get("last_ok")),
            last_message=str(row.get("last_message") or ""),
        )
    return out


def save_state(path: Path, state: dict[str, RotateState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "items": {
            key: {
                "last_ok_at": row.last_ok_at,
                "last_attempt_at": row.last_attempt_at,
                "last_ok": row.last_ok,
                "last_message": row.last_message,
            }
            for key, row in state.items()
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def next_due_at(
    *,
    index: int,
    count: int,
    interval_sec: float,
    last_ok_at: float,
    started_at: float,
) -> float:
    gap = stagger_gap_sec(count, interval_sec)
    if last_ok_at > 0:
        return last_ok_at + interval_sec
    return started_at + interval_sec + (index - 1) * gap


@dataclass
class RotateLoop:
    targets: list[RotateTarget]
    interval_sec: float
    state_path: Path
    timeout_sec: float = 15.0
    retry_sec: float = 60.0
    press: Callable[[RotateTarget], Awaitable[RotateResult]] = press_rotate
    now: Callable[[], float] = time.time
    state: dict[str, RotateState] = field(default_factory=dict)
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.state:
            self.state = load_state(self.state_path)
        if not self.started_at:
            self.started_at = self.now()

    def _key(self, target: RotateTarget) -> str:
        return str(target.index)

    def due_targets(self, at: float | None = None) -> list[RotateTarget]:
        at = self.now() if at is None else at
        due: list[tuple[float, RotateTarget]] = []
        n = len(self.targets)
        for target in self.targets:
            row = self.state.get(self._key(target), RotateState())
            when = next_due_at(
                index=target.index,
                count=n,
                interval_sec=self.interval_sec,
                last_ok_at=row.last_ok_at,
                started_at=self.started_at,
            )
            if row.last_attempt_at and not row.last_ok:
                wait = (
                    self.interval_sec
                    if any(tok in (row.last_message or "").casefold() for tok in ("chờ", "cho ", "cooldown"))
                    else self.retry_sec
                )
                when = max(when, row.last_attempt_at + wait)
            if at >= when:
                due.append((when, target))
        due.sort(key=lambda item: (item[0], item[1].index))
        return [target for _, target in due]

    def record(self, target: RotateTarget, result: RotateResult, at: float | None = None) -> None:
        at = self.now() if at is None else at
        row = self.state.get(self._key(target), RotateState())
        row.last_attempt_at = at
        row.last_ok = result.ok
        row.last_message = result.message
        if result.ok:
            row.last_ok_at = at
        self.state[self._key(target)] = row
        save_state(self.state_path, self.state)

    async def run_due(self, hook: RotateHook | None = None) -> list[RotateResult]:
        due = self.due_targets()
        if not due:
            return []
        # Một key mỗi nhịp — giữ lệch 30s, không ấn 10 cái cùng lúc.
        target = due[0]
        result = await self.press(target)
        if not result.ok and any(tok in (result.message or "").casefold() for tok in ("chờ", "cooldown")):
            key = self._key(target)
            row = self.state.get(key, RotateState())
            row.last_attempt_at = self.now()
            row.last_ok = False
            row.last_message = result.message
            self.state[key] = row
            save_state(self.state_path, self.state)
        else:
            self.record(target, result)
        if hook is not None:
            maybe = hook(target, result)
            if maybe is not None:
                await maybe
        return [result]

    async def run_forever(self, stop, hook: RotateHook | None = None, sleep: Callable[[float], Any] = None) -> None:
        sleeper = sleep or __import__("asyncio").sleep
        while not stop.is_set():
            await self.run_due(hook)
            await sleeper(1.0)


async def check_proxy_line(endpoint: str, *, timeout_sec: float = 12.0) -> dict[str, Any]:
    parsed = urlparse(endpoint) if "://" in endpoint else None
    if parsed and parsed.hostname:
        host, port, user, password = parsed.hostname, parsed.port, parsed.username or "", parsed.password or ""
        proxy_url = endpoint
    else:
        parts = endpoint.split(":")
        if len(parts) == 4:
            host, port, user, password = parts[0], int(parts[1]), parts[2], parts[3]
            proxy_url = f"http://{quote(user)}:{quote(password)}@{host}:{port}"
        elif len(parts) == 2:
            host, port, user, password = parts[0], int(parts[1]), "", ""
            proxy_url = f"http://{host}:{port}"
        else:
            return {"endpoint": endpoint, "ok": False, "error": "invalid_endpoint"}
    started = time.time()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url, timeout=timeout_sec, follow_redirects=True, trust_env=False
        ) as client:
            resp = await client.get("http://api.ipify.org")
            ip = (resp.text or "").strip()[:64]
            return {
                "endpoint": endpoint,
                "ok": resp.status_code < 400 and bool(ip),
                "exit_ip": ip or None,
                "http_status": resp.status_code,
                "ms": int((time.time() - started) * 1000),
                "error": None if resp.status_code < 400 else f"http_{resp.status_code}",
            }
    except Exception as exc:
        return {
            "endpoint": endpoint,
            "ok": False,
            "exit_ip": None,
            "http_status": None,
            "ms": int((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def build_loop(settings: Settings) -> RotateLoop:
    timeout = float(settings.rotate_4g_timeout_sec)

    async def _press(target: RotateTarget) -> RotateResult:
        return await press_rotate(target, timeout_sec=timeout)

    return RotateLoop(
        targets=load_rotate_targets(settings),
        interval_sec=max(60.0, float(settings.rotate_4g_interval_sec)),
        state_path=settings.rotate_4g_state_file,
        timeout_sec=timeout,
        press=_press,
    )


def rotate_map(settings: Settings) -> dict[str, str]:
    return {t.endpoint: t.rotate_url for t in load_rotate_targets(settings)}
