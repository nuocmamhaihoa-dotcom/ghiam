"""99999 kịch bản đường tải video và đường đọc/ghép.

Hàng đủ khi danh bạ và hồ sơ cùng tên đứng liền, hoặc trong cùng video tên
giống từ 90% và chỉ có một số với một @. Hai số hoặc hai @ thì không đoán.
Không ghép hồ sơ của khách khác. Số không mất. @ chỉ được bỏ khi khung đó
không phải hồ sơ. File tải lên khớp từng byte sau khi ghép mảnh.
"""

from __future__ import annotations

import os
import random
import tempfile
import threading
import unittest
from pathlib import Path

from control_plane.read_fallback import pair_leftovers_by_time
from control_plane.screen_table import (
    ContactHit,
    FrameObs,
    _handles_are_same,
    build_table,
    clean_name,
    clean_username,
    is_skipped,
    normalize_phone,
    profile_fields,
    phone_distance,
    same_person_name,
)
from control_plane.video_store import begin_upload, finish, init_db, search_results
from control_plane.video_upload_sessions import (
    abort_session,
    complete_upload,
    get_upload,
    init_upload,
    put_chunk,
)

PREFIXES = ["032", "033", "035", "037", "052", "055", "070", "078", "081", "090", "091", "098"]
NAMES = [
    "Đặng Thị Tâm",
    "khactam",
    "Nguyễn Tuyết Mai",
    "Bon Nguyenvan",
    "Trọng Nguyễn",
    "Lý Mai Trang",
    "Phương Anh",
    "Quang Le",
    "Hanhnguyen",
    "kimngn2298",
    "nvchien",
    "Lâm Tường",
    "Bích Ngọc",
    "An Khang",
    "Mai Phương",
]


def _scenario_count() -> int:
    """Mặc định 2000 khi chạy cùng suite. Lần đủ 99999: SCENARIO_COUNT=99999."""
    raw = os.environ.get("SCENARIO_COUNT", "").strip()
    if raw:
        return max(1, int(raw))
    return 2000


class _Phones:
    def __init__(self) -> None:
        self.used: list[str] = []
        self.n = 0

    def take(self) -> str:
        while True:
            self.n += 1
            prefix = PREFIXES[self.n % len(PREFIXES)]
            tail = f"{(self.n * 7919) % 10_000_000:07d}"
            phone = prefix + tail
            if normalize_phone(phone) != phone:
                continue
            if any(phone_distance(phone, prev) <= 2 for prev in self.used):
                continue
            self.used.append(phone)
            return phone


def _handle(index: int) -> str:
    return f"@u{index:05d}x"


def _list(hits: list[ContactHit], at: float) -> FrameObs:
    return FrameObs("list", tuple(hits), at=at)


def _profile(name: str, username: str, at: float) -> FrameObs:
    return FrameObs("profile", (), name, username, at=at)


def _output_phones(table) -> list[str]:
    phones = [row.phone for row in table.rows]
    phones += [item.phone for item in table.unopened]
    phones += [item.phone for item in table.review if item.phone]
    return phones


def _output_users(table) -> list[str]:
    users = [row.username for row in table.rows]
    users += [item.username for item in table.review if item.username]
    return users


def _assert_common(table, frames: list[FrameObs]) -> None:
    phones = _output_phones(table)
    if len(phones) != len(set(phones)):
        raise AssertionError(f"trùng số {phones}")
    users = _output_users(table)
    if len(users) != len(set(users)):
        raise AssertionError(f"trùng @ {users}")
    phone_names: dict[str, set[str]] = {}
    user_names: dict[str, set[str]] = {}
    for frame in frames:
        if frame.kind in ("list", "tap"):
            for hit in frame.contacts:
                phone = normalize_phone(hit.phone)
                name = clean_name(hit.name)
                if phone and name and not is_skipped(name):
                    phone_names.setdefault(phone, set()).add(name)
        if frame.kind == "profile":
            username = clean_username(frame.profile_username)
            if username:
                user_names.setdefault(username, set()).add(clean_name(frame.profile_name))
    for row in table.rows:
        if not (row.phone and row.name and row.username.startswith("@")):
            raise AssertionError(f"hàng thiếu ô {row}")
        pnames = phone_names.get(row.phone, set())
        unames = user_names.get(row.username, set())
        if not unames or not any(name for name in unames):
            raise AssertionError(f"ghép khi không có tên hồ sơ {row}")
        if not any(same_person_name(left, right) for left in pnames for right in unames if right):
            raise AssertionError(f"ghép hai tên khác nhau {row} {pnames} {unames}")
    for username, names in user_names.items():
        if not any(profile_fields(name, username)[1] for name in names):
            continue
        if any(_handles_are_same(username, found) for found in users):
            continue
        raise AssertionError(f"mất @{username} {names}")
    kept = set(phones)
    for phone in phone_names:
        if phone in kept:
            continue
        if any(phone_distance(phone, other) <= 2 and other in kept for other in phone_names):
            continue
        raise AssertionError(f"mất số {phone}")


def _read_scenario(seed: int) -> None:
    rng = random.Random(seed)
    phones = _Phones()
    mode = seed % 12
    frames: list[FrameObs] = []
    expect_rows: set[tuple[str, str]] = set()
    t = 1.0
    if mode == 0:
        for index in range(rng.randint(1, 5)):
            name = NAMES[index]
            phone = phones.take()
            user = _handle(seed * 10 + index)
            extras = [ContactHit(phones.take(), NAMES[(index + 3 + extra) % len(NAMES)]) for extra in range(rng.randint(0, 2))]
            frames.append(_list([ContactHit(phone, name, True), *extras], t))
            t += 0.2
            frames.append(_profile(name, user, t))
            t += 0.4
            expect_rows.add((phone, user))
    elif mode == 1:
        phone = phones.take()
        frames.append(_list([ContactHit(phone, "Đặng Thị Tâm", True)], t))
        frames.append(_profile("khactam", _handle(seed), t + 0.3))
    elif mode == 2:
        a, b = phones.take(), phones.take()
        user_a, user_b = _handle(seed), _handle(seed + 1)
        frames.append(_list([ContactHit(a, "Đặng Thị Tâm", True), ContactHit(b, "khactam")], t))
        frames.append(_profile("Đặng Thị Tâm", user_a, t + 0.2))
        frames.append(_profile("khactam", user_b, t + 0.5))
        expect_rows.add((a, user_a))
        expect_rows.add((b, user_b))
    elif mode == 3:
        phone = phones.take()
        frames.append(_list([ContactHit(phone, "Phương Anh", True)], t))
        frames.append(_profile("", _handle(seed), t + 0.2))
    elif mode == 4:
        phone = phones.take()
        frames.append(_list([ContactHit(phone, "Đặng Thị Tâm", True)], t))
        frames.append(_profile("khactam", _handle(seed), t + 0.2))
        frames.append(_profile("Đặng Thị Tâm", _handle(seed + 1), t + 0.5))
        expect_rows.add((phone, _handle(seed + 1)))
    elif mode == 5:
        phone = phones.take()
        user = _handle(seed)
        frames.append(_list([ContactHit(phone, "Đặng Thị Tâm", True)], t))
        frames.append(_profile("Dang Thi Tam", user, t + 0.3))
        expect_rows.add((phone, user))
    elif mode == 6:
        phone = phones.take()
        frames.append(_list([ContactHit(phone, "nvchien", True), ContactHit(phones.take(), "khactam")], t))
        frames.append(_profile("OCR lech", "@nvchien89", t + 0.3))
    elif mode == 7:
        a, b = phones.take(), phones.take()
        frames.append(_list([ContactHit(a, "Photo Name", False), ContactHit(b, "Photo Name", False)], t))
        frames.append(_profile("Photo Name", _handle(seed), t + 0.3))
    elif mode == 8:
        a, b = phones.take(), phones.take()
        user = _handle(seed)
        frames.append(_list([ContactHit(a, "Lý Mai Trang", True), ContactHit(b, "Lý Mai Trang")], t))
        frames.append(_profile("Lý Mai Trang", user, t + 0.25))
        expect_rows.add((a, user))
    elif mode == 9:
        phone = phones.take()
        user = _handle(seed)
        frames.append(FrameObs("tap", (ContactHit(phone, "Quang Le", True),), at=t))
        frames.append(_profile("Quang Le", user, t + 9.0))
        expect_rows.add((phone, user))
    elif mode == 10:
        base = phones.take()
        # Hai số cùng khung, lệch đúng 1 chữ số, cùng tên: giữ cả hai.
        chars = list(base)
        chars[-1] = str((int(chars[-1]) + 1) % 10)
        twin = "".join(chars)
        if normalize_phone(twin) and phone_distance(base, twin) == 1:
            frames.append(_list([ContactHit(base, "f_ Tranhungchef"), ContactHit(twin, "f_ Tranhungchef")], t))
        else:
            frames.append(_list([ContactHit(base, "f_ Tranhungchef")], t))
    else:
        for index in range(rng.randint(1, 4)):
            name = NAMES[index]
            other = NAMES[index + 5]
            phone = phones.take()
            user = _handle(seed * 10 + index)
            kind = rng.randrange(4)
            if kind == 0:
                frames.append(_list([ContactHit(phone, name, True)], t))
                t += 0.2
                frames.append(_profile(name, user, t))
                expect_rows.add((phone, user))
            elif kind == 1:
                frames.append(_list([ContactHit(phone, name, True)], t))
                t += 0.2
                frames.append(_profile(other, user, t))
            elif kind == 2:
                frames.append(_list([ContactHit(phone, name)], t))
            else:
                frames.append(_profile(name, user, t))
            t += 0.6
    table = build_table(frames)
    _assert_common(table, frames)
    got = {(row.phone, row.username) for row in table.rows}
    if mode != 11 and got != expect_rows:
        raise AssertionError(f"mode {mode} rows {got} != {expect_rows}")
    # Không gắn @ của khách khác vào số, kể cả khi hướng 2 chạy thêm.
    if mode == 4:
        again = pair_leftovers_by_time(table, frames)
        if any(row.username == _handle(seed) for row in again.rows):
            raise AssertionError(f"ghép nhầm hồ sơ khách khác {again.rows}")
    if mode == 4 and seed % 5 == 0:
        _assert_store_joins_same_video_name(frames, phone, _handle(seed + 1), _handle(seed))


def _assert_store_joins_same_video_name(
    frames: list[FrameObs],
    phone: str,
    good_user: str,
    bad_user: str,
) -> None:
    table = build_table(frames)
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "video.db"
        init_db(db)
        created = begin_upload(db, name="clip.mp4", size_bytes=8, sha256=f"s-{id(frames)}-{frames[0].at}", device="t")
        finish(db, int(created["id"]), table)
        rows = search_results(db, view="complete", limit=20)
        got = {(item["phone"], item["username"]) for item in rows}
        if (phone, good_user) not in got:
            raise AssertionError(f"cùng video không ghép tên giống {rows}")
        if any(item["username"] == bad_user for item in rows):
            raise AssertionError(f"ghép nhầm @ khách khác {rows}")


def _base_image():
    from PIL import Image

    rng = random.Random(1)
    image = Image.new("RGB", (320, 240))
    pixels = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(320 * 240)]
    image.putdata(pixels)
    return image


def _jpeg_base(image) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    image.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _jpeg_bytes(base: bytes, seed: int) -> bytes:
    """Mỗi kịch bản một file khác byte, vẫn là JPEG hợp lệ."""
    tag = int(seed).to_bytes(4, "big")
    return base + bytes([0xFF, 0xFE, 0x00, 0x06]) + tag


def _mp4_bytes(folder: Path) -> bytes:
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        return b""
    target = folder / "tiny.mp4"
    done = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    if done.returncode != 0 or not target.exists():
        return b""
    return target.read_bytes()


def _put_all(db: Path, upload_id: str, payload: bytes, chunk: int, order: list[int]) -> None:
    total = (len(payload) + chunk - 1) // chunk
    for index in order:
        start = index * chunk
        end = min(len(payload), start + chunk)
        put_chunk(db, upload_id, index, payload[start:end])
        if index >= total:
            raise AssertionError("index vượt file")


def _upload_scenario(seed: int, folder: Path, video_db: Path, uploads_db: Path, jpeg_base: bytes, mp4: bytes) -> None:
    rng = random.Random(20_000 + seed)
    mode = seed % 8
    use_mp4 = bool(mp4) and seed == 0
    payload = mp4 if use_mp4 else _jpeg_bytes(jpeg_base, seed)
    name = f"clip-{seed}.mp4" if use_mp4 else f"clip-{seed}.jpg"
    chunk = 16 * 1024
    video_dir = folder / "videos"
    body = init_upload(
        uploads_db,
        video_dir,
        name=name,
        size_bytes=len(payload),
        device="iPhone",
        client_key=f"phone|{name}|{len(payload)}|{seed}",
        chunk_size=chunk,
        sha256="",
        disk_limit_bytes=10**15,
        video_db_path=video_db,
    )
    upload_id = str(body["upload_id"])
    total = int(body["chunks_total"])
    try:
        if mode == 0:
            order = list(range(total))
            rng.shuffle(order)
            _put_all(uploads_db, upload_id, payload, int(body["chunk_size"]), order)
            done = complete_upload(
                uploads_db, video_dir, upload_id, disk_limit_bytes=10**15, video_db_path=video_db
            )
            if not done.get("ok") or done.get("duplicate"):
                raise AssertionError(done)
            saved = video_dir / f"{done['id']}{Path(name).suffix}"
            if saved.read_bytes() != payload:
                raise AssertionError("file ghép không khớp byte")
            again = complete_upload(
                uploads_db, video_dir, upload_id, disk_limit_bytes=10**15, video_db_path=video_db
            )
            if not again.get("already"):
                raise AssertionError(f"complete lần 2 không idempotent {again}")
        elif mode == 1:
            order = [index for index in range(total) for _ in range(2)]
            rng.shuffle(order)
            _put_all(uploads_db, upload_id, payload, int(body["chunk_size"]), order)
            status = get_upload(uploads_db, upload_id)
            if status is None or int(status["received_bytes"]) != len(payload):
                raise AssertionError(f"nhận trùng mảnh làm sai kích thước {status}")
            done = complete_upload(
                uploads_db, video_dir, upload_id, disk_limit_bytes=10**15, video_db_path=video_db
            )
            saved = video_dir / f"{done['id']}{Path(name).suffix}"
            if saved.read_bytes() != payload:
                raise AssertionError("file sau mảnh trùng không khớp")
        elif mode == 2:
            if total <= 1:
                _put_all(uploads_db, upload_id, payload, int(body["chunk_size"]), list(range(total)))
            else:
                _put_all(uploads_db, upload_id, payload, int(body["chunk_size"]), list(range(total - 1)))
                try:
                    complete_upload(
                        uploads_db, video_dir, upload_id, disk_limit_bytes=10**15, video_db_path=video_db
                    )
                except ValueError as exc:
                    if "Thiếu" not in str(exc):
                        raise
                else:
                    raise AssertionError("thiếu mảnh vẫn complete")
                status = get_upload(uploads_db, upload_id)
                if status is None or status["status"] != "receiving":
                    raise AssertionError(status)
        elif mode == 3:
            _put_all(uploads_db, upload_id, payload, int(body["chunk_size"]), [0])
            resumed = init_upload(
                uploads_db,
                video_dir,
                name=name,
                size_bytes=len(payload),
                device="iPhone",
                client_key=str(body["client_key"]),
                chunk_size=chunk,
                disk_limit_bytes=10**15,
                video_db_path=video_db,
            )
            if resumed["upload_id"] != upload_id or not resumed["resumed"]:
                raise AssertionError(f"không resume {resumed}")
            if 0 not in list(resumed["received"]):
                raise AssertionError(f"mất mảnh đã nhận {resumed}")
        elif mode == 4:
            other = init_upload(
                uploads_db,
                video_dir,
                name=name,
                size_bytes=len(payload),
                device="iPhone",
                client_key=str(body["client_key"]) + "|lane2",
                chunk_size=chunk,
                disk_limit_bytes=10**15,
                video_db_path=video_db,
            )
            if other["upload_id"] == upload_id:
                raise AssertionError("hướng 2 dùng lại phiên hướng 1")
            abort_session(uploads_db, str(other["upload_id"]))
        elif mode == 5:
            abort_session(uploads_db, upload_id)
            try:
                put_chunk(uploads_db, upload_id, 0, payload[: int(body["chunk_size"])])
            except LookupError:
                return
            raise AssertionError("put sau khi hủy vẫn nhận")
        elif mode == 6:
            try:
                put_chunk(uploads_db, upload_id, 0, b"x" * 3)
            except ValueError:
                status = get_upload(uploads_db, upload_id)
                if status and list(status["received"]):
                    raise AssertionError("mảnh sai độ dài vẫn được tính")
                return
            raise AssertionError("mảnh sai độ dài không bị từ chối")
        else:
            order = list(range(total))
            errors: list[BaseException] = []

            def _one(index: int) -> None:
                try:
                    start = index * int(body["chunk_size"])
                    end = min(len(payload), start + int(body["chunk_size"]))
                    put_chunk(uploads_db, upload_id, index, payload[start:end])
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=_one, args=(index,)) for index in order]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            if errors:
                raise errors[0]
            done = complete_upload(
                uploads_db, video_dir, upload_id, disk_limit_bytes=10**15, video_db_path=video_db
            )
            saved = video_dir / f"{done['id']}{Path(name).suffix}"
            if saved.read_bytes() != payload:
                raise AssertionError("ghép mảnh song song sai byte")
    finally:
        if mode != 5:
            abort_session(uploads_db, upload_id)


class Scenario99999Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for index, left in enumerate(NAMES):
            for right in NAMES[index + 1 :]:
                if same_person_name(left, right):
                    raise AssertionError(f"bộ tên không tách được {left} / {right}")

    def test_read_and_upload_scenarios(self) -> None:
        count = _scenario_count()
        read_n = count * 9 // 10
        upload_n = count - read_n
        failures: list[str] = []
        for seed in range(read_n):
            try:
                _read_scenario(seed)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"đọc seed={seed}: {exc}")
                if len(failures) >= 12:
                    break
        self.assertEqual(failures, [], "\n".join(failures))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video_dir = root / "videos"
            video_dir.mkdir()
            video_db = root / "video.db"
            uploads_db = root / "uploads.db"
            init_db(video_db)
            jpeg_base = _jpeg_base(_base_image())
            mp4 = _mp4_bytes(root)
            self.assertGreater(len(jpeg_base), 16 * 1024)
            for seed in range(upload_n):
                try:
                    _upload_scenario(seed, root, video_db, uploads_db, jpeg_base, mp4)
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"tải seed={seed}: {exc}")
                    if len(failures) >= 8:
                        break
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
