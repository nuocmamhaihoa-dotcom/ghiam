"""Hai hướng đọc thêm, chỉ chạy khi hướng chính còn hồ sơ chưa ghép.

Hướng 1 là pipeline hiện tại (không đụng hàng đã đủ Số + Tên + @).
Hướng 2: ghép số và @ còn sót nếu trong 4 giây chỉ có đúng một cặp.
Hướng 3: đọc riêng vùng trên khung (tên / @) quanh chỗ còn thiếu, rồi ghép lại theo hướng 2.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from control_plane.screen_table import (
    FrameObs,
    Review,
    Row,
    Table,
    Unopened,
    clean_name,
    clean_username,
    is_profile_handle,
    is_profile_name,
    joined_name,
    normalize_phone,
    phone_in_text,
    same_person_name,
)

PAIR_WINDOW_SEC = 4.0
MAX_HEADER_READS = 240


def gaps_remain(table: Table) -> bool:
    """Còn số thiếu @, hoặc hồ sơ chưa gắn số."""
    if table.unopened:
        return True
    for item in table.review:
        name = item.contact_name or item.profile_name
        if item.phone and name and item.username:
            continue
        if item.phone or item.username:
            return True
    return False


def _phone_times(frames: list[FrameObs]) -> dict[str, float]:
    seen: dict[str, float] = {}
    opened: dict[str, float] = {}
    for frame in frames:
        if frame.at < 0:
            continue
        for hit in frame.contacts:
            phone = normalize_phone(hit.phone)
            if not phone:
                continue
            if hit.selected or frame.kind == "tap":
                opened[phone] = frame.at
            elif phone not in seen:
                seen[phone] = frame.at
    seen.update(opened)
    return seen


def _user_times(frames: list[FrameObs]) -> dict[str, float]:
    found: dict[str, float] = {}
    for frame in frames:
        if frame.at < 0 or frame.kind != "profile":
            continue
        user = clean_username(frame.profile_username)
        if user and user not in found:
            found[user] = frame.at
    return found


def _other_customer_between(
    frames: list[FrameObs],
    start: float,
    end: float,
    person_name: str,
    username: str,
) -> bool:
    """Có hồ sơ của khách khác nằm giữa số và @ thì không phải cặp liền."""
    if start > end:
        start, end = end, start
    for frame in frames:
        if frame.kind != "profile" or frame.at < 0 or not (start < frame.at < end):
            continue
        handle = clean_username(frame.profile_username)
        if handle and handle == username:
            continue
        name = clean_name(frame.profile_name)
        if name and same_person_name(name, person_name):
            continue
        return True
    return False


def _unique_time_pairs(
    phones: list[tuple[str, str, float]],
    users: list[tuple[str, str, float]],
    frames: list[FrameObs] | None = None,
    window: float = PAIR_WINDOW_SEC,
) -> list[tuple[str, str]]:
    """Chỉ ghép khi cùng tên, đúng một @ trong cửa sổ, không có khách khác chen giữa."""
    phone_hits: dict[str, list[str]] = {}
    user_hits: dict[str, list[str]] = {}
    for phone, phone_name, phone_at in phones:
        for username, user_name, user_at in users:
            if abs(phone_at - user_at) > window:
                continue
            if not same_person_name(phone_name, user_name):
                continue
            if frames and _other_customer_between(frames, phone_at, user_at, phone_name, username):
                continue
            phone_hits.setdefault(phone, []).append(username)
            user_hits.setdefault(username, []).append(phone)
    pairs: list[tuple[str, str]] = []
    for phone, usernames in phone_hits.items():
        uniq_users = list(dict.fromkeys(usernames))
        if len(uniq_users) != 1:
            continue
        username = uniq_users[0]
        if len(list(dict.fromkeys(user_hits.get(username, [])))) != 1:
            continue
        pairs.append((phone, username))
    return pairs


def pair_leftovers_by_time(table: Table, frames: list[FrameObs]) -> Table:
    """Hướng 2. Hàng đã đủ 3 ô giữ nguyên. Không có mốc thời gian thì không đụng."""
    if not gaps_remain(table) or not any(frame.at >= 0 for frame in frames):
        return table
    phone_at = _phone_times(frames)
    user_at = _user_times(frames)
    complete_phones = {row.phone for row in table.rows if row.phone and row.name and row.username}
    complete_users = {clean_username(row.username) for row in table.rows if row.username}

    phones: list[tuple[str, str, float]] = []
    users: list[tuple[str, str, float]] = []
    phone_items: dict[str, Unopened | Review] = {}
    user_items: dict[str, Review] = {}
    kept_unopened: list[Unopened] = []
    kept_review: list[Review] = []

    for item in table.unopened:
        at = phone_at.get(item.phone, -1.0)
        if item.phone in complete_phones or at < 0:
            kept_unopened.append(item)
            continue
        phones.append((item.phone, item.name, at))
        phone_items[item.phone] = item
    for item in table.review:
        name = item.contact_name or item.profile_name
        if item.phone and name and item.username:
            kept_review.append(item)
            continue
        if item.phone and not item.username and item.phone not in complete_phones:
            at = phone_at.get(item.phone, -1.0)
            if at < 0 or item.phone in phone_items:
                kept_review.append(item)
                continue
            phones.append((item.phone, name, at))
            phone_items[item.phone] = item
            continue
        user = clean_username(item.username)
        if user and not item.phone and user not in complete_users:
            at = user_at.get(user, -1.0)
            if at < 0 or user in user_items:
                kept_review.append(item)
                continue
            users.append((user, item.profile_name or item.contact_name, at))
            user_items[user] = item
            continue
        kept_review.append(item)

    pairs = _unique_time_pairs(phones, users, frames)
    if not pairs:
        return table
    paired_phones = {phone for phone, _user in pairs}
    paired_users = {user for _phone, user in pairs}
    rows = list(table.rows)
    phone_name = {phone: name for phone, name, _at in phones}
    user_name = {user: name for user, name, _at in users}
    for phone, username in pairs:
        contact = phone_name.get(phone, "")
        profile = user_name.get(username, "")
        display = joined_name(contact, profile) if contact and profile else (contact or profile)
        rows.append(Row(phone, display or contact, username))
    for phone, _name, _at in phones:
        if phone in paired_phones:
            continue
        item = phone_items[phone]
        if isinstance(item, Unopened):
            kept_unopened.append(item)
        else:
            kept_review.append(item)
    for username, _name, _at in users:
        if username in paired_users:
            continue
        kept_review.append(user_items[username])
    return Table(rows=rows, review=kept_review, unopened=kept_unopened)


def maybe_handle(line: str) -> str:
    """@ đọc được, hoặc một token có số/dấu chấm giống username.

    Số điện thoại không phải username. Không biến 0900000099 thành @0900000099.
    """
    found = clean_username(line)
    if found:
        return found
    if phone_in_text(line):
        return ""
    parts = line.split()
    if not parts or "@" in line:
        return ""
    token = parts[-1]
    if phone_in_text(token) or not any(ch.isdigit() or ch in "._" for ch in token):
        return ""
    return clean_username("@" + token)


def parse_header_text(text: str) -> tuple[str, str, str]:
    """Tách số, tên, @ từ chữ đọc ở vùng trên khung.

    @ chỉ nhận khi dòng ngay phía trên là tên khách. Ngày, nút follow,
    danh bạ và @ toàn số không thành hồ sơ.
    """
    phone = ""
    username = ""
    name = ""
    lines = [" ".join(raw.split()) for raw in text.splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        found = phone_in_text(line)
        if found and not phone:
            phone = found
        if username or index == 0:
            continue
        handle = maybe_handle(line)
        if not is_profile_handle(handle):
            continue
        above = lines[index - 1]
        shown = clean_name(above)
        if not is_profile_name(shown) or "@" in above or maybe_handle(above):
            continue
        username = handle
        name = shown
    return phone, name, username


def _ocr_header_text(path: Path) -> str:
    """Đọc lại chỉ các dòng trong vùng @. Không gửi nửa trên khung vào một lần OCR."""
    if shutil.which("tesseract") is None:
        return ""
    try:
        from PIL import Image, ImageOps

        from control_plane.screen_read import read_locked_header_text
    except ImportError:
        return ""
    try:
        with Image.open(path) as image:
            rgb = ImageOps.exif_transpose(image).convert("RGB")
            if rgb.width > 1080:
                ratio = 1080 / rgb.width
                rgb = rgb.resize((1080, max(1, int(rgb.height * ratio))))
            return read_locked_header_text(rgb)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return ""


def read_headers_for_gaps(
    table: Table,
    observations: list[tuple[Path, FrameObs]],
    *,
    reader=None,
) -> Table:
    """Hướng 3. Chỉ đọc vùng trên quanh số còn thiếu @. Hàng đủ 3 ô không đọc lại."""
    if not gaps_remain(table):
        return table
    frames = [obs for _path, obs in observations]
    if not any(obs.at >= 0 for obs in frames):
        return table
    phone_at = _phone_times(frames)
    # Chỉ đọc lại quanh số còn thiếu @. @ không có số không phải khách, khỏi chiếm lượt đọc.
    anchors: list[float] = []
    for item in table.unopened:
        at = phone_at.get(item.phone, -1.0)
        if at >= 0:
            anchors.append(at)
    for item in table.review:
        if not item.phone or item.username:
            continue
        at = phone_at.get(item.phone, -1.0)
        if at >= 0:
            anchors.append(at)
    if not anchors:
        return table

    ranked: list[tuple[float, Path, FrameObs]] = []
    for path, obs in observations:
        if obs.at < 0 or not path:
            continue
        distance = min(abs(obs.at - anchor) for anchor in anchors)
        if distance > PAIR_WINDOW_SEC:
            continue
        ranked.append((distance, Path(path), obs))
    ranked.sort(key=lambda item: item[0])
    chosen: list[tuple[Path, FrameObs]] = []
    seen_paths: set[str] = set()
    for _distance, path, obs in ranked:
        key = str(path)
        if key in seen_paths or not path.exists():
            continue
        seen_paths.add(key)
        chosen.append((path, obs))
        if len(chosen) >= MAX_HEADER_READS:
            break
    if not chosen:
        return table

    read = reader or _ocr_header_text
    complete_phones = {row.phone for row in table.rows if row.phone}
    complete_users = {clean_username(row.username) for row in table.rows if row.username}
    known_phones = {item.phone for item in table.unopened} | {item.phone for item in table.review if item.phone}
    known_users = {clean_username(item.username) for item in table.review if item.username}
    extra_frames: list[FrameObs] = list(frames)
    temp_unopened = list(table.unopened)
    temp_review = list(table.review)
    added_phones: set[str] = set()
    added_users: set[str] = set()
    from control_plane.screen_table import ContactHit

    for path, obs in chosen:
        phone, name, username = parse_header_text(read(path))
        if username and username not in complete_users and username not in known_users and username not in added_users:
            extra_frames.append(FrameObs("profile", (), name, username, at=obs.at))
            temp_review.append(Review("", "", name, username, "đọc vùng trên"))
            added_users.add(username)
        if (
            phone
            and phone not in complete_phones
            and phone not in known_phones
            and phone not in added_phones
        ):
            extra_frames.append(FrameObs("list", (ContactHit(phone, name or phone, True),), at=obs.at))
            temp_unopened.append(Unopened(phone, name or phone))
            added_phones.add(phone)
    if not added_phones and not added_users:
        return table
    result = pair_leftovers_by_time(Table(rows=list(table.rows), review=temp_review, unopened=temp_unopened), extra_frames)
    # Số/@ chỉ đọc thêm để ghép phần đã có. Cặp do chính lượt này bịa ra thì bỏ.
    return Table(
        rows=[
            row
            for row in result.rows
            if not (row.phone in added_phones and clean_username(row.username) in added_users)
        ],
        unopened=[item for item in result.unopened if item.phone not in added_phones],
        review=[
            item
            for item in result.review
            if not (not item.phone and clean_username(item.username) in added_users)
        ],
    )


def apply_read_fallbacks(
    table: Table,
    observations: list[tuple[Path, FrameObs]],
    on_progress=None,
) -> Table:
    """Hướng 1 đã chạy xong. Chỉ khi còn sót thì lần lượt hướng 2 rồi hướng 3."""
    if not gaps_remain(table):
        return table
    frames = [obs for _path, obs in observations]
    if on_progress is not None:
        on_progress("Hướng đọc 2: ghép theo thời gian 4 giây các hồ sơ còn sót")
    table = pair_leftovers_by_time(table, frames)
    if not gaps_remain(table):
        return table
    if on_progress is not None:
        on_progress("Hướng đọc 3: đọc riêng từng dòng tên và @ của hồ sơ chưa ghép")
    table = read_headers_for_gaps(table, observations)
    if not gaps_remain(table):
        return table
    from control_plane.vision_gap import apply_vision_for_gaps, vision_enabled

    if not vision_enabled():
        return table
    if on_progress is not None:
        on_progress("Hướng đọc 4: nhìn ảnh chỉ vùng @ của số còn thiếu")
    return apply_vision_for_gaps(table, observations)
