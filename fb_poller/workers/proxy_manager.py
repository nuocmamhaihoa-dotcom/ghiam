from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fb_poller.config import Settings
from fb_poller.storage.db import session_scope
from fb_poller.storage.repo import ProxyRepo


@dataclass
class ProxyConfig:
    id: int | None
    server: str
    username: str | None = None
    password: str | None = None
    raw: str = ""
    kind: str = "static"
    rotate_url: str | None = None

    def as_playwright(self) -> dict[str, str] | None:
        if not self.server:
            return None
        cfg: dict[str, str] = {"server": self.server}
        if self.username:
            cfg["username"] = self.username
        if self.password:
            cfg["password"] = self.password
        return cfg


def parse_proxy_line(line: str) -> ProxyConfig | None:
    """
    Supported formats:
      http://user:pass@host:port
      host:port
      host:port:user:pass
      socks5://host:port
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if "://" in line:
        parsed = urlparse(line)
        if not parsed.hostname or not parsed.port:
            return None
        scheme = parsed.scheme or "http"
        server = f"{scheme}://{parsed.hostname}:{parsed.port}"
        return ProxyConfig(
            id=None,
            server=server,
            username=parsed.username,
            password=parsed.password,
            raw=line,
        )

    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        return ProxyConfig(id=None, server=f"http://{host}:{port}", raw=line)
    if len(parts) == 4:
        host, port, user, password = parts
        return ProxyConfig(
            id=None,
            server=f"http://{host}:{port}",
            username=user,
            password=password,
            raw=line,
        )
    return None


def load_proxy_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


async def import_proxy_files(_settings: Settings) -> dict[str, tuple[int, int]]:
    """Proxy đã gỡ — không nạp list vào DB."""
    return {"static": (0, 0), "4g": (0, 0)}


class ProxyManager:
    """Round-robin sticky proxies for a worker process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._configs: list[ProxyConfig] = []
        self._idx = 0

    async def load_for_worker(self, count: int) -> int:
        """Phần mềm không dùng proxy — browser đi IP máy."""
        self._configs = []
        return 0

    def next(self) -> ProxyConfig | None:
        if not self._configs:
            return None
        cfg = self._configs[self._idx % len(self._configs)]
        self._idx += 1
        return cfg

    def for_slot(self, slot: int) -> ProxyConfig | None:
        if not self._configs:
            return None
        return self._configs[slot % len(self._configs)]

    async def mark_sick(self, proxy_id: int | None) -> None:
        if proxy_id is None:
            return
        async with session_scope() as session:
            await ProxyRepo(session).mark_sick(proxy_id)
        self._configs = [c for c in self._configs if c.id != proxy_id]

    async def revive_endpoint(self, endpoint: str) -> None:
        """Sau xoay IP thành công: gỡ sick để kq2 dùng lại key đó."""
        from fb_poller.workers.rotate_4g import rotate_map

        async with session_scope() as session:
            proxy = await ProxyRepo(session).mark_ok(endpoint)
            if proxy is None:
                return
            parsed = parse_proxy_line(proxy.endpoint)
            if not parsed:
                return
            parsed.id = proxy.id
            parsed.kind = proxy.proxy_type or "4g"
            parsed.rotate_url = rotate_map(self.settings).get(proxy.endpoint)
            if any(c.id == parsed.id for c in self._configs):
                for c in self._configs:
                    if c.id == parsed.id:
                        c.kind = parsed.kind
                        c.rotate_url = parsed.rotate_url
                return
            self._configs.append(parsed)
