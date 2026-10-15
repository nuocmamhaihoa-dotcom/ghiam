"""Tách khung hình từ video, bỏ khung trùng, đọc song song nhiều khung.

Đọc hai bước. Bước quét chạy trên mọi khung: loại màn hình, vị trí dòng, số
điện thoại. Mỗi người hiện trên cả chục khung khi cuộn danh bạ, nên bước đọc
kỹ tên và @id chỉ chạy trên vài khung rõ nhất của mỗi người: dòng không bị
vòng tròn chạm màn hình đè, rải đều theo thời gian để mỗi lần đọc thấy một
ảnh hơi khác. Nhanh hơn nhiều so với đọc kỹ mọi khung, và bỏ phiếu giữa các
lần đọc vẫn sửa được lỗi của từng lần.

Luồng riêng chạy sau đó: đọc kỹ tên và @id trên mọi khung còn lại. Kết quả
nằm ở full_contacts và full_handle, không sửa tên hay @id của luồng chính.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from doc_video.frame import Contact, FrameRead, Layout, fill_side, read_layout, row_at, scan_frame
from doc_video.text import digits_of, find_handle, normalize_phone

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên chỉ bỏ khung gần như y hệt.
SAME_FRAME = 0.6
# Số khung đọc kỹ cho mỗi số điện thoại, và cho mỗi lần mở hồ sơ. Mỗi lần đọc
# kỹ @id đã gồm sáu lần đọc ở nhiều cỡ rồi bỏ phiếu, nên hồ sơ cần ít khung hơn.
READS_PER_PERSON = 3
READS_PER_PROFILE = 2


def _scan_file(path: str, at: float) -> Layout:
    try:
        with Image.open(path) as image:
            return scan_frame(image.convert("RGB"), at)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return Layout(at=at)


def _read_file(
    path: str, layout: Layout, rows: list[int] | None, primary: bool = True, with_side: bool = False
) -> FrameRead:
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            frame = read_layout(rgb, layout, rows) if primary else _unread(layout)
            if with_side and (layout.side_pairs or layout.side_handle_line is not None):
                try:
                    fill_side(rgb, layout, frame)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    # Hướng bổ sung lỗi thì giữ kết quả hướng cũ của khung này.
                    print(f"Bỏ đọc bốn dãy ký tự ở khung {Path(path).name}: {exc}", flush=True)
            return frame
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return _unread(layout)


def _fill_missing_profile_handles(layouts: list[Layout], done: dict[int, FrameRead]) -> None:
    """Lần đọc kỹ không ra @id thì dùng @id đã thấy lúc quét, để lần mở hồ sơ không mất.

    Chỉ điền khi cả lượt mở không có @id nào. Không lấy @id lúc quét đè lên @id đã đọc kỹ.
    """
    for run in _profile_runs(layouts):
        if any(done[index].handle for index in run if index in done):
            continue
        hints = [layouts[index].handle_hint for index in run if layouts[index].handle_hint]
        if not hints:
            continue
        hint = Counter(hints).most_common(1)[0][0]
        named = [index for index in run if index in done and done[index].profile_name]
        target = named[0] if named else next((index for index in run if index in done), None)
        if target is None:
            target = next(index for index in run if layouts[index].handle_hint)
            done[target] = FrameRead("profile", handle=hint, at=layouts[target].at)
            continue
        frame = done[target]
        frame.kind = "profile"
        frame.handle = hint


def _read_full_file(path: str, layout: Layout, rows: list[int] | None) -> FrameRead:
    """Đọc kỹ phần luồng chính chưa đọc. Lỗi một khung thì bỏ khung đó, không làm hỏng luồng chính."""
    try:
        with Image.open(path) as image:
            read = read_layout(image.convert("RGB"), layout, rows)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ luồng đọc toàn bộ ở khung {Path(path).name}: {exc}", flush=True)
        return FrameRead(at=layout.at)
    frame = FrameRead(at=layout.at)
    if layout.kind == "list":
        wanted = set(rows or [])
        frame.full_contacts = [
            Contact(contact.phone, contact.name, False)
            for index, contact in enumerate(read.contacts)
            if index in wanted and contact.name
        ]
        return frame
    if read.handle:
        frame.full_handle = read.handle
        frame.full_profile_name = read.profile_name
    return frame


def _keep_primary_names(frame: FrameRead) -> None:
    """Gộp lần đọc kỹ của luồng chính vào luồng toàn bộ để bỏ phiếu trên hết video.

    Không sửa contacts, tên hồ sơ hay @id của luồng chính.
    """
    seen = {contact.phone for contact in frame.full_contacts}
    for contact in frame.contacts:
        if contact.name and contact.phone not in seen:
            frame.full_contacts.append(Contact(contact.phone, contact.name, False))
            seen.add(contact.phone)
    if frame.handle.startswith("@") and not frame.full_handle:
        frame.full_handle = frame.handle
        frame.full_profile_name = frame.profile_name


def _unread(layout: Layout) -> FrameRead:
    """Khung không đọc kỹ: danh bạ vẫn có số điện thoại, tên để trống; hồ sơ không có @id."""
    if layout.kind == "list":
        return FrameRead(
            "list",
            [Contact(row.phone, "", row.in_order) for row in layout.rows],
            at=layout.at,
            tapped=layout.tapped,
        )
    return FrameRead(layout.kind, at=layout.at)


def extract_frames(video: Path, folder: Path, fps: float) -> list[tuple[Path, float]]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Chưa cài ffmpeg. Ubuntu: sudo apt install ffmpeg")
    pattern = folder / "f_%06d.jpg"
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps}", "-q:v", "2", str(pattern)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    files = sorted(folder.glob("f_*.jpg"))
    if done.returncode != 0 and not files:
        raise RuntimeError(f"Không mở được video {video.name}: {done.stderr.strip()[:300]}")
    return [(path, index / fps) for index, path in enumerate(files)]


def _thumb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L").resize((90, 195), Image.Resampling.BOX), dtype=np.float32)


def drop_repeats(frames: list[tuple[Path, float]]) -> list[tuple[Path, float]]:
    kept: list[tuple[Path, float]] = []
    last: np.ndarray | None = None
    for path, at in frames:
        thumb = _thumb(path)
        if last is not None and float(np.abs(thumb - last).mean()) < SAME_FRAME:
            continue
        kept.append((path, at))
        last = thumb
    return kept


def _spread(items: list, count: int) -> list:
    """count phần tử rải đều trong danh sách, giữ thứ tự."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1) if count > 1 else 0
    return [items[round(index * step)] for index in range(count)]


def _profile_runs(layouts: list[Layout]) -> list[list[int]]:
    """Các khung của từng lần mở hồ sơ. Hết lượt khi quay lại danh bạ, hoặc @id thấy lúc quét khác hẳn."""
    runs: list[list[int]] = []
    run: list[int] = []
    for index, layout in enumerate(layouts):
        if layout.kind == "list":
            if run:
                runs.append(run)
            run = []
            continue
        if layout.kind != "profile":
            continue
        if run:
            last = layouts[run[-1]].handle_hint
            hint = layout.handle_hint
            if last and hint and SequenceMatcher(None, last, hint, autojunk=False).ratio() < 0.6:
                runs.append(run)
                run = []
        run.append(index)
    if run:
        runs.append(run)
    return runs


def _same_scroll(left: Layout, right: Layout) -> bool:
    """Hai khung danh bạ ở cùng vị trí cuộn: ít nhất hai số nằm đúng cùng độ cao."""
    pitch = left.row_pitch or right.row_pitch
    if pitch <= 0:
        return False
    tops = {row.phone: row.top for row in left.rows if row.in_order}
    same = [
        row
        for row in right.rows
        if row.in_order and row.phone in tops and abs(tops[row.phone] - row.top) <= 0.15 * pitch
    ]
    return len(same) >= 2


def resolve_taps(layouts: list[Layout], reach: int = 4) -> None:
    """Vòng tròn chạm đè lên chữ nên dòng bị bấm biến mất khỏi chính khung đó.

    Tìm số ở đúng độ cao vòng tròn trên khung danh bạ gần nhất có cùng vị trí cuộn.
    """
    for index, layout in enumerate(layouts):
        if layout.kind != "list" or layout.tapped or layout.tap_y < 0:
            continue
        neighbours = sorted(
            (other for other in range(max(0, index - reach), min(len(layouts), index + reach + 1)) if other != index),
            key=lambda other: abs(other - index),
        )
        for other in neighbours:
            candidate = layouts[other]
            if candidate.kind != "list" or not _same_scroll(layout, candidate):
                continue
            phone = row_at(candidate.rows, layout.tap_y, layout.row_pitch or candidate.row_pitch)
            if phone:
                layout.tapped = phone
                break


def _side_key(phone_line) -> str:
    return normalize_phone(phone_line.text) or digits_of(phone_line.text) or f"y{phone_line.y0}"


def _row_is_planned(layout: Layout, plan_rows: list[int] | None, phone_line) -> bool:
    if not plan_rows:
        return False
    rough = normalize_phone(phone_line.text)
    for row_index in plan_rows:
        if row_index >= len(layout.rows):
            continue
        row = layout.rows[row_index]
        if row.top == phone_line.y0 or (rough and row.phone == rough):
            return True
    return False


def side_frames(layouts: list[Layout], plan: dict[int, list[int] | None]) -> set[int]:
    """Vài khung để đọc kỹ bốn dãy ký tự. Không thêm khung vào bước đọc tên của hướng cũ."""
    chosen: set[int] = set()
    groups: dict[str, list[int]] = {}
    lines: dict[str, list] = {}
    for index, layout in enumerate(layouts):
        for phone_line, _name_line in layout.side_pairs:
            key = _side_key(phone_line)
            groups.setdefault(key, []).append(index)
            lines.setdefault(key, []).append(phone_line)
    for key, indexes in groups.items():
        sample = lines[key]
        named = [
            index
            for index, phone_line in zip(indexes, sample)
            if _row_is_planned(layouts[index], plan.get(index), phone_line)
        ]
        preferred = named or [index for index in indexes if index in plan] or indexes
        chosen.update(_spread(preferred, READS_PER_PERSON))
    for run in _profile_runs(layouts):
        if not any(layouts[index].side_handle_line is not None for index in run):
            continue
        preferred = [index for index in run if index in plan and layouts[index].side_handle_line is not None]
        pool = preferred or [index for index in run if layouts[index].side_handle_line is not None]
        chosen.update(_spread(pool, READS_PER_PROFILE))
    loose: dict[str, list[int]] = {}
    for index, layout in enumerate(layouts):
        if layout.kind == "profile" or layout.side_handle_line is None:
            continue
        hint = find_handle(layout.side_handle_line.text)
        if hint:
            loose.setdefault(hint, []).append(index)
    for indexes in loose.values():
        chosen.update(_spread(indexes, READS_PER_PROFILE))
    return chosen


def plan_full_reads(
    layouts: list[Layout], plan: dict[int, list[int] | None]
) -> dict[int, list[int] | None]:
    """Mọi dòng còn đọc được mà luồng chính chưa đọc kỹ.

    Danh bạ: các dòng tên chưa nằm trong plan. Hồ sơ: các khung của lượt mở
    chưa được đọc kỹ. Ưu tiên khung không bị vòng tròn chạm đè. Giá trị None
    là đọc cả hồ sơ.
    """
    full: dict[int, list[int] | None] = {}
    by_phone: dict[str, list[tuple[int, int, bool]]] = {}
    for frame_index, layout in enumerate(layouts):
        if layout.kind != "list":
            continue
        planned = plan.get(frame_index)
        planned_rows = set(planned) if planned else set()
        for row_index, row in enumerate(layout.rows):
            if not row.readable or row_index in planned_rows:
                continue
            by_phone.setdefault(row.phone, []).append((frame_index, row_index, row.clear))
    for seen in by_phone.values():
        clear = [item for item in seen if item[2]]
        for frame_index, row_index, _ in clear or seen:
            rows = full.setdefault(frame_index, [])
            if rows is not None and row_index not in rows:
                rows.append(row_index)
    for run in _profile_runs(layouts):
        already = {index for index in run if index in plan and plan[index] is None}
        rest = [
            index
            for index in run
            if index not in already and layouts[index].handle_line is not None
        ]
        clear = [index for index in rest if layouts[index].clear and layouts[index].name_readable]
        for index in clear or rest:
            full[index] = None
    for rows in full.values():
        if rows is not None:
            rows.sort()
    return full


def plan_reads(
    layouts: list[Layout], per_person: int = READS_PER_PERSON, per_profile: int = READS_PER_PROFILE
) -> dict[int, list[int] | None]:
    """Khung nào cần đọc kỹ. Giá trị là các dòng danh bạ cần đọc tên, hoặc None với khung hồ sơ."""
    by_phone: dict[str, list[tuple[int, int, bool]]] = {}
    for frame_index, layout in enumerate(layouts):
        if layout.kind != "list":
            continue
        for row_index, row in enumerate(layout.rows):
            by_phone.setdefault(row.phone, []).append((frame_index, row_index, row.clear))
    plan: dict[int, list[int] | None] = {}
    for seen in by_phone.values():
        clear = [item for item in seen if item[2]]
        for frame_index, row_index, _ in _spread(clear or seen, per_person):
            rows = plan.setdefault(frame_index, [])
            if rows is not None and row_index not in rows:
                rows.append(row_index)
    for run in _profile_runs(layouts):
        # Ưu tiên khung mà lần quét đã thấy @id (hồ sơ đã tải xong, không còn trượt vào), rồi tới
        # khung không bị vòng tròn chạm đè. Khung đầu thường là lúc hồ sơ đang trượt vào màn hình.
        loaded = [index for index in run if layouts[index].handle_hint]
        neat = [index for index in loaded if layouts[index].clear]
        pool = neat if len(neat) >= per_profile else loaded if len(loaded) >= per_profile else run
        if pool is run and len(pool) > per_profile:
            pool = pool[1:]
        # Lượt dài bất thường có thể là hai hồ sơ liền nhau mà lần quét không tách được.
        count = max(per_profile, len(pool) // 4)
        for index in _spread(pool, count):
            plan[index] = None
    for rows in plan.values():
        if rows is not None:
            rows.sort()
    return plan


def read_inputs(
    paths: list[Path],
    fps: float = 2.0,
    workers: int | None = None,
    submit: Callable[..., Future] | None = None,
    work_dir: Path | None = None,
    task_timeout: float | None = None,
) -> list[FrameRead]:
    """Đọc ảnh và video theo đúng thứ tự đưa vào. Thứ tự này dùng để biết hồ sơ mở sau danh bạ nào.

    submit là executor.submit của một nhóm tiến trình có sẵn; không có thì tự mở nhóm riêng.
    task_timeout: chờ một khung lâu hơn chừng này giây thì coi nhóm tiến trình đã hỏng (BrokenExecutor).
    """
    jobs: list[tuple[str, float]] = []
    temps: list[tempfile.TemporaryDirectory[str]] = []
    pool: ProcessPoolExecutor | None = None
    try:
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                jobs.append((str(path), -1.0))
            elif suffix in VIDEO_SUFFIXES:
                folder = tempfile.TemporaryDirectory(prefix="docvideo-frames-", dir=work_dir)
                temps.append(folder)
                frames = drop_repeats(extract_frames(path, Path(folder.name), fps))
                jobs.extend((str(frame), at) for frame, at in frames)
            else:
                raise RuntimeError(f"Không đọc được {path.name}: cần ảnh hoặc video.")
        if submit is None:
            workers = workers or max(1, (os.cpu_count() or 2) - 1)
            if workers > 1 and len(jobs) > 1:
                pool = ProcessPoolExecutor(max_workers=workers)
                submit = pool.submit
        layouts = _run(submit, [(_scan_file, path, at) for path, at in jobs], task_timeout)
        resolve_taps(layouts)
        plan = plan_reads(layouts)
        side_at = side_frames(layouts, plan)
        order = sorted(set(plan) | side_at)
        reads = _run(
            submit,
            [
                (_read_file, jobs[index][0], layouts[index], plan.get(index), index in plan, index in side_at)
                for index in order
            ],
            task_timeout,
        )
        done = dict(zip(order, reads))
        _fill_missing_profile_handles(layouts, done)
        full_plan = plan_full_reads(layouts, plan)
        if full_plan:
            full_order = sorted(full_plan)
            full_reads = _run(
                submit,
                [(_read_full_file, jobs[index][0], layouts[index], full_plan[index]) for index in full_order],
                task_timeout,
            )
            for index, extra in zip(full_order, full_reads):
                frame = done.get(index)
                if frame is None:
                    frame = _unread(layouts[index])
                    done[index] = frame
                frame.full_contacts.extend(extra.full_contacts)
                if extra.full_handle:
                    frame.full_handle = extra.full_handle
                    frame.full_profile_name = extra.full_profile_name
        for index, layout in enumerate(layouts):
            frame = done.get(index)
            if frame is None:
                frame = _unread(layout)
                done[index] = frame
            _keep_primary_names(frame)
        return [done[index] for index in range(len(layouts))]
    finally:
        if pool is not None:
            pool.shutdown(cancel_futures=True)
        for folder in temps:
            folder.cleanup()


def _run(submit: Callable[..., Future] | None, calls: list[tuple], timeout: float | None = None) -> list:
    if submit is None:
        return [call[0](*call[1:]) for call in calls]
    futures = [submit(*call) for call in calls]
    try:
        return [future.result(timeout=timeout) for future in futures]
    except FutureTimeout as exc:
        for future in futures:
            future.cancel()
        raise BrokenExecutor(f"Một khung không đọc xong sau {timeout:.0f} giây.") from exc
    except BaseException:
        for future in futures:
            future.cancel()
        raise
