"""Phân tích danh sách proxy dán hàng loạt (proxy tĩnh và proxy 4G xoay).

Mỗi dòng: ``<proxy> [link đổi IP] [tuỳ chọn key=value ...]``, các phần ngăn cách bằng ``|``,
dấu cách hoặc tab. Dòng trống và dòng bắt đầu bằng ``#`` hoặc ``//`` bị bỏ qua.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from app.models import ProxyKind, ProxyProtocol, RotationMode, resolve_rotation_mode

SESSION_PLACEHOLDER = "{session}"
DEFAULT_MAX_CONCURRENCY = {ProxyKind.STATIC: 2, ProxyKind.ROTATING: 1}
MAX_CONCURRENCY_LIMIT = 100
MAX_DURATION_SEC = 86_400
MASK = "•••"
HTTPS_PROTOCOL_NOTE = (
    "'https' nghĩa là kết nối TLS tới chính proxy. Nếu nhà cung cấp ghi 'HTTP/HTTPS' thì hãy dùng http"
)

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://(.*)$", re.DOTALL)
_EMBEDDED_URL_RE = re.compile(r":(?=https?://)", re.IGNORECASE)
_TOKEN_SPLIT_RE = re.compile(r"[|\s]+")
_OPTION_RE = re.compile(r"^([A-Za-z_]+)=(.*)$")
_PORT_RE = re.compile(r"^[0-9]{1,5}$")
_DURATION_RE = re.compile(r"^([0-9]{1,6})([smh]?)$", re.IGNORECASE)
_HOST_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_BARE_IPV6_RE = re.compile(r"^[0-9A-Fa-f:.]+$")
_POOL_RE = re.compile(r"^[\w.\-]{1,64}$")
_SECRET_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")

_SCHEMES = {
    "http": ProxyProtocol.HTTP,
    "https": ProxyProtocol.HTTPS,
    "socks5": ProxyProtocol.SOCKS5,
    "socks5h": ProxyProtocol.SOCKS5,
    "socks": ProxyProtocol.SOCKS5,
}
_UNSUPPORTED_SCHEMES = frozenset({"socks4", "socks4a"})
_KIND_ALIASES = {
    "static": ProxyKind.STATIC,
    "tinh": ProxyKind.STATIC,
    "rotating": ProxyKind.ROTATING,
    "4g": ProxyKind.ROTATING,
    "xoay": ProxyKind.ROTATING,
}
OPTION_KEYS = frozenset({"type", "pool", "interval", "cooldown", "concurrency", "method", "protocol"})

EndpointKey = tuple[str, str, int, str]


class ProxyParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Endpoint:
    protocol: ProxyProtocol | None
    host: str
    port: int
    username: str
    password: str | None


@dataclass(frozen=True, slots=True)
class ImportDefaults:
    kind: ProxyKind = ProxyKind.STATIC
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    pool: str = "default"
    max_concurrency: int | None = None
    rotation_interval_sec: int = 0
    rotation_cooldown_sec: int = 60
    rotation_method: str = "GET"
    rotate_on_block: bool = True


@dataclass(frozen=True, slots=True)
class ParsedProxy:
    kind: ProxyKind
    protocol: ProxyProtocol
    host: str
    port: int
    username: str
    password: str | None
    rotation_url: str | None
    rotation_method: str
    rotation_interval_sec: int
    rotation_cooldown_sec: int
    rotate_on_block: bool
    max_concurrency: int
    pool: str

    @property
    def uses_session(self) -> bool:
        return has_session_placeholder(self.username, self.password)

    @property
    def rotation_mode(self) -> RotationMode:
        return resolve_rotation_mode(
            self.kind,
            has_rotation_url=self.rotation_url is not None,
            uses_session=self.uses_session,
            rotation_interval_sec=self.rotation_interval_sec,
        )

    @property
    def endpoint_key(self) -> EndpointKey:
        return endpoint_key(self.protocol, self.host, self.port, self.username)


@dataclass(slots=True)
class ParsedLine:
    line_no: int
    raw: str
    proxy: ParsedProxy | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def endpoint_key(protocol: str, host: str, port: int, username: str) -> EndpointKey:
    return (str(protocol), host.lower(), port, username)


def has_session_placeholder(username: str, password: str | None) -> bool:
    return SESSION_PLACEHOLDER in username or SESSION_PLACEHOLDER in (password or "")


def host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def display_endpoint(protocol: str, host: str, port: int, username: str, *, has_password: bool) -> str:
    auth = ""
    if username:
        auth = f"{username}:{MASK}@" if has_password else f"{username}@"
    return f"{protocol}://{auth}{host_for_url(host)}:{port}"


def mask_secret_url(url: str) -> str:
    """Che khoá API trong link đổi IP (tham số query và đoạn đường dẫn trông giống khoá)."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{host_for_url(netloc)}:{parts.port}"
    segments = [
        f"{segment[:3]}{MASK}" if _SECRET_SEGMENT_RE.match(segment) and any(c.isdigit() for c in segment) else segment
        for segment in parts.path.split("/")
    ]
    query = "&".join(f"{key}={MASK}" for key, _ in parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme, netloc, "/".join(segments), query, ""))


def normalize_host(raw: str) -> tuple[str, int] | None:
    """Chuẩn hoá host. Trả về (host, điểm tin cậy) hoặc None nếu không hợp lệ.

    Điểm: 3 = địa chỉ IP, 2 = tên miền có dấu chấm, 1 = tên một nhãn (ví dụ ``localhost``).
    """
    host = raw.strip()
    if host.startswith("[") and host.endswith("]"):
        try:
            return ipaddress.IPv6Address(host[1:-1]).compressed, 3
        except ValueError:
            return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return address.compressed, 3
    host = host.rstrip(".")
    if not host or len(host) > 253:
        return None
    labels = host.split(".")
    if not all(_HOST_LABEL_RE.match(label) for label in labels) or labels[-1].isdigit():
        return None
    return host.lower(), 2 if len(labels) > 1 else 1


def parse_port(raw: str) -> int | None:
    if not _PORT_RE.match(raw):
        return None
    port = int(raw)
    return port if 1 <= port <= 65535 else None


def parse_duration(raw: str, *, option: str) -> int:
    match = _DURATION_RE.match(raw.strip())
    if match is None:
        raise ProxyParseError(f"Giá trị {option}='{_clip(raw)}' không hợp lệ (ví dụ: 300, 90s, 5m, 1h)")
    seconds = int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2).lower()]
    if seconds > MAX_DURATION_SEC:
        raise ProxyParseError(f"{option} tối đa {MAX_DURATION_SEC} giây")
    return seconds


def parse_endpoint(spec: str) -> Endpoint:
    """Phân tích phần proxy của một dòng (không gồm link đổi IP và tuỳ chọn)."""
    text = spec.strip()
    protocol: ProxyProtocol | None = None
    decode = False
    scheme_match = _SCHEME_RE.match(text)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme in _UNSUPPORTED_SCHEMES:
            raise ProxyParseError("Chưa hỗ trợ SOCKS4, hãy dùng HTTP hoặc SOCKS5")
        if scheme not in _SCHEMES:
            raise ProxyParseError(f"Giao thức '{_clip(scheme)}' không được hỗ trợ (chỉ http, https, socks5)")
        protocol = _SCHEMES[scheme]
        decode = True
        text = scheme_match.group(2).removesuffix("/")
        if any(char in text for char in "/?#"):
            raise ProxyParseError(
                "Proxy không được chứa đường dẫn. Nếu đây là link đổi IP, hãy đặt nó sau proxy "
                "(ngăn cách bằng dấu | hoặc khoảng trắng)"
            )
    if not text:
        raise ProxyParseError("Thiếu địa chỉ proxy")

    candidates: list[tuple[int, int, Endpoint]] = []

    def consider(hostport: str, username: str, password: str | None, priority: int) -> None:
        parsed = _parse_hostport(hostport)
        if parsed is not None:
            host, port, score = parsed
            candidates.append((score, priority, Endpoint(protocol, host, port, username, password)))

    if "@" in text:
        credentials, primary_hostport = text.rsplit("@", 1)
        consider(primary_hostport, *_split_credentials(credentials, decode=decode), priority=1)
        swapped_hostport, swapped_credentials = text.split("@", 1)
        consider(swapped_hostport, *_split_credentials(swapped_credentials, decode=decode), priority=0)
    elif text.startswith("["):
        closing = text.find("]")
        if closing == -1:
            raise ProxyParseError("Thiếu dấu ']' đóng địa chỉ IPv6")
        rest = text[closing + 1 :]
        if not rest.startswith(":"):
            raise ProxyParseError("Thiếu cổng sau địa chỉ IPv6, ví dụ [2001:db8::1]:8080")
        parts = rest[1:].split(":", 2)
        primary_hostport = f"{text[: closing + 1]}:{parts[0]}"
        if len(parts) == 2:
            raise ProxyParseError("Thiếu mật khẩu, định dạng: [ipv6]:port:user:pass")
        username, password = (parts[1], parts[2]) if len(parts) == 3 else ("", None)
        consider(primary_hostport, username, password, priority=1)
    else:
        parts = text.split(":")
        if len(parts) == 1:
            raise ProxyParseError("Thiếu cổng (port), định dạng: host:port")
        if len(parts) == 3:
            raise ProxyParseError("Thiếu mật khẩu, định dạng: host:port:user:pass")
        primary_hostport = f"{parts[0]}:{parts[1]}"
        if len(parts) == 2:
            consider(primary_hostport, "", None, priority=1)
        else:
            consider(primary_hostport, parts[2], ":".join(parts[3:]), priority=1)
            if len(parts) == 4:
                consider(f"{parts[2]}:{parts[3]}", parts[0], parts[1], priority=0)

    if not candidates:
        if text.count(":") >= 2 and _BARE_IPV6_RE.match(text):
            raise ProxyParseError("Địa chỉ IPv6 phải đặt trong ngoặc vuông, ví dụ [2001:db8::1]:8080")
        raise ProxyParseError(_describe_hostport_problem(primary_hostport))
    _, _, endpoint = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    return _validate_credentials(endpoint)


def parse_line(raw_line: str, line_no: int, defaults: ImportDefaults) -> ParsedLine | None:
    line = raw_line.strip().lstrip("\ufeff").strip()
    if not line or line.startswith(("#", "//")):
        return None
    result = ParsedLine(line_no=line_no, raw=line)
    try:
        result.proxy = _parse_proxy_line(line, defaults, result.warnings)
    except ProxyParseError as exc:
        result.error = str(exc)
    return result


def parse_proxy_text(text: str, defaults: ImportDefaults) -> list[ParsedLine]:
    results: list[ParsedLine] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        parsed = parse_line(raw_line, line_no, defaults)
        if parsed is not None:
            results.append(parsed)
    return results


def _parse_proxy_line(line: str, defaults: ImportDefaults, warnings: list[str]) -> ParsedProxy:
    tokens = [token for token in _TOKEN_SPLIT_RE.split(_separate_embedded_url(line)) if token]
    endpoint, rest = _parse_leading_endpoint(tokens)
    rotation_url, options = _parse_trailing_tokens(rest, warnings)
    return _build_proxy(endpoint, rotation_url, options, defaults, warnings)


def _separate_embedded_url(line: str) -> str:
    """Tách dạng ``host:port:user:pass:https://link-doi-ip`` thành hai phần."""
    scheme_match = _SCHEME_RE.match(line)
    start = scheme_match.end(1) + 3 if scheme_match else 0
    match = _EMBEDDED_URL_RE.search(line, start)
    if match is None:
        return line
    return f"{line[: match.start()]} {line[match.end() :]}"


def _parse_leading_endpoint(tokens: list[str]) -> tuple[Endpoint, list[str]]:
    if len(tokens) >= 2 and _is_columnar(tokens[0], tokens[1]):
        host_token, port_token, rest = tokens[0], tokens[1], tokens[2:]
        username, password = "", None
        if rest and not _is_url(rest[0]) and not _is_option(rest[0]):
            if len(rest) < 2 or _is_url(rest[1]) or _is_option(rest[1]):
                raise ProxyParseError("Thiếu mật khẩu (cột thứ 4) sau tên đăng nhập")
            username, password, rest = rest[0], rest[1], rest[2:]
        bare_host = host_token.strip("[]")
        hostport = f"[{bare_host}]:{port_token}" if ":" in bare_host else f"{host_token}:{port_token}"
        parsed = _parse_hostport(hostport)
        if parsed is None:
            raise ProxyParseError(_describe_hostport_problem(hostport))
        return _validate_credentials(Endpoint(None, parsed[0], parsed[1], username, password)), rest
    return parse_endpoint(tokens[0]), tokens[1:]


def _parse_trailing_tokens(tokens: list[str], warnings: list[str]) -> tuple[str | None, dict[str, str]]:
    rotation_url: str | None = None
    options: dict[str, str] = {}
    for token in tokens:
        if _is_url(token):
            if rotation_url is None:
                rotation_url = token
            else:
                warnings.append("Có nhiều hơn một link, chỉ dùng link đầu tiên làm link đổi IP")
            continue
        option = _OPTION_RE.match(token)
        if option and option.group(1).lower() in OPTION_KEYS:
            options[option.group(1).lower()] = option.group(2)
            continue
        warnings.append(f"Bỏ qua phần không nhận ra: '{_clip(token)}'")
    return rotation_url, options


def _build_proxy(
    endpoint: Endpoint,
    rotation_url: str | None,
    options: dict[str, str],
    defaults: ImportDefaults,
    warnings: list[str],
) -> ParsedProxy:
    option_protocol = _parse_protocol_option(options["protocol"]) if "protocol" in options else None
    if endpoint.protocol is not None:
        protocol = endpoint.protocol
        if option_protocol is not None and option_protocol != endpoint.protocol:
            warnings.append(f"Bỏ qua protocol={option_protocol}, dùng giao thức ghi trong proxy")
    else:
        protocol = option_protocol or defaults.protocol

    uses_session = has_session_placeholder(endpoint.username, endpoint.password)
    if "type" in options:
        kind = _parse_kind(options["type"])
    elif rotation_url is not None or uses_session:
        kind = ProxyKind.ROTATING
    else:
        kind = defaults.kind

    pool = _parse_pool(options["pool"]) if "pool" in options else defaults.pool
    if "concurrency" in options:
        max_concurrency = _parse_int(options["concurrency"], "concurrency", 1, MAX_CONCURRENCY_LIMIT)
    else:
        max_concurrency = defaults.max_concurrency or DEFAULT_MAX_CONCURRENCY[kind]

    if kind == ProxyKind.STATIC:
        if rotation_url is not None:
            warnings.append("Proxy tĩnh không dùng link đổi IP, đã bỏ qua link")
            rotation_url = None
        ignored = [key for key in ("interval", "cooldown", "method") if key in options]
        if ignored:
            warnings.append(f"Proxy tĩnh: bỏ qua tuỳ chọn {', '.join(ignored)}")
        if uses_session:
            warnings.append("Proxy tĩnh có {session}: sẽ dùng một session cố định")
        interval, cooldown, method, rotate_on_block = 0, defaults.rotation_cooldown_sec, "GET", False
    else:
        interval = (
            parse_duration(options["interval"], option="interval")
            if "interval" in options
            else defaults.rotation_interval_sec
        )
        cooldown = (
            parse_duration(options["cooldown"], option="cooldown")
            if "cooldown" in options
            else defaults.rotation_cooldown_sec
        )
        method = _parse_method(options["method"]) if "method" in options else defaults.rotation_method
        rotate_on_block = defaults.rotate_on_block
        if rotation_url is not None:
            rotation_url = validate_rotation_url(rotation_url, warnings)
        elif not uses_session and interval == 0:
            warnings.append("Proxy 4G chưa có link đổi IP hoặc {session}: hệ thống chỉ theo dõi IP, không tự xoay được")

    if protocol == ProxyProtocol.HTTPS:
        warnings.append(HTTPS_PROTOCOL_NOTE)

    return ParsedProxy(
        kind=kind,
        protocol=protocol,
        host=endpoint.host,
        port=endpoint.port,
        username=endpoint.username,
        password=endpoint.password,
        rotation_url=rotation_url,
        rotation_method=method,
        rotation_interval_sec=interval,
        rotation_cooldown_sec=cooldown,
        rotate_on_block=rotate_on_block,
        max_concurrency=max_concurrency,
        pool=pool,
    )


def _parse_hostport(text: str) -> tuple[str, int, int] | None:
    text = text.strip()
    if text.startswith("["):
        closing = text.find("]")
        if closing == -1 or text[closing + 1 : closing + 2] != ":":
            return None
        host_part, port_part = text[: closing + 1], text[closing + 2 :]
    else:
        if text.count(":") != 1:
            return None
        host_part, port_part = text.split(":")
    host = normalize_host(host_part)
    port = parse_port(port_part)
    if host is None or port is None:
        return None
    return host[0], port, host[1]


def _describe_hostport_problem(hostport: str) -> str:
    host, separator, port = hostport.rpartition(":")
    if not separator:
        return "Thiếu cổng (port), định dạng: host:port"
    if normalize_host(host) is None:
        return f"Host không hợp lệ: '{_clip(host)}'"
    if parse_port(port) is None:
        return f"Cổng (port) không hợp lệ: '{_clip(port)}' (phải từ 1 đến 65535)"
    return "Không nhận dạng được proxy"


def _split_credentials(text: str, *, decode: bool) -> tuple[str, str | None]:
    username, separator, password = text.partition(":")
    if decode:
        return unquote(username), unquote(password) if separator else None
    return username, password if separator else None


def _validate_credentials(endpoint: Endpoint) -> Endpoint:
    password = endpoint.password or None
    if password is not None and not endpoint.username:
        raise ProxyParseError("Có mật khẩu nhưng thiếu tên đăng nhập")
    if len(endpoint.username) > 255 or (password is not None and len(password) > 255):
        raise ProxyParseError("Tên đăng nhập hoặc mật khẩu quá dài (tối đa 255 ký tự)")
    return replace(endpoint, password=password)


def validate_rotation_url(url: str, warnings: list[str]) -> str:
    if len(url) > 2000:
        raise ProxyParseError("Link đổi IP quá dài (tối đa 2000 ký tự)")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port
    except ValueError as exc:
        raise ProxyParseError("Link đổi IP không hợp lệ") from exc
    if parts.scheme.lower() not in ("http", "https") or not hostname:
        raise ProxyParseError("Link đổi IP không hợp lệ")
    if parts.path in ("", "/") and not parts.query:
        warnings.append("Link đổi IP không có đường dẫn hoặc tham số, hãy kiểm tra lại")
    return url


def _is_columnar(first: str, second: str) -> bool:
    if not _PORT_RE.match(second) or "@" in first or "://" in first:
        return False
    if ":" not in first:
        return True
    try:
        ipaddress.IPv6Address(first.strip("[]"))
    except ValueError:
        return False
    return True


def _is_url(token: str) -> bool:
    return token.lower().startswith(("http://", "https://"))


def _is_option(token: str) -> bool:
    option = _OPTION_RE.match(token)
    return bool(option and option.group(1).lower() in OPTION_KEYS)


def _parse_kind(raw: str) -> ProxyKind:
    kind = _KIND_ALIASES.get(raw.strip().lower())
    if kind is None:
        raise ProxyParseError(f"type='{_clip(raw)}' không hợp lệ (dùng static hoặc 4g)")
    return kind


def _parse_protocol_option(raw: str) -> ProxyProtocol:
    protocol = _SCHEMES.get(raw.strip().lower())
    if protocol is None:
        raise ProxyParseError(f"protocol='{_clip(raw)}' không hợp lệ (dùng http, https hoặc socks5)")
    return protocol


def _parse_method(raw: str) -> str:
    method = raw.strip().upper()
    if method not in ("GET", "POST"):
        raise ProxyParseError(f"method='{_clip(raw)}' không hợp lệ (dùng GET hoặc POST)")
    return method


def _parse_pool(raw: str) -> str:
    pool = raw.strip()
    if not _POOL_RE.match(pool):
        raise ProxyParseError(f"pool='{_clip(raw)}' không hợp lệ (1-64 ký tự chữ, số, . _ -, không dấu cách)")
    return pool


def _parse_int(raw: str, option: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"[0-9]{1,6}", raw.strip()):
        raise ProxyParseError(f"{option}='{_clip(raw)}' phải là số nguyên")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ProxyParseError(f"{option} phải trong khoảng {minimum}-{maximum}")
    return value


def _clip(value: str, limit: int = 40) -> str:
    return value if len(value) <= limit else f"{value[:limit]}…"
