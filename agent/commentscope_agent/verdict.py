"""Chấm kết quả một lượt dùng proxy để báo lại VPS: ok, blocked (trang đích chặn IP) hoặc failed (proxy hỏng)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from commentscope_agent.client import Outcome

BLOCK_STATUSES = frozenset({403, 429})
GATEWAY_STATUSES = frozenset({502, 504})


@dataclass(frozen=True, slots=True)
class PageVisit:
    url: str
    status: int | None = None
    final_url: str | None = None
    title: str | None = None
    error: str | None = None
    elapsed_ms: int = 0
    screenshot: Path | None = None

    @property
    def broken(self) -> bool:
        return self.error is not None or self.status in GATEWAY_STATUSES

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or self.url


def judge(visits: Sequence[PageVisit], *, proxy_failures: int, last_proxy_error: str | None) -> tuple[Outcome, str]:
    """Lỗi do chính proxy (không kết nối được, sai mật khẩu, lỗi TLS) được xét trước tín hiệu bị chặn."""
    if not visits:
        return "cancelled", "Chưa mở trang nào"
    broken = [visit for visit in visits if visit.broken]
    if broken and proxy_failures:
        return "failed", last_proxy_error or _describe(broken[0])
    blocked = next((visit for visit in visits if visit.status in BLOCK_STATUSES), None)
    if blocked is not None:
        return "blocked", f"{blocked.host} trả HTTP {blocked.status}, IP của proxy có thể đã bị chặn"
    if broken:
        return "failed", _describe(broken[0])
    return "ok", f"Mở được {len(visits)} trang qua proxy"


def _describe(visit: PageVisit) -> str:
    if visit.error is None:
        return f"{visit.host} trả HTTP {visit.status} qua proxy"
    if "ERR_CERT_" in visit.error:
        return f"Lỗi chứng chỉ HTTPS khi mở {visit.host} (proxy có thể đang can thiệp kết nối HTTPS): {visit.error}"
    return f"Không mở được {visit.host} qua proxy: {visit.error}"
