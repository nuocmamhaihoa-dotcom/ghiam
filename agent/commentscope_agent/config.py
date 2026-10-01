"""Cấu hình agent: file JSON < biến môi trường COMMENTSCOPE_* < tham số dòng lệnh."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_CONFIG_FILE = Path("config.json")
KINDS = ("static", "rotating")
ENV_KEYS = {
    "COMMENTSCOPE_SERVER_URL": "server_url",
    "COMMENTSCOPE_AGENT_TOKEN": "token",
    "COMMENTSCOPE_WORKER_ID": "worker_id",
    "COMMENTSCOPE_POOL": "pool",
    "COMMENTSCOPE_KIND": "kind",
    "COMMENTSCOPE_CHECK_URL": "check_url",
    "COMMENTSCOPE_CAPACITY": "capacity",
}
FILE_KEYS = frozenset(
    {
        "server_url",
        "token",
        "worker_id",
        "pool",
        "kind",
        "lease_ttl_sec",
        "lease_wait_sec",
        "request_timeout_sec",
        "check_url",
        "capacity",
    }
)


class ConfigError(Exception):
    """Cấu hình agent thiếu hoặc sai."""


@dataclass(frozen=True, slots=True)
class AgentConfig:
    server_url: str
    token: str = field(repr=False)
    worker_id: str
    pool: str | None = None
    kind: str | None = None
    lease_ttl_sec: int = 600
    lease_wait_sec: float = 120.0
    request_timeout_sec: float = 20.0
    check_url: str | None = None
    capacity: int = 1


def load_config(path: Path | None, env: Mapping[str, str], overrides: Mapping[str, Any]) -> AgentConfig:
    values = _read_file(path)
    for env_key, name in ENV_KEYS.items():
        value = env.get(env_key, "").strip()
        if value:
            if name == "capacity":
                try:
                    values[name] = int(value)
                except ValueError:
                    raise ConfigError("capacity phải là số nguyên") from None
            else:
                values[name] = value
    values.update({name: value for name, value in overrides.items() if value is not None})
    return _build(values)


def default_worker_id() -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-.")
    return (name or "pc")[:128]


def insecure_transport_warning(config: AgentConfig) -> str | None:
    parts = urlsplit(config.server_url)
    host = parts.hostname or ""
    if parts.scheme != "http" or _is_loopback_host(host):
        return None
    return (
        f"Cảnh báo: token agent đang được gửi qua http:// không mã hoá tới {host}. "
        "Hãy dùng https:// (Caddy trên VPS tự cấp chứng chỉ)."
    )


def _read_file(path: Path | None) -> dict[str, Any]:
    target = path if path is not None else DEFAULT_CONFIG_FILE
    if not target.exists():
        if path is not None:
            raise ConfigError(f"Không tìm thấy file cấu hình {target}")
        return {}
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Không đọc được file cấu hình {target}: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"File cấu hình {target} không phải JSON hợp lệ (dòng {exc.lineno}, cột {exc.colno})"
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(f"File cấu hình {target} phải là một object JSON {{...}}")
    unknown = sorted(set(data) - FILE_KEYS)
    if unknown:
        raise ConfigError(f"File cấu hình {target} có khoá không hỗ trợ: {', '.join(unknown)}")
    return {name: value for name, value in data.items() if value is not None}


def _build(values: Mapping[str, Any]) -> AgentConfig:
    server_url = _text(values, "server_url")
    if not server_url:
        raise ConfigError(
            'Thiếu địa chỉ VPS: đặt COMMENTSCOPE_SERVER_URL, khoá "server_url" trong config.json hoặc dùng --server'
        )
    server_url = server_url.rstrip("/")
    if not _is_http_url(server_url):
        raise ConfigError(
            f"Địa chỉ VPS phải bắt đầu bằng https:// hoặc http://, ví dụ https://scope.example.com: {server_url[:100]}"
        )
    token = _text(values, "token")
    if not token:
        raise ConfigError(
            'Thiếu token agent: đặt biến môi trường COMMENTSCOPE_AGENT_TOKEN hoặc khoá "token" trong config.json '
            "(dùng một token trong AGENT_TOKENS trên VPS)"
        )
    worker_id = _text(values, "worker_id") or default_worker_id()
    if len(worker_id) > 128:
        raise ConfigError("worker_id dài tối đa 128 ký tự")
    pool = _text(values, "pool") or None
    if pool is not None and len(pool) > 64:
        raise ConfigError("Tên pool dài tối đa 64 ký tự")
    kind = _text(values, "kind") or None
    if kind is not None and kind not in KINDS:
        raise ConfigError("kind phải là static (proxy tĩnh) hoặc rotating (proxy 4G xoay)")
    ttl = _integer(values, "lease_ttl_sec", 600)
    if not 30 <= ttl <= 86_400:
        raise ConfigError("lease_ttl_sec phải từ 30 đến 86400 giây")
    wait = _number(values, "lease_wait_sec", 120.0)
    if wait < 0:
        raise ConfigError("lease_wait_sec không được âm")
    timeout = _number(values, "request_timeout_sec", 20.0)
    if timeout <= 0:
        raise ConfigError("request_timeout_sec phải lớn hơn 0")
    check_url = _text(values, "check_url") or None
    if check_url is not None and not _is_http_url(check_url):
        raise ConfigError("check_url phải là địa chỉ http:// hoặc https://")
    capacity = _integer(values, "capacity", 1)
    if not 1 <= capacity <= 4:
        raise ConfigError("capacity phải từ 1 đến 4 (số bài đọc cùng lúc trên máy này)")
    return AgentConfig(
        server_url=server_url,
        token=token,
        worker_id=worker_id,
        pool=pool,
        kind=kind,
        lease_ttl_sec=ttl,
        lease_wait_sec=wait,
        request_timeout_sec=timeout,
        check_url=check_url,
        capacity=capacity,
    )


def _text(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{key} phải là chuỗi ký tự")
    return value.strip()


def _number(values: Mapping[str, Any], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} phải là số")
    return float(value)


def _integer(values: Mapping[str, Any], key: str, default: int) -> int:
    value = _number(values, key, default)
    if not value.is_integer():
        raise ConfigError(f"{key} phải là số nguyên")
    return int(value)


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
