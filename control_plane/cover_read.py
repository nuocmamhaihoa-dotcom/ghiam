"""Ghép phần chữ còn thấy khi chấm xám che một đoạn số, tên hoặc @.

Chấm đứng yên, dòng trượt. Mỗi khung chỉ tin chữ nằm ngoài chấm.
Hai khung ghép được khi chữ chồng lên nhau khớp và ra đúng một số đủ 10 chữ số,
hoặc đúng một @ / một tên không phải bịa thêm ký tự. Không khớp thì bỏ mảnh đó.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from control_plane.screen_table import (
    COVER_REASON,
    ContactHit,
    FrameObs,
    Review,
    clean_name,
    clean_username,
    is_profile_handle,
    is_profile_name,
    is_skipped,
    normalize_phone,
    same_person_name,
)


def partial_digits(raw: str) -> str:
    """Chữ số nhìn thấy. O đứng sau một chữ số thì là 0. Không đòi đủ 10 số."""
    digits: list[str] = []
    saw_digit = False
    for ch in str(raw or ""):
        if ch.isdigit():
            digits.append(ch)
            saw_digit = True
            continue
        if ch in "Oo" and saw_digit:
            digits.append("0")
            continue
        if ch.isspace() or ch in "-.()":
            continue
        if digits:
            break
    return "".join(digits)


def mask_digits(left: str, right: str) -> list[str] | None:
    """10 ô. Trái là đầu số, phải là cuối số. Ô trống là chỗ chấm che. Lệch chữ số thì bỏ."""
    left_digits = partial_digits(left)
    right_digits = partial_digits(right)
    if len(left_digits) > 10 or len(right_digits) > 10:
        return None
    if not left_digits and not right_digits:
        return None
    slots = [""] * 10
    for index, ch in enumerate(left_digits):
        slots[index] = ch
    for offset, ch in enumerate(reversed(right_digits)):
        pos = 9 - offset
        if slots[pos] and slots[pos] != ch:
            return None
        slots[pos] = ch
    return slots


def fill_hole(slots: list[str], middle: str) -> list[str]:
    """Chữ số nằm giữa hai chấm chỉ được điền khi vừa khít lỗ, không dài hơn cũng không ngắn hơn."""
    mid = partial_digits(middle)
    if not mid:
        return slots
    hole = [index for index, ch in enumerate(slots) if not ch]
    if len(mid) != len(hole):
        return slots
    filled = list(slots)
    for index, ch in zip(hole, mid):
        filled[index] = ch
    return filled


def phone_from_slots(slots: list[str] | None) -> str:
    if not slots or any(not ch for ch in slots):
        return ""
    return normalize_phone("".join(slots))


def overlap_join(left: str, right: str, min_overlap: int = 4) -> str:
    """Nối hai mảnh khi một mảnh chứa mảnh kia, hoặc phần cuối trùng phần đầu. Không chèn ký tự."""
    left = " ".join(str(left or "").split())
    right = " ".join(str(right or "").split())
    if not left or not right:
        return left or right
    if right.casefold().startswith(left.casefold()):
        return right
    if left.casefold().startswith(right.casefold()):
        return left
    max_n = min(len(left), len(right))
    for size in range(max_n, min_overlap - 1, -1):
        if left[-size:].casefold() == right[:size].casefold():
            return left + right[size:]
    return ""


def assemble_pieces(pieces: list[str], min_overlap: int = 4) -> str:
    """Ghép nhiều mảnh thành một chuỗi. Hai cách ghép ra hai chuỗi khác nhau thì bỏ."""
    parts = []
    seen: set[str] = set()
    for piece in pieces:
        text = " ".join(str(piece or "").split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        parts.append(text)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    guard = 0
    while len(parts) > 1 and guard < 16:
        guard += 1
        found = ""
        pair: tuple[int, int] | None = None
        for i, left in enumerate(parts):
            for j, right in enumerate(parts):
                if i == j:
                    continue
                got = overlap_join(left, right, min_overlap)
                if not got:
                    continue
                if found and got.casefold() != found.casefold():
                    return ""
                found = got
                pair = (i, j)
        if not found or pair is None:
            return ""
        rest = [part for index, part in enumerate(parts) if index not in pair]
        kept = [part for part in rest if part.casefold() not in found.casefold()]
        parts = [found, *kept]
    if len(parts) == 1:
        return parts[0]
    return ""


def narrow_join(left: str, right: str, *, narrow: bool, sep: str = "") -> str:
    """Chấm chỉ kẹp giữa hai mảnh, không nuốt chữ: nối lại. Chấm nuốt một đoạn thì không nối một khung."""
    left = " ".join(str(left or "").split())
    right = " ".join(str(right or "").split())
    if left and not right:
        return left
    if right and not left:
        return right
    if narrow and left and right:
        return f"{left}{sep}{right}".strip()
    return ""


@dataclass
class _Frag:
    frame_i: int
    hit_i: int
    left: str
    right: str
    name: str
    at: float
    selected: bool
    slots: list[str]
    shot: bytes = b""


@dataclass
class _Done:
    frame_i: int
    phone: str
    name: str
    selected: bool


def _known(slots: list[str]) -> int:
    return sum(1 for ch in slots if ch)


def _pattern(slots: list[str]) -> str:
    return "".join(ch or "·" for ch in slots)


def _agrees(phone: str, slots: list[str]) -> bool:
    return len(phone) == len(slots) and all(not slot or slot == digit for slot, digit in zip(slots, phone))


def _compatible(left: _Frag, right: _Frag) -> bool:
    if left.frame_i == right.frame_i:
        return False
    named = bool(left.name and right.name)
    if named and not same_person_name(left.name, right.name):
        return False
    shared = 0
    for a, b in zip(left.slots, right.slots):
        if a and b and a != b:
            return False
        if a and b:
            shared += 1
    pure_sides = (left.left and not left.right and right.right and not right.left) or (
        right.left and not right.right and left.right and not left.left
    )
    if named:
        if shared < 2 and not pure_sides:
            return False
    elif shared < 4:
        return False
    if left.at < 0 or right.at < 0:
        # Ảnh không có thời điểm. Không ghép hai mảnh chỉ vì cùng tên.
        return named and shared >= 4
    limit = 8.0 if named else 3.0
    return abs(left.at - right.at) <= limit


def _merge_slots(group: list[_Frag]) -> list[str] | None:
    slots = [""] * 10
    for frag in group:
        for index, ch in enumerate(frag.slots):
            if not ch:
                continue
            if slots[index] and slots[index] != ch:
                return None
            slots[index] = ch
    return slots


def stitch_phone_fragments(
    frags: list[_Frag],
    clean: list[tuple[str, str]],
) -> tuple[list[_Done], list[Review]]:
    """Ghép mảnh số. Số đã đọc đủ ở khung không bị che thì giữ số đó, không ghép đè."""
    groups: list[list[_Frag]] = []
    for frag in frags:
        fits = [
            group
            for group in groups
            if all(_compatible(frag, other) for other in group) and _merge_slots(group + [frag]) is not None
        ]
        if len(fits) == 1:
            fits[0].append(frag)
        else:
            # Không vừa nhóm nào, hoặc vừa hai nhóm: để riêng, không dính hai khách.
            groups.append([frag])

    done: list[_Done] = []
    notes: list[Review] = []
    clean_phones = {phone for phone, _name in clean}
    for group in groups:
        slots = _merge_slots(group)
        if slots is None:
            continue
        names = [frag.name for frag in group if frag.name]
        name = names[0] if names else ""
        phone = phone_from_slots(slots)
        explained = any(_agrees(item, slots) and (not name or not other or same_person_name(name, other)) for item, other in clean)
        if phone and phone not in clean_phones and not explained:
            host = min(group, key=lambda frag: (frag.at if frag.at >= 0 else 10**9, frag.frame_i))
            done.append(
                _Done(
                    host.frame_i,
                    phone,
                    name,
                    any(frag.selected for frag in group),
                )
            )
            continue
        if phone or explained or _known(slots) < 6:
            continue
        label = name or _pattern(slots)
        shot = next((frag.shot for frag in group if frag.shot), b"")
        notes.append(Review("", label, "", "", COVER_REASON, phone_mask=_pattern(slots), phone_shot=shot))
    return done, notes


def _fits(whole: str, *parts: str) -> bool:
    if not whole:
        return False
    folded = whole.casefold()
    for part in parts:
        text = " ".join(str(part or "").split())
        if not text:
            continue
        if text.casefold() not in folded and text.casefold().lstrip("@") not in folded.lstrip("@"):
            return False
    return True


def _stitch_profile_run(run: list[tuple[int, FrameObs]], frames: list[FrameObs]) -> None:
    if not any(frame.handle_left or frame.handle_right or frame.name_left or frame.name_right for _, frame in run):
        return
    handle_parts: list[str] = []
    name_parts: list[str] = []
    full_handle = ""
    full_name = ""
    for _, frame in run:
        if is_profile_handle(frame.profile_username) and not full_handle:
            full_handle = frame.profile_username
        if is_profile_name(frame.profile_name) and not full_name:
            full_name = frame.profile_name
        if frame.handle_left:
            handle_parts.append(frame.handle_left)
        if frame.handle_right:
            handle_parts.append(frame.handle_right)
        if frame.name_left:
            name_parts.append(frame.name_left)
        if frame.name_right:
            name_parts.append(frame.name_right)
    handle = full_handle
    if not handle and handle_parts:
        built = assemble_pieces(handle_parts)
        candidate = clean_username(built if built.startswith("@") else f"@{built}" if built else "")
        if is_profile_handle(candidate) and _fits(candidate, *handle_parts):
            handle = candidate
    elif handle and handle_parts and not _fits(handle, *handle_parts):
        handle = ""
    name = full_name
    if not name and name_parts:
        built_name = clean_name(assemble_pieces(name_parts))
        if is_profile_name(built_name) and _fits(built_name, *name_parts):
            name = built_name
    elif name and name_parts and not _fits(name, *name_parts):
        name = ""
    if not handle and not name:
        return
    for index, frame in run:
        username = frame.profile_username
        if not username and handle and _fits(handle, frame.handle_left, frame.handle_right):
            username = handle
        shown = frame.profile_name
        if not shown and name and _fits(name, frame.name_left, frame.name_right):
            shown = name
        if username == frame.profile_username and shown == frame.profile_name:
            continue
        frames[index] = replace(
            frame,
            profile_username=username,
            profile_name=shown,
            handle_left="" if username else frame.handle_left,
            handle_right="" if username else frame.handle_right,
            name_left="" if shown else frame.name_left,
            name_right="" if shown else frame.name_right,
        )


def apply_cover_stitch(frames: list[FrameObs]) -> tuple[list[FrameObs], list[Review]]:
    """Điền số và @ ghép được. Mảnh không đủ thì thành một dòng cần xem, không thành số sai."""
    if not frames:
        return frames, []
    needs = False
    for frame in frames:
        if frame.handle_left or frame.handle_right or frame.name_left or frame.name_right:
            needs = True
            break
        for hit in frame.contacts:
            if hit.seen_left or hit.seen_right:
                needs = True
                break
        if needs:
            break
    if not needs:
        return frames, []

    edited: list[FrameObs] = list(frames)
    run: list[tuple[int, FrameObs]] = []

    def close_run() -> None:
        nonlocal run
        if run:
            _stitch_profile_run(run, edited)
        run = []

    for index, frame in enumerate(edited):
        if frame.kind != "profile":
            close_run()
            continue
        if run and frame.at >= 0 and run[-1][1].at >= 0 and frame.at - run[-1][1].at > 8:
            close_run()
        run.append((index, frame))
    close_run()

    frags: list[_Frag] = []
    clean: list[tuple[str, str]] = []
    for frame_i, frame in enumerate(edited):
        for hit_i, hit in enumerate(frame.contacts):
            phone = normalize_phone(hit.phone)
            name = clean_name(hit.name)
            if name and is_skipped(name):
                name = ""
            if phone:
                clean.append((phone, name))
                continue
            slots = mask_digits(hit.seen_left, hit.seen_right)
            if slots is None:
                continue
            phone = phone_from_slots(slots)
            if phone:
                contacts = list(frame.contacts)
                contacts[hit_i] = ContactHit(
                    phone,
                    name,
                    hit.selected,
                    from_cover=not name,
                    phone_shot=hit.phone_shot,
                    name_shot=hit.name_shot,
                )
                edited[frame_i] = replace(frame, contacts=tuple(contacts))
                frame = edited[frame_i]
                clean.append((phone, name))
                continue
            frags.append(
                _Frag(
                    frame_i,
                    hit_i,
                    partial_digits(hit.seen_left),
                    partial_digits(hit.seen_right),
                    name,
                    frame.at,
                    hit.selected,
                    slots,
                    hit.phone_shot,
                )
            )

    done, notes = stitch_phone_fragments(frags, clean)
    for item in done:
        frame = edited[item.frame_i]
        extra = ContactHit(item.phone, item.name, item.selected, from_cover=not item.name)
        edited[item.frame_i] = replace(frame, contacts=frame.contacts + (extra,))
    return edited, notes
