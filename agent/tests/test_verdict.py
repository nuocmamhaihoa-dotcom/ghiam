"""Chấm kết quả một lượt dùng proxy để báo VPS: ok, blocked, failed hoặc cancelled."""

from __future__ import annotations

from commentscope_agent.verdict import PageVisit, judge

OK = PageVisit(url="https://www.example.com/p/1", status=200)


def test_nothing_opened_is_cancelled() -> None:
    assert judge([], proxy_failures=0, last_proxy_error=None) == ("cancelled", "Chưa mở trang nào")


def test_pages_that_load_are_ok() -> None:
    visits = [OK, PageVisit(url="http://example.org/", status=404)]
    assert judge(visits, proxy_failures=0, last_proxy_error=None) == ("ok", "Mở được 2 trang qua proxy")


def test_forbidden_or_rate_limited_means_the_ip_is_blocked() -> None:
    for status in (403, 429):
        visits = [OK, PageVisit(url="https://m.example.com/p/2", status=status)]
        assert judge(visits, proxy_failures=0, last_proxy_error=None) == (
            "blocked",
            f"m.example.com trả HTTP {status}, IP của proxy có thể đã bị chặn",
        )


def test_a_broken_proxy_outweighs_a_block_signal() -> None:
    visits = [PageVisit(url="https://a.example.com/", status=403), PageVisit(url="https://b.example.com/", error="x")]
    error = "Không kết nối được tới proxy #7: Connection refused"
    assert judge(visits, proxy_failures=1, last_proxy_error=error) == ("failed", error)


def test_proxy_hiccup_does_not_matter_when_every_page_loaded() -> None:
    assert judge([OK], proxy_failures=2, last_proxy_error="lỗi cũ") == ("ok", "Mở được 1 trang qua proxy")


def test_page_errors_without_proxy_errors_are_failures() -> None:
    timeout = PageVisit(url="https://slow.example.com/", error="Quá 45 giây mà trang chưa tải xong")
    assert judge([timeout], proxy_failures=0, last_proxy_error=None) == (
        "failed",
        "Không mở được slow.example.com qua proxy: Quá 45 giây mà trang chưa tải xong",
    )


def test_certificate_errors_hint_at_an_intercepting_proxy() -> None:
    visit = PageVisit(url="https://bank.example.com/", error="net::ERR_CERT_AUTHORITY_INVALID")
    outcome, detail = judge([visit], proxy_failures=0, last_proxy_error=None)
    assert outcome == "failed"
    assert detail.startswith("Lỗi chứng chỉ HTTPS khi mở bank.example.com")


def test_gateway_errors_from_the_proxy_are_failures() -> None:
    visit = PageVisit(url="http://origin.test/", status=502)
    assert visit.broken
    assert judge([visit], proxy_failures=0, last_proxy_error=None) == ("failed", "origin.test trả HTTP 502 qua proxy")
