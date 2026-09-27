from __future__ import annotations

import pytest

from app.models import ProxyKind, ProxyProtocol, RotationMode
from app.proxies.parser import (
    ImportDefaults,
    ParsedProxy,
    ProxyParseError,
    mask_secret_url,
    parse_duration,
    parse_endpoint,
    parse_line,
    parse_proxy_text,
)

STATIC = ImportDefaults()
ROTATING = ImportDefaults(kind=ProxyKind.ROTATING)


def parse_ok(line: str, defaults: ImportDefaults = STATIC) -> ParsedProxy:
    parsed = parse_line(line, 1, defaults)
    assert parsed is not None
    assert parsed.error is None, parsed.error
    assert parsed.proxy is not None
    return parsed.proxy


def parse_error(line: str, defaults: ImportDefaults = STATIC) -> str:
    parsed = parse_line(line, 1, defaults)
    assert parsed is not None
    assert parsed.proxy is None
    assert parsed.error is not None
    return parsed.error


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1.2.3.4:8080", ("http", "1.2.3.4", 8080, "", None)),
        ("1.2.3.4:8080:user:pass", ("http", "1.2.3.4", 8080, "user", "pass")),
        ("user:pass@1.2.3.4:8080", ("http", "1.2.3.4", 8080, "user", "pass")),
        ("1.2.3.4:8080@user:pass", ("http", "1.2.3.4", 8080, "user", "pass")),
        ("user:pass:1.2.3.4:8080", ("http", "1.2.3.4", 8080, "user", "pass")),
        ("admin:12345@1.2.3.4:8080", ("http", "1.2.3.4", 8080, "admin", "12345")),
        ("1.2.3.4:8080@user:1234", ("http", "1.2.3.4", 8080, "user", "1234")),
        ("user:1234:1.2.3.4:8080", ("http", "1.2.3.4", 8080, "user", "1234")),
        ("1.2.3.4:8080:user:pa:ss", ("http", "1.2.3.4", 8080, "user", "pa:ss")),
        ("1.2.3.4:8080:user:", ("http", "1.2.3.4", 8080, "user", None)),
        ("http://user:pass@Proxy.Example.COM:3128", ("http", "proxy.example.com", 3128, "user", "pass")),
        ("http://user:pass@proxy.example.com:3128/", ("http", "proxy.example.com", 3128, "user", "pass")),
        ("socks5://user:p%40ss@1.2.3.4:1080", ("socks5", "1.2.3.4", 1080, "user", "p@ss")),
        ("socks5h://1.2.3.4:1080", ("socks5", "1.2.3.4", 1080, "", None)),
        ("SOCKS5://1.2.3.4:1080:u:p", ("socks5", "1.2.3.4", 1080, "u", "p")),
        ("localhost:3128", ("http", "localhost", 3128, "", None)),
        ("[2001:db8::1]:8080", ("http", "2001:db8::1", 8080, "", None)),
        ("[2001:DB8:0::1]:8080:user:pass", ("http", "2001:db8::1", 8080, "user", "pass")),
        ("http://user:pass@[2001:db8::1]:8080", ("http", "2001:db8::1", 8080, "user", "pass")),
        ("token@1.2.3.4:8080", ("http", "1.2.3.4", 8080, "token", None)),
    ],
)
def test_supported_proxy_formats(line: str, expected: tuple[str, str, int, str, str | None]) -> None:
    proxy = parse_ok(line)
    assert (proxy.protocol, proxy.host, proxy.port, proxy.username, proxy.password) == expected
    assert proxy.kind == ProxyKind.STATIC


@pytest.mark.parametrize(
    "line",
    [
        "1.2.3.4\t8080\tuser\tpass",
        "1.2.3.4 8080 user pass",
        "1.2.3.4 | 8080 | user | pass",
    ],
)
def test_columnar_lines_from_spreadsheets(line: str) -> None:
    proxy = parse_ok(line)
    assert (proxy.host, proxy.port, proxy.username, proxy.password) == ("1.2.3.4", 8080, "user", "pass")


def test_columnar_ipv6_and_protocol_option() -> None:
    proxy = parse_ok("2001:db8::5 1080 protocol=socks5")
    assert (proxy.protocol, proxy.host, proxy.port) == ("socks5", "2001:db8::5", 1080)


def test_default_protocol_applies_only_without_scheme() -> None:
    defaults = ImportDefaults(protocol=ProxyProtocol.SOCKS5)
    assert parse_ok("1.2.3.4:1080", defaults).protocol == ProxyProtocol.SOCKS5
    assert parse_ok("http://1.2.3.4:8080", defaults).protocol == ProxyProtocol.HTTP


@pytest.mark.parametrize(
    "line",
    [
        "42.118.1.2:30012:user:pass|https://api.proxy4g.vn/change-ip?key=AAA",
        "42.118.1.2:30012:user:pass https://api.proxy4g.vn/change-ip?key=AAA",
        "42.118.1.2:30012:user:pass\thttps://api.proxy4g.vn/change-ip?key=AAA",
        "42.118.1.2:30012:user:pass:https://api.proxy4g.vn/change-ip?key=AAA",
        "http://user:pass@42.118.1.2:30012|https://api.proxy4g.vn/change-ip?key=AAA",
        "42.118.1.2 30012 user pass https://api.proxy4g.vn/change-ip?key=AAA",
    ],
)
def test_rotating_proxy_with_change_ip_link_is_detected(line: str) -> None:
    proxy = parse_ok(line)
    assert proxy.kind == ProxyKind.ROTATING
    assert proxy.rotation_mode == RotationMode.URL
    assert proxy.rotation_url == "https://api.proxy4g.vn/change-ip?key=AAA"
    assert (proxy.host, proxy.port, proxy.username, proxy.password) == ("42.118.1.2", 30012, "user", "pass")
    assert proxy.max_concurrency == 1


def test_session_placeholder_makes_proxy_rotating() -> None:
    proxy = parse_ok("gate.provider.com:7000:user-country-vn-session-{session}:secret")
    assert proxy.kind == ProxyKind.ROTATING
    assert proxy.rotation_mode == RotationMode.SESSION
    assert proxy.uses_session


def test_session_placeholder_in_password() -> None:
    proxy = parse_ok("gate.provider.com:12321:user:pass_session-{session}_lifetime-30m")
    assert proxy.rotation_mode == RotationMode.SESSION


def test_line_options_override_defaults() -> None:
    parsed = parse_line(
        "1.2.3.4:8080 type=4g interval=5m cooldown=90s pool=viettel concurrency=2 method=post", 1, STATIC
    )
    assert parsed is not None
    assert parsed.proxy is not None
    proxy = parsed.proxy
    assert proxy.kind == ProxyKind.ROTATING
    assert proxy.rotation_mode == RotationMode.PROVIDER
    assert (proxy.rotation_interval_sec, proxy.rotation_cooldown_sec) == (300, 90)
    assert (proxy.pool, proxy.max_concurrency, proxy.rotation_method) == ("viettel", 2, "POST")
    assert parsed.warnings == []


def test_rotating_defaults_applied_from_form() -> None:
    defaults = ImportDefaults(
        kind=ProxyKind.ROTATING,
        pool="4g-mobi",
        rotation_interval_sec=600,
        rotation_cooldown_sec=120,
        rotation_method="POST",
        rotate_on_block=False,
        max_concurrency=3,
    )
    proxy = parse_ok("10.0.0.1:9000:u:p|https://x.vn/reset?port=9000", defaults)
    assert proxy.pool == "4g-mobi"
    assert (proxy.rotation_interval_sec, proxy.rotation_cooldown_sec) == (600, 120)
    assert (proxy.rotation_method, proxy.rotate_on_block, proxy.max_concurrency) == ("POST", False, 3)


def test_rotating_without_link_warns() -> None:
    parsed = parse_line("1.2.3.4:8080:u:p", 1, ROTATING)
    assert parsed is not None
    assert parsed.proxy is not None
    assert parsed.proxy.kind == ProxyKind.ROTATING
    assert parsed.proxy.rotation_mode == RotationMode.NONE
    assert any("chưa có link đổi IP" in warning for warning in parsed.warnings)


def test_static_type_option_drops_link_with_warning() -> None:
    parsed = parse_line("1.2.3.4:8080:u:p https://x.vn/change?key=1 type=static", 1, STATIC)
    assert parsed is not None
    assert parsed.proxy is not None
    assert parsed.proxy.kind == ProxyKind.STATIC
    assert parsed.proxy.rotation_url is None
    assert any("bỏ qua link" in warning for warning in parsed.warnings)


def test_https_scheme_and_unknown_tokens_produce_warnings() -> None:
    parsed = parse_line("https://1.2.3.4:443 ghichu", 1, STATIC)
    assert parsed is not None
    assert parsed.proxy is not None
    assert parsed.proxy.protocol == ProxyProtocol.HTTPS
    assert len(parsed.warnings) == 2


def test_link_without_path_warns() -> None:
    parsed = parse_line("1.2.3.4:8080 http://5.6.7.8:9090", 1, STATIC)
    assert parsed is not None
    assert parsed.proxy is not None
    assert any("không có đường dẫn" in warning for warning in parsed.warnings)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("1.2.3.4", "Thiếu cổng"),
        ("1.2.3.4:99999", "Cổng (port) không hợp lệ"),
        ("1.2.3.4:abc", "Cổng (port) không hợp lệ"),
        ("1.2.3.4:8080:user", "Thiếu mật khẩu"),
        ("bad_host:80", "Host không hợp lệ"),
        ("1.2.3.999:80", "Host không hợp lệ"),
        ("socks4://1.2.3.4:1080", "SOCKS4"),
        ("ftp://1.2.3.4:21", "không được hỗ trợ"),
        ("https://api.vn/change?key=1", "không được chứa đường dẫn"),
        ("2001:db8::1:8080", "ngoặc vuông"),
        (":pass@1.2.3.4:8080", "thiếu tên đăng nhập"),
        ("1.2.3.4 8080 user https://x.vn/a?b=1", "Thiếu mật khẩu"),
        ("1.2.3.4:8080 type=abc", "type="),
        ("1.2.3.4:8080 type=4g interval=10x", "interval="),
        ("1.2.3.4:8080 concurrency=0", "concurrency"),
        ("1.2.3.4:8080 pool=a/b", "pool="),
        ("1.2.3.4:8080|https://", "Link đổi IP không hợp lệ"),
    ],
)
def test_invalid_lines_report_clear_errors(line: str, message: str) -> None:
    assert message in parse_error(line)


def test_blank_and_comment_lines_are_skipped() -> None:
    text = "\ufeff# danh sách proxy\n\n// ghi chú\n1.2.3.4:8080\n   \n5.6.7.8:3128:u:p\n"
    lines = parse_proxy_text(text, STATIC)
    assert [line.line_no for line in lines] == [4, 6]
    assert all(line.proxy is not None for line in lines)


def test_bom_on_first_line_is_ignored() -> None:
    lines = parse_proxy_text("\ufeff1.2.3.4:8080", STATIC)
    assert lines[0].proxy is not None


def test_endpoint_key_is_case_insensitive_for_host() -> None:
    first = parse_ok("Proxy.EXAMPLE.com:80:u:p")
    second = parse_ok("proxy.example.com:80:u:p")
    assert first.endpoint_key == second.endpoint_key


def test_parse_endpoint_rejects_empty() -> None:
    with pytest.raises(ProxyParseError):
        parse_endpoint("http://")


@pytest.mark.parametrize(("raw", "seconds"), [("300", 300), ("90s", 90), ("5m", 300), ("1H", 3600)])
def test_parse_duration(raw: str, seconds: int) -> None:
    assert parse_duration(raw, option="interval") == seconds


@pytest.mark.parametrize("raw", ["abc", "5d", "25h", "-5"])
def test_parse_duration_rejects_invalid(raw: str) -> None:
    with pytest.raises(ProxyParseError):
        parse_duration(raw, option="interval")


def test_mask_secret_url_hides_api_keys() -> None:
    assert mask_secret_url("https://api.vn/change?key=abc123&port=1") == "https://api.vn/change?key=•••&port=•••"
    masked = mask_secret_url("https://user:pw@app.proxyno1.com/api/change-key-ip/abcdef0123456789xyz")
    assert masked == "https://app.proxyno1.com/api/change-key-ip/abc•••"
