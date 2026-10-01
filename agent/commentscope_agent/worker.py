"""Nhận việc từ VPS và đọc comment công khai của bài Facebook."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from commentscope_agent.bridge import ProxyBridge, Upstream
from commentscope_agent.browser import ProxiedBrowser
from commentscope_agent.client import ControlPlaneClient, ControlPlaneError, NoWork, WorkAssignment
from commentscope_agent.reader import PageLike, ReadOptions, ReadResult, read_public_facebook

BATCH = 50


@dataclass(frozen=True, slots=True)
class BridgeStats:
    upstream_failures: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0


class PageOpener:
    """Mở Chromium qua proxy đã thuê. Test thay bằng trang giả."""

    def __init__(self, assignment: WorkAssignment, *, headless: bool = True, timeout_sec: float = 45) -> None:
        self._assignment = assignment
        self._headless = headless
        self._timeout_sec = timeout_sec

    @asynccontextmanager
    async def open(self) -> AsyncIterator[tuple[PageLike, Any]]:
        proxy = self._assignment.lease.proxy
        locale, timezone_id = _locale_for(proxy.country)
        upstream = Upstream(
            protocol=proxy.protocol,
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            password=proxy.password,
            label=proxy.label,
        )
        async with (
            ProxyBridge(upstream) as bridge,
            ProxiedBrowser(
                bridge.server_url,
                username=bridge.credentials.username,
                password=bridge.credentials.password,
                headless=self._headless,
                navigation_timeout_sec=self._timeout_sec,
                locale=locale,
                timezone_id=timezone_id,
            ) as browser,
        ):
            await browser.enable_savings()
            page = await browser.new_page()
            try:
                yield page, bridge
            finally:
                with contextlib.suppress(Exception):
                    await page.close()


async def execute_assignment(
    assignment: WorkAssignment,
    client: ControlPlaneClient,
    *,
    outbox: Path | None = None,
    headless: bool = True,
) -> ReadResult:
    async with PageOpener(assignment, headless=headless).open() as (page, bridge):
        return await run_assignment(assignment, client, page, bridge=bridge, outbox=outbox)


async def run_assignment(
    assignment: WorkAssignment,
    client: ControlPlaneClient,
    page: PageLike,
    *,
    bridge: Any | None = None,
    outbox: Path | None = None,
) -> ReadResult:
    options = ReadOptions(
        max_comments=assignment.max_comments,
        include_replies=assignment.include_replies,
        max_replies_per_comment=assignment.max_replies_per_comment,
        time_budget_sec=assignment.time_budget_sec,
    )
    try:
        result = await read_public_facebook(page, assignment.url, options)
    except asyncio.CancelledError:
        await _finish(client, assignment, _cancelled_body(), outbox)
        raise
    body = _complete_body(result, _stats(bridge))
    await _send_comments(client, assignment, result.payloads(), outbox)
    await _finish(client, assignment, body, outbox)
    return result


async def _send_comments(
    client: ControlPlaneClient,
    assignment: WorkAssignment,
    comments: Sequence[Mapping[str, Any]],
    outbox: Path | None,
) -> None:
    seq = 1
    for start in range(0, len(comments), BATCH):
        chunk = list(comments[start : start + BATCH])
        payload = {"kind": "progress", "attempt_id": assignment.attempt_id, "seq": seq, "comments": chunk}
        try:
            await client.report_progress(assignment.attempt_id, seq, chunk)
        except ControlPlaneError:
            _remember(outbox, payload)
            raise
        seq += 1


async def _finish(
    client: ControlPlaneClient, assignment: WorkAssignment, body: dict[str, Any], outbox: Path | None
) -> None:
    payload = {"kind": "complete", "attempt_id": assignment.attempt_id, **body}
    try:
        await client.complete_attempt(assignment.attempt_id, body)
    except ControlPlaneError:
        _remember(outbox, payload)
        raise


def _complete_body(result: ReadResult, stats: BridgeStats) -> dict[str, Any]:
    proxy_outcome = "ok"
    if result.outcome == "blocked":
        proxy_outcome = "blocked"
    elif result.outcome == "cancelled":
        proxy_outcome = "cancelled"
    elif result.outcome == "failed" and stats.upstream_failures:
        proxy_outcome = "failed"
    return {
        "outcome": result.outcome,
        "detail": result.detail[:500],
        "proxy_outcome": proxy_outcome,
        "comments_reported": result.comments_reported,
        "complete": result.complete,
        "stop_reason": result.stop_reason,
        "pages": result.pages,
        "bytes_transferred": stats.bytes_sent + stats.bytes_received,
    }


def _cancelled_body() -> dict[str, Any]:
    return {
        "outcome": "cancelled",
        "detail": "Máy PC dừng trong khi đang đọc",
        "proxy_outcome": "cancelled",
        "comments_reported": None,
        "complete": False,
        "stop_reason": "cancelled",
        "pages": 0,
        "bytes_transferred": 0,
    }


def _remember(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_lines(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").splitlines()


def _store_lines(path: Path, lines: list[str]) -> None:
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


async def flush_outbox(client: ControlPlaneClient, path: Path) -> None:
    lines = await asyncio.to_thread(_load_lines, path)
    if lines is None:
        return
    kept: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            if item.get("kind") == "progress":
                await client.report_progress(str(item["attempt_id"]), int(item["seq"]), list(item["comments"]))
            elif item.get("kind") == "complete":
                body = {key: value for key, value in item.items() if key not in {"kind", "attempt_id"}}
                await client.complete_attempt(str(item["attempt_id"]), body)
        except ControlPlaneError as exc:
            if exc.status == 404:
                continue
            kept.append(line)
    await asyncio.to_thread(_store_lines, path, kept)


async def claim_forever(
    client: ControlPlaneClient,
    *,
    worker_id: str,
    ttl_sec: int,
    stop: asyncio.Event,
    idle_sec: float = 5,
) -> AsyncIterator[WorkAssignment]:
    while not stop.is_set():
        try:
            claimed = await client.claim_work(worker_id=worker_id, ttl_sec=ttl_sec)
        except ControlPlaneError:
            await _wait(stop, idle_sec)
            continue
        if isinstance(claimed, NoWork):
            await _wait(stop, min(claimed.retry_after_sec, 30))
            continue
        yield claimed


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        return


def _stats(bridge: Any | None) -> BridgeStats:
    stats = getattr(bridge, "stats", None)
    return BridgeStats(
        upstream_failures=int(getattr(stats, "upstream_failures", 0) or 0),
        bytes_received=int(getattr(stats, "bytes_received", 0) or 0),
        bytes_sent=int(getattr(stats, "bytes_sent", 0) or 0),
    )


def _locale_for(country: str | None) -> tuple[str, str]:
    if country and country.upper() == "US":
        return "en-US", "America/New_York"
    return "vi-VN", "Asia/Ho_Chi_Minh"
