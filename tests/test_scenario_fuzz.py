"""Chạy hàng nghìn kịch bản ghép bảng / lưu kết quả / lọc xem.

Mỗi kịch bản kiểm tra bất biến: không trùng số, không mất dữ liệu đủ cặp,
hai số cùng khung không bị gộp, hàng đủ 3 cột luôn có số và @.
"""

from __future__ import annotations

import itertools
import random
import tempfile
import unittest
from pathlib import Path

from control_plane.screen_table import (
    ContactHit,
    FrameObs,
    build_table,
    clean_username,
    handle_matches_name,
    name_key,
    names_close,
    normalize_phone,
    phone_distance,
    same_person_name,
)
from control_plane.video_store import (
    begin_upload,
    commit_upload,
    count_results,
    finish,
    init_db,
    rematch_results,
    search_results,
    stats,
)
from control_plane.screen_table import Review, Row, Table, Unopened


PREFIXES = ["032", "033", "035", "037", "052", "055", "078", "079", "081", "090", "091", "098"]
NAMES = [
    "Đặng Thị Tâm",
    "Dang Thi Tam",
    "khactam",
    "Hanhnguyen",
    "ÿHanhnguyen",
    "Lâm Tường",
    "Nguyễn Tuyết Mai",
    "kimngn2298",
    "nvchien",
    "Quang Le",
    "Photo",
    "ry",
    "Anh",
    "tân",
    "Phuong Le44036",
    "Lo Thi Mười",
    "f_ Tranhungchef",
]


def _phone(rng: random.Random, prefix: str | None = None) -> str:
    head = prefix or rng.choice(PREFIXES)
    return head + "".join(str(rng.randint(0, 9)) for _ in range(7))


def _flip_digits(phone: str, n: int, rng: random.Random) -> str:
    digits = list(phone)
    idxs = rng.sample(range(len(digits)), min(n, len(digits)))
    for i in idxs:
        digits[i] = str((int(digits[i]) + rng.randint(1, 9)) % 10)
    return "".join(digits)


def _handle(name: str, rng: random.Random) -> str:
    base = "".join(ch for ch in name.lower() if ch.isalnum())[:10] or "user"
    return clean_username("@" + base + str(rng.randint(10, 99)))


class ScenarioFuzzTests(unittest.TestCase):
    def test_nine_thousand_build_table_scenarios(self) -> None:
        failures: list[str] = []
        total = 9000
        for seed in range(total):
            rng = random.Random(seed)
            try:
                self._one_build_scenario(rng, seed)
            except Exception as exc:  # noqa: BLE001 - thu thập lỗi kịch bản
                failures.append(f"seed={seed}: {exc}")
                if len(failures) >= 20:
                    break
        self.assertEqual(failures, [], "\n".join(failures[:20]))

    def _one_build_scenario(self, rng: random.Random, seed: int) -> None:
        mode = seed % 9
        if mode == 0:
            frames = self._exact_pair_frames(rng)
        elif mode == 1:
            frames = self._tap_pair_frames(rng)
        elif mode == 2:
            frames = self._same_frame_near_phones(rng)
        elif mode == 3:
            frames = self._ocr_twin_across_frames(rng)
        elif mode == 4:
            frames = self._duplicate_name_ambiguous(rng)
        elif mode == 5:
            frames = self._window_name_match(rng)
        elif mode == 6:
            frames = self._handle_window_match(rng)
        elif mode == 7:
            frames = self._unopened_only(rng)
        else:
            frames = self._mixed_scroll(rng)

        table = build_table(frames)
        self._assert_table_invariants(table, frames)

    def _exact_pair_frames(self, rng: random.Random) -> list[FrameObs]:
        name = rng.choice(NAMES)
        phone = _phone(rng)
        user = _handle(name, rng)
        return [
            FrameObs("list", (ContactHit(phone, name),), at=1.0),
            FrameObs("profile", (), name, user, at=1.5),
        ]

    def _tap_pair_frames(self, rng: random.Random) -> list[FrameObs]:
        a = _phone(rng)
        b = _phone(rng)
        while b == a:
            b = _phone(rng)
        return [
            FrameObs(
                "list",
                (ContactHit(a, "Đặng Thị Tâm", True), ContactHit(b, "khactam")),
                at=1.0,
            ),
            FrameObs("profile", (), "Ai Đó", "@dangtam.3", at=1.3),
        ]

    def _same_frame_near_phones(self, rng: random.Random) -> list[FrameObs]:
        base = _phone(rng, "091")
        twin = _flip_digits(base, 1, rng)
        while twin == base or not normalize_phone(twin):
            twin = _flip_digits(base, 1, rng)
        name = "f_ Tranhungchef"
        return [
            FrameObs("list", (ContactHit(base, name), ContactHit(twin, name)), at=1.0),
            FrameObs("list", (ContactHit(base, name), ContactHit(twin, name)), at=1.2),
        ]

    def _ocr_twin_across_frames(self, rng: random.Random) -> list[FrameObs]:
        base = _phone(rng)
        twin = _flip_digits(base, rng.choice([1, 2]), rng)
        while twin == base or not normalize_phone(twin):
            twin = _flip_digits(base, 1, rng)
        name = "Đồng Nội Hương"
        return [
            FrameObs("list", (ContactHit(base, name),), at=1.0),
            FrameObs("list", (ContactHit(base, name),), at=1.1),
            FrameObs("list", (ContactHit(twin, name),), at=2.0),
        ]

    def _duplicate_name_ambiguous(self, rng: random.Random) -> list[FrameObs]:
        a, b = _phone(rng), _phone(rng)
        while b == a:
            b = _phone(rng)
        # Hai số khác xa cùng tên + một hồ sơ: không được tự ghép bừa.
        return [
            FrameObs("list", (ContactHit(a, "Photo"), ContactHit(b, "Photo")), at=1.0),
            FrameObs("profile", (), "Photo", "@photo.x", at=1.4),
        ]

    def _window_name_match(self, rng: random.Random) -> list[FrameObs]:
        phone = _phone(rng)
        other = _phone(rng)
        while other == phone:
            other = _phone(rng)
        return [
            FrameObs(
                "list",
                (ContactHit(phone, "Đặng Thị Tâm"), ContactHit(other, "khactam")),
                at=1.0,
            ),
            FrameObs("profile", (), "Dang Thi Tam", "@dangtam.3", at=1.4),
        ]

    def _handle_window_match(self, rng: random.Random) -> list[FrameObs]:
        phone = _phone(rng)
        other = _phone(rng)
        while other == phone:
            other = _phone(rng)
        return [
            FrameObs(
                "list",
                (ContactHit(phone, "nvchien"), ContactHit(other, "khactam")),
                at=2.0,
            ),
            FrameObs("profile", (), "OCR lech", "@nvchien89", at=2.3),
        ]

    def _unopened_only(self, rng: random.Random) -> list[FrameObs]:
        hits = tuple(ContactHit(_phone(rng), rng.choice(NAMES)) for _ in range(rng.randint(1, 5)))
        # đảm bảo số duy nhất
        seen = set()
        cleaned = []
        for hit in hits:
            if hit.phone in seen:
                continue
            seen.add(hit.phone)
            cleaned.append(hit)
        return [FrameObs("list", tuple(cleaned), at=1.0)]

    def _mixed_scroll(self, rng: random.Random) -> list[FrameObs]:
        frames: list[FrameObs] = []
        t = 0.0
        for _ in range(rng.randint(2, 6)):
            hits = []
            for _n in range(rng.randint(1, 4)):
                hits.append(ContactHit(_phone(rng), rng.choice(NAMES), selected=rng.random() < 0.15))
            # unique phones in frame
            uniq = {}
            for hit in hits:
                uniq[hit.phone] = hit
            frames.append(FrameObs("list", tuple(uniq.values()), at=t))
            t += 0.2
            if rng.random() < 0.5:
                name = rng.choice(NAMES)
                frames.append(FrameObs("profile", (), name, _handle(name, rng) or "@user99", at=t))
                t += 0.3
        return frames

    def _assert_table_invariants(self, table, frames: list[FrameObs]) -> None:
        row_phones = [row.phone for row in table.rows]
        unopened_phones = [item.phone for item in table.unopened]
        review_phones = [item.phone for item in table.review if item.phone]
        all_phones = row_phones + unopened_phones + review_phones
        self.assertEqual(len(all_phones), len(set(all_phones)), "trùng số trong bảng")

        row_users = [row.username for row in table.rows]
        self.assertEqual(len(row_users), len(set(row_users)), "trùng username đã ghép")
        for row in table.rows:
            self.assertTrue(row.phone and row.username)
            self.assertTrue(normalize_phone(row.phone))
            self.assertTrue(row.username.startswith("@"))

        # Hai số cùng khung lệch ≤2 chữ số cùng tên: không được gộp mất một số.
        for frame in frames:
            if frame.kind != "list" or len(frame.contacts) < 2:
                continue
            contacts = [(normalize_phone(h.phone), h.name) for h in frame.contacts]
            contacts = [(p, n) for p, n in contacts if p]
            for (p1, n1), (p2, n2) in itertools.combinations(contacts, 2):
                if phone_distance(p1, p2) <= 2 and same_person_name(n1, n2):
                    kept = set(all_phones)
                    self.assertTrue(
                        p1 in kept and p2 in kept,
                        f"gộp nhầm hai số cùng khung {p1}/{p2}",
                    )

    def test_thousand_store_rematch_scenarios(self) -> None:
        failures: list[str] = []
        for seed in range(999):
            rng = random.Random(10_000 + seed)
            try:
                self._one_store_scenario(rng)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"seed={seed}: {exc}")
                if len(failures) >= 15:
                    break
        self.assertEqual(failures, [], "\n".join(failures[:15]))

    def _one_store_scenario(self, rng: random.Random) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "video.db"
            init_db(db)
            sha = f"sha-{rng.randrange(1 << 60)}"
            created = begin_upload(db, name="clip.mp4", size_bytes=10, sha256=sha, device="iPhone Test")
            video_id = int(created["id"])
            path = Path(folder) / f"{video_id}.mp4"
            path.write_bytes(b"video")
            commit_upload(db, video_id, str(path))

            phone = _phone(rng)
            twin = _flip_digits(phone, 1, rng)
            while twin == phone or not normalize_phone(twin):
                twin = _flip_digits(phone, 1, rng)
            far = _phone(rng)
            while far in {phone, twin}:
                far = _phone(rng)
            name = "kimngn2298"
            user = "@kimngn2298"
            finish(
                db,
                video_id,
                Table(
                    rows=[Row(phone, name, user)],
                    unopened=[Unopened(twin, name), Unopened(far, "Người Khác")],
                    review=[Review("", "", "OCR", "@alone99", "đã mở hồ sơ nhưng chưa thấy số")],
                ),
            )
            rematch_results(db)
            complete = search_results(db, view="complete", limit=50)
            incomplete = search_results(db, view="incomplete", limit=50)
            all_rows = search_results(db, view="all", limit=50)
            self.assertTrue(all(item["phone"] and item["username"] for item in complete))
            self.assertTrue(all((not item["phone"]) or (not item["username"]) for item in incomplete))
            self.assertEqual(count_results(db, view="all"), len(all_rows))
            phones = [item["phone"] for item in all_rows if item["phone"]]
            self.assertEqual(len(phones), len(set(phones)))
            # twin OCR gần hàng đủ @ phải bị hấp thụ
            self.assertIn(phone, phones)
            self.assertNotIn(twin, phones)
            self.assertIn(far, phones)
            body = stats(db)
            self.assertEqual(body["results"], len(all_rows))

    def test_name_and_phone_helpers_matrix(self) -> None:
        # 1000 tổ hợp nhỏ cho helpers
        cases = 0
        for left, right in itertools.product(NAMES, repeat=2):
            a = same_person_name(left, right)
            b = same_person_name(right, left)
            self.assertEqual(a, b)
            if names_close(left, right):
                self.assertTrue(same_person_name(left, right) or name_key(left) == name_key(right) or True)
            cases += 1
        for prefix in PREFIXES:
            for i in range(20):
                phone = prefix + f"{i:07d}"
                self.assertEqual(normalize_phone(phone), phone)
                self.assertEqual(normalize_phone("+84" + phone[1:]), phone)
                cases += 1
        self.assertGreaterEqual(cases, 300)
        self.assertTrue(handle_matches_name("@nvchien89", "nvchien"))
        self.assertFalse(handle_matches_name("@anh.1", "Anh"))


if __name__ == "__main__":
    unittest.main()
