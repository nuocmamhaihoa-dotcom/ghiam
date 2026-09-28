"""Proxy liveness is an exit IP, not a 200 from a host the proxy cannot resolve."""

from __future__ import annotations

import unittest

from control_plane.proxy_check import check_targets, exit_ip_from_body


class ProxyCheckTests(unittest.TestCase):
    def test_live_body_is_an_ip(self) -> None:
        self.assertEqual(exit_ip_from_body(200, "1.2.3.4\n"), "1.2.3.4")
        self.assertEqual(
            exit_ip_from_body(200, "2403:6a40:0:13:6268:1f17:afe7:f68"),
            "2403:6a40:0:13:6268:1f17:afe7:f68",
        )
        self.assertIsNone(exit_ip_from_body(200, "<html>502 Bad Gateway</html>"))
        self.assertIsNone(exit_ip_from_body(502, "1.2.3.4"))
        self.assertIsNone(exit_ip_from_body(200, ""))

    def test_bad_check_host_is_not_the_only_target(self) -> None:
        urls = check_targets("http://api.ipify.org")
        self.assertEqual(urls[0], "http://api.ipify.org")
        self.assertIn("http://ident.me", urls)
        self.assertIn("http://ifconfig.me/ip", urls)
        self.assertIn("http://icanhazip.com", urls)
        self.assertEqual(len(urls), len(set(urls)))

    def test_configured_url_is_not_repeated(self) -> None:
        urls = check_targets("http://ident.me")
        self.assertEqual(urls[0], "http://ident.me")
        self.assertEqual(urls.count("http://ident.me"), 1)


if __name__ == "__main__":
    unittest.main()
