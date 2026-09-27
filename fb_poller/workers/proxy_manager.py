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
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


async def import_proxy_files(settings: Settings) -> dict[str, tuple[int, int]]:
    async with session_scope() as session:
        repo = ProxyRepo(session)
        static_lines = load_proxy_file(settings.proxies_static_file)
        g4_lines = load_proxy_file(settings.proxies_4g_file)
        static = await repo.upsert_endpoints(static_lines, "static")
        g4 = await repo.upsert_endpoints(g4_lines, "4g")
        return {"static": static, "4g": g4}


class ProxyManager:
    """Round-robin sticky proxies for a worker process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._configs: list[ProxyConfig] = []
        self._idx = 0

    async def load_for_worker(self, count: int) -> int:
        async with session_scope() as session:
            repo = ProxyRepo(session)
            proxies = await repo.assign_static(self.settings.worker_id, count)
            configs: list[ProxyConfig] = []
            for p in proxies:
                parsed = parse_proxy_line(p.endpoint)
                if not parsed:
                    continue
                parsed.id = p.id
                configs.append(parsed)
            self._configs = configs
        return len(self._configs)

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
