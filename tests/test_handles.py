"""Lấy @handle từ link / chữ dán / QR."""

from __future__ import annotations

import unittest

from control_plane.handles import exact_line, handle_from_url, profile_from_share, urls_in


class HandleFromUrlTests(unittest.TestCase):
    def test_tiktok_profile_and_video(self) -> None:
        self.assertEqual(handle_from_url("https://www.tiktok.com/@trn.tng751"), "@trn.tng751")
        self.assertEqual(
            handle_from_url("https://www.tiktok.com/@trn.tng751/video/1234567890123456789"),
            "@trn.tng751",
        )
        self.assertEqual(handle_from_url("vm.tiktok.com/ZMabcdefg"), "")

    def test_instagram_and_facebook(self) -> None:
        self.assertEqual(handle_from_url("https://www.instagram.com/hoanganh1116/"), "@hoanganh1116")
        self.assertEqual(handle_from_url("https://instagram.com/reel/AbCdEf/"), "")
        self.assertEqual(handle_from_url("https://www.facebook.com/b.soi22"), "@b.soi22")
        self.assertEqual(handle_from_url("https://www.facebook.com/profile.php?id=100012345"), "")

    def test_zalo_and_youtube(self) -> None:
        self.assertEqual(handle_from_url("https://zalo.me/nguyen.anh88"), "@nguyen.anh88")
        self.assertEqual(handle_from_url("https://www.youtube.com/@channel.name"), "@channel.name")
        self.assertEqual(handle_from_url("https://www.youtube.com/watch?v=dQw4w9wg"), "")

    def test_share_text_keeps_exact_handle(self) -> None:
        found = profile_from_share("Trần Tùng https://www.tiktok.com/@trn.tng751")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["username"], "@trn.tng751")
        self.assertEqual(found["name"], "Trần Tùng")
        self.assertEqual(found["kind"], "profile")

    def test_url_only_uses_handle_as_name(self) -> None:
        found = profile_from_share("https://www.tiktok.com/@trn.tng751")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["username"], "@trn.tng751")
        self.assertEqual(found["name"], "trn.tng751")

    def test_pasted_at_handle(self) -> None:
        found = profile_from_share("Nguyễn Anh @nguyen.anh")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["username"], "@nguyen.anh")
        self.assertEqual(found["name"], "Nguyễn Anh")

    def test_exact_line_does_not_strip_url(self) -> None:
        text = "  https://www.tiktok.com/@trn.tng751  Trần Tùng  "
        self.assertIn("tiktok.com/@trn.tng751", exact_line(text))
        self.assertEqual(urls_in(text)[0], "https://www.tiktok.com/@trn.tng751")

    def test_junk_handle_is_rejected(self) -> None:
        self.assertIsNone(profile_from_share("https://www.tiktok.com/@trn!tng"))
        self.assertEqual(handle_from_url("https://example.com/user"), "")
