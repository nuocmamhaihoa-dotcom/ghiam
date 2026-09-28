"""Periodic live/die checks for static proxies (VPS hub)."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from control_plane import db
from control_plane.settings import settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# api.ipify.org returns 502 through these proxies. These three return the exit IP.
_FALLBACK_CHECK_URLS = (
    "http://ident.me",
    "http://ifconfig.me/ip",
    "http://icanhazip.com",
)


def check_targets(configured: str) -> list[str]:
    """Configured URL first, then known-good fallbacks, without duplicates."""
    urls: list[str] = []
    for url in (configured, *_FALLBACK_CHECK_URLS):
        text = url.strip()
        if text and text not in urls:
            urls.append(text)
    return urls


def exit_ip_from_body(status_code: int, body: str) -> str | None:
    """A live proxy returns only an IP. An HTML error page is not live."""
    if status_code >= 400:
        return None
    text = (body or "").strip()
    if not text or len(text) > 64:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def load_proxy_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def to_proxy_url(endpoint: str, *, user: str = "", password: str = "") -> str | None:
    """Normalize host:port / URL into httpx proxy URL."""
    ep = endpoint.strip()
    if not ep:
        return None
    if "://" in ep:
        parsed = urlparse(ep)
        if not parsed.hostname or not parsed.port:
            return None
        # If URL has no userinfo but shared auth provided, inject it
        if user and password and not parsed.username:
            return f"{parsed.scheme}://{user}:{password}@{parsed.hostname}:{parsed.port}"
        return ep
    parts = ep.split(":")
    if len(parts) == 2:
        host, port = parts
        if user and password:
            return f"http://{user}:{password}@{host}:{port}"
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, u, p = parts
        return f"http://{u}:{p}@{host}:{port}"
    return None


def _classify_http_error(code: int) -> str:
    if code in (401, 407):
        return f"http_{code} (cần user:pass)"
    if code == 502:
        return "http_502 (TCP ok — thường thiếu user:pass hoặc chưa whitelist IP VPS)"
    if code == 403:
        return "http_403 (bị chặn / chưa auth)"
    return f"http_{code}"


async def check_one(
    endpoint: str,
    *,
    timeout: float,
    check_url: str,
    sem: asyncio.Semaphore,
    user: str = "",
    password: str = "",
) -> dict[str, Any]:
    proxy_url = to_proxy_url(endpoint, user=user, password=password)
    started = time.perf_counter()
    async with sem:
        if not proxy_url:
            return {
                "endpoint": endpoint,
                "status": "die",
                "latency_ms": None,
                "exit_ip": None,
                "error": "invalid_endpoint",
            }
        # 1) TCP reachability
        try:
            parsed = urlparse(proxy_url)
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                raise ValueError("no host/port")
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=min(timeout, 5.0))
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            del reader
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            return {
                "endpoint": endpoint,
                "status": "die",
                "latency_ms": ms,
                "exit_ip": None,
                "error": f"tcp: {type(exc).__name__}: {exc}"[:240],
            }

        # 2) HTTP via proxy. One bad check host must not mark a working proxy dead.
        last_error = "http: no check"
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=timeout,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                for url in check_targets(check_url):
                    try:
                        resp = await client.get(url)
                    except Exception as exc:
                        msg = str(exc)
                        if "407" in msg:
                            last_error = "http_407 (cần user:pass)"
                        elif "502" in msg:
                            last_error = "http_502 (TCP ok — thường thiếu user:pass hoặc chưa whitelist IP VPS)"
                        else:
                            last_error = f"http: {type(exc).__name__}: {msg}"[:240]
                        continue
                    ip = exit_ip_from_body(resp.status_code, resp.text or "")
                    ms = int((time.perf_counter() - started) * 1000)
                    if ip:
                        return {
                            "endpoint": endpoint,
                            "status": "live",
                            "latency_ms": ms,
                            "exit_ip": ip,
                            "error": None,
                        }
                    if resp.status_code >= 400:
                        last_error = _classify_http_error(resp.status_code)
                    else:
                        last_error = "response is not an IP"
        except Exception as exc:
            last_error = f"http: {type(exc).__name__}: {exc}"[:240]
        ms = int((time.perf_counter() - started) * 1000)
        return {
            "endpoint": endpoint,
            "status": "die",
            "latency_ms": ms,
            "exit_ip": None,
            "error": last_error,
        }


async def run_proxy_check_once() -> dict[str, Any]:
    path = settings.proxies_file
    # Ensure control_data has a working copy
    cd_copy = settings.data_dir / "proxies_static.txt"
    lines = load_proxy_lines(path)
    if not lines and (settings.data_dir.parent / "deploy" / "vps" / "proxies_static.txt").exists():
        alt = settings.data_dir.parent / "deploy" / "vps" / "proxies_static.txt"
        lines = load_proxy_lines(alt)
        path = alt
    if lines and path.resolve() != cd_copy.resolve():
        cd_copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
        settings.proxies_file = cd_copy

    db.upsert_proxy_endpoints(settings.db_path, lines, "static")
    sem = asyncio.Semaphore(max(1, settings.proxy_check_concurrency))
    tasks = [
        check_one(
            ep,
            timeout=settings.proxy_check_timeout_sec,
            check_url=settings.proxy_check_url,
            sem=sem,
            user=settings.proxy_user,
            password=settings.proxy_pass,
        )
        for ep in lines
    ]
    started = time.perf_counter()
    results = await asyncio.gather(*tasks) if tasks else []
    now = utcnow()
    live = die = 0
    for r in results:
        db.update_proxy_check(
            settings.db_path,
            r["endpoint"],
            status=r["status"],
            latency_ms=r.get("latency_ms"),
            exit_ip=r.get("exit_ip"),
            error=r.get("error"),
            checked_at=now,
        )
        if r["status"] == "live":
            live += 1
        else:
            die += 1
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "checked": len(results),
        "live": live,
        "die": die,
        "elapsed_ms": elapsed_ms,
        "at": now,
        "file": str(settings.proxies_file),
    }


_checker_task: asyncio.Task[None] | None = None


async def _checker_loop() -> None:
    # Small delay so uvicorn finishes startup
    await asyncio.sleep(2)
    while True:
        try:
            summary = await run_proxy_check_once()
            print(
                f"[proxy-check] checked={summary['checked']} live={summary['live']} "
                f"die={summary['die']} ms={summary['elapsed_ms']}",
                flush=True,
            )
        except Exception as exc:
            print(f"[proxy-check] error: {exc}", flush=True)
        await asyncio.sleep(max(60, settings.proxy_check_interval_sec))


def start_background_checker() -> None:
    global _checker_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _checker_task is None or _checker_task.done():
        _checker_task = loop.create_task(_checker_loop())
