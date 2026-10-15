"""Gom kết quả mọi khung hình rồi ghép số điện thoại với @id.

Hướng 1 chạy trước, vì đây là cách cho tỷ lệ đủ ba ô cao nhất: một màn danh bạ
được mở hết từng dòng từ trên xuống, hồ sơ hiện rõ. Dòng thứ nhất ứng với hồ sơ
mở đầu tiên. Hàng hướng 1 đã ghép thì không bị hướng sau sửa.

Số, tên hoặc @id còn thiếu mới đi tiếp các hướng sau, mỗi hướng chỉ thêm vào
chỗ còn trống:

1. Cùng tên, và tên đó chỉ có một số với một @id. Nhiều số cùng tên thì lấy
   số đang hiện trên danh bạ ngay trước hồ sơ, nếu chỉ còn một số như vậy.
2. @id trùng với tên danh bạ (TikTok tạo @id từ tên đó).
3. Dòng vừa bấm: khung danh bạ ngay trước hồ sơ có vòng tròn chạm màn hình
   nằm trên đúng một dòng. Nút Back và ảnh đại diện không tính là vòng chạm.
   Các lần mở cùng hồ sơ phải chỉ cùng một dòng.
4. Tên chứa nhau, tên lệch nhẹ ngay trên màn đó, hoặc đúng một số trống tên.
5. Dòng dưới số đã in sẵn @id (user kèm số, hoặc token có chữ số). Chỉ điền
   số và @id còn trống. Chữ thuần vẫn là tên, không biến thành @id.
6. Cặp số và tên mà bước quét chính bỏ sót, và bốn dãy ký tự cùng tên. Hai
   hướng này không sửa hàng đã có.
7. Luồng đọc toàn bộ: tên đọc kỹ trên mọi khung còn lại. Chỉ ghép khi tên
   danh bạ và tên hồ sơ trùng, mỗi bên một cặp. Hàng đã có không đổi.

Mở thiếu một dòng thì không ghép theo thứ tự, trừ khi chỉ còn đúng một số và
một hồ sơ.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from doc_video.frame import FrameRead
from doc_video.text import find_handle, handle_matches_name, lookalike_handles, name_key, subtitle_handle, vote_chars

# Hai lần đọc @id trong cùng một lượt mở hồ sơ giống nhau ít nhất chừng này.
_SAME_VISIT = 0.6


@dataclass(frozen=True)
class Row:
    phone: str
    name: str
    handle: str


@dataclass(frozen=True)
class Review:
    phone: str
    list_name: str
    profile_name: str
    handle: str
    reason: str


@dataclass
class Result:
    rows: list[Row] = field(default_factory=list)
    review: list[Review] = field(default_factory=list)
    unopened: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _Visit:
    handle: str
    visible: tuple[str, ...]


@dataclass
class _Seen:
    names: Counter = field(default_factory=Counter)
    first: int = 0
    count: int = 0
    near: set[str] = field(default_factory=set)
    taps: Counter = field(default_factory=Counter)

    def name(self) -> str:
        """Bỏ phiếu theo từng ký tự giữa các khung đã đọc tên này. Mỗi khung sai một chỗ khác nhau."""
        if not self.names:
            return ""
        if len(self.names) == 1:
            return next(iter(self.names))
        voted = " ".join(vote_chars(list(self.names.items()), loose=True).split())
        if voted:
            return voted
        top = max(self.names.values())
        return min((name for name, hits in self.names.items() if hits == top), key=lambda name: -_marks(name))


def _marks(name: str) -> int:
    return sum(1 for ch in name if ord(ch) > 127)


# Chữ số bộ đọc hay nhầm với nhau khi chữ bị cắt hoặc mờ.
_DIGIT_LOOKALIKE = [set("35"), set("68"), set("17"), set("08"), set("56"), set("89"), set("38")]


def _misread_digits(strong: str, weak: str) -> bool:
    """Lệch đúng một chữ số bất kỳ, hoặc lệch tới ba chữ số mà mọi chỗ lệch là cặp dễ nhầm.

    Chữ số bị che nửa trên thì mọi số 3 trong dòng cùng thành 5 (0913327358 với 0915527558).
    """
    if len(strong) != len(weak):
        return False
    diffs = [(a, b) for a, b in zip(strong, weak) if a != b]
    if len(diffs) == 1:
        return True
    return 2 <= len(diffs) <= 3 and all(any(a in group and b in group for group in _DIGIT_LOOKALIKE) for a, b in diffs)


def _lookalike_only(strong: str, weak: str) -> bool:
    diffs = [(a, b) for a, b in zip(strong, weak) if a != b]
    return bool(diffs) and all(any(a in group and b in group for group in _DIGIT_LOOKALIKE) for a, b in diffs)


def _drop_shadow_duplicates(
    phones: dict[str, _Seen], ordered: set[str], frames_with: dict[str, set[int]]
) -> None:
    """Cặp bổ sung chỉ thêm số đang thiếu.

    Trùng tên một số đã có trong thứ tự dòng thì bỏ, để không biến một cặp đã
    ghép chắc thành nhiều số cùng tên.
    """
    taken = {name_key(phones[phone].name()) for phone in ordered if phone in phones}
    taken.discard("")
    for phone in list(phones):
        if phone in ordered:
            continue
        key = name_key(phones[phone].name())
        if key and key in taken:
            del phones[phone]
            frames_with.pop(phone, None)


def _merge_misreads(phones: dict[str, _Seen], frames_with: dict[str, set[int]]) -> dict[str, str]:
    """Số lệch vài chữ số so với số đọc được nhiều hơn, chưa từng cùng hiện trên một khung: là đọc nhầm.

    Cần cùng tên. Riêng số chỉ lệch ở các chữ số dễ nhầm (3 với 5…) mà hiếm khi thấy (tối đa
    3 khung) trong khi số kia thấy nhiều gấp ba trở lên thì gộp dù tên đọc ra khác: tên trên
    vài khung đó cũng hay đọc sai. Trả về số đọc nhầm và số đúng tương ứng.
    """
    merged: dict[str, str] = {}
    for weak in sorted(phones, key=lambda phone: phones[phone].count):
        if weak not in phones:
            continue
        for strong, seen in sorted(phones.items(), key=lambda item: -item[1].count):
            if strong == weak or seen.count <= phones[weak].count:
                continue
            if not _misread_digits(strong, weak):
                continue
            if frames_with[strong] & frames_with[weak]:
                continue
            same_name = name_key(seen.name()) == name_key(phones[weak].name())
            rare = phones[weak].count <= 3 and seen.count >= 3 * phones[weak].count and _lookalike_only(strong, weak)
            if not same_name and not rare:
                continue
            seen.names.update(phones[weak].names)
            seen.count += phones[weak].count
            frames_with[strong] |= frames_with.pop(weak)
            del phones[weak]
            merged[weak] = strong
            break
    return merged


def _vote_visits(frames: list[FrameRead]) -> list[FrameRead]:
    """Một lượt mở hồ sơ kéo dài nhiều khung. Bỏ phiếu @id giữa các khung đó.

    Lượt mở hết khi gặp lại màn danh bạ, hoặc khi @id khác hẳn (một hồ sơ khác).
    Không bỏ phiếu thì mỗi lần đọc sai một ký tự thành một hồ sơ riêng, và
    việc ghép theo thứ tự mở bị lệch số.
    """
    out = list(frames)
    run: list[int] = []

    def close() -> None:
        if len(run) > 1:
            counts = Counter(out[index].handle[1:] for index in run)
            voted = find_handle("@" + vote_chars(list(counts.items()), loose=False))
            if voted:
                for index in run:
                    out[index] = replace(out[index], handle=voted)
        run.clear()

    for index, frame in enumerate(frames):
        if frame.kind == "list":
            close()
            continue
        if frame.kind != "profile" or not frame.handle:
            continue
        if run:
            last = frames[run[-1]].handle
            if SequenceMatcher(None, last, frame.handle, autojunk=False).ratio() < _SAME_VISIT:
                close()
        run.append(index)
    close()
    return out


def _merge_handle_misreads(frames: list[FrameRead]) -> list[FrameRead]:
    """@id đọc nhầm một ký tự dễ lẫn, cùng tên hồ sơ, gộp vào @id đọc được nhiều lần hơn."""
    counts: Counter = Counter()
    names: dict[str, Counter] = {}
    for frame in frames:
        if frame.kind == "profile" and frame.handle:
            counts[frame.handle] += 1
            names.setdefault(frame.handle, Counter())[name_key(frame.profile_name)] += 1
    mapping: dict[str, str] = {}
    for weak in sorted(counts, key=lambda handle: counts[handle]):
        for strong in sorted(counts, key=lambda handle: -counts[handle]):
            if strong == weak or strong in mapping or counts[strong] <= counts[weak]:
                continue
            if not lookalike_handles(weak, strong):
                continue
            weak_name = names[weak].most_common(1)[0][0]
            strong_name = names[strong].most_common(1)[0][0]
            if weak_name and strong_name and weak_name != strong_name:
                continue
            mapping[weak] = strong
            break
    if not mapping:
        return frames

    def target(handle: str) -> str:
        seen = {handle}
        while handle in mapping and mapping[handle] not in seen:
            handle = mapping[handle]
            seen.add(handle)
        return handle

    return [replace(frame, handle=target(frame.handle)) if frame.handle in mapping else frame for frame in frames]


def _recent_tap(
    tap: tuple[str, int, float], index: int, at: float, visible: tuple[str, ...], last_list: int
) -> bool:
    """Nền xám của dòng bấm tắt ngay khi nhấc ngón tay, nên khung cuối trước hồ sơ thường không còn thấy.

    Dòng bấm ở chính khung danh bạ cuối cùng trước hồ sơ thì nhận ngay. Sớm hơn thì nhận trong
    khoảng 3 giây (hoặc 6 khung với ảnh không có giờ), nếu dòng đó vẫn còn trên màn danh bạ ngay
    trước hồ sơ, tức là chưa cuộn đi nơi khác.
    """
    phone, tap_index, tap_at = tap
    if tap_index == last_list:
        return True
    if phone not in visible:
        return False
    if at >= 0 and tap_at >= 0:
        return at - tap_at <= 3.0
    return index - tap_index <= 6


def build(frames: list[FrameRead]) -> Result:
    frames = _merge_handle_misreads(_vote_visits(frames))
    phones: dict[str, _Seen] = {}
    handles: dict[str, _Seen] = {}
    frames_with: dict[str, set[int]] = {}
    ordered_phones: set[str] = set()
    visits: list[_Visit] = []
    visible: tuple[str, ...] = ()
    # Dòng vừa bấm thấy gần nhất kể từ lần mở hồ sơ trước: (số, vị trí khung, giây).
    tap: tuple[str, int, float] | None = None
    last_list = -1
    order = 0
    for index, frame in enumerate(frames):
        if frame.kind == "list":
            last_list = index
            if frame.tapped:
                tap = (frame.tapped, index, frame.at)
            ordered: list[str] = []
            for contact in frame.contacts:
                seen = phones.get(contact.phone)
                if seen is None:
                    seen = phones[contact.phone] = _Seen(first=order)
                    order += 1
                if contact.name:
                    seen.names[contact.name] += 1
                seen.count += 1
                frames_with.setdefault(contact.phone, set()).add(index)
                # Cặp bổ sung không chen vào thứ tự dòng. Thứ tự mở hồ sơ giữ nguyên.
                if contact.in_order and contact.phone not in ordered:
                    ordered.append(contact.phone)
                if contact.in_order:
                    ordered_phones.add(contact.phone)
            visible = tuple(ordered)
        elif frame.kind == "profile" and frame.handle:
            seen = handles.get(frame.handle)
            if seen is None:
                seen = handles[frame.handle] = _Seen(first=order)
                order += 1
            if frame.profile_name:
                seen.names[frame.profile_name] += 1
            seen.count += 1
            seen.near.update(visible)
            if not visits or visits[-1].handle != frame.handle:
                visits.append(_Visit(frame.handle, visible))
                if tap is not None and _recent_tap(tap, index, frame.at, visible, last_list):
                    seen.taps[tap[0]] += 1
            tap = None
    _drop_shadow_duplicates(phones, ordered_phones, frames_with)
    merged = _merge_misreads(phones, frames_with)
    if merged:
        # Số đọc nhầm đã gộp vào số đúng: mọi chỗ còn nhắc tới số nhầm phải đổi theo, nếu
        # không bước ghép theo thứ tự tra một số không còn tồn tại.
        def fix(phone: str) -> str:
            return merged.get(phone, phone)

        def fix_all(items: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(dict.fromkeys(fix(phone) for phone in items))

        for seen in handles.values():
            seen.taps = Counter({fix(phone): hits for phone, hits in seen.taps.items()})
            seen.near = {fix(phone) for phone in seen.near}
        visits = [_Visit(visit.handle, fix_all(visit.visible)) for visit in visits]
    return _pair(phones, handles, visits)


def _pair(phones: dict[str, _Seen], handles: dict[str, _Seen], visits: list[_Visit]) -> Result:
    result = Result()
    used_phones: set[str] = set()
    used_handles: set[str] = set()
    # Hướng 1. Màn mở hết từ trên xuống được ghép trước. Hàng này không bị sửa.
    _pair_opened_screen(result, phones, handles, visits, used_phones, used_handles)
    _pair_same_name(result, phones, handles, used_phones, used_handles)
    _pair_tapped(result, phones, handles, used_phones, used_handles)
    _pair_contained(result, phones, handles, used_phones, used_handles)
    # Chỗ hướng 1 chưa ghép, sau khi các hướng theo tên đã lấy phần của chúng.
    _pair_opened_screen(result, phones, handles, visits, used_phones, used_handles)
    _pair_similar_on_screen(result, phones, handles, visits, used_phones, used_handles)
    _pair_unnamed_on_screen(result, phones, handles, visits, used_phones, used_handles)
    _pair_printed_username(result, phones, handles, used_phones, used_handles)

    left_phones = [phone for phone in phones if phone not in used_phones]
    left_handles = [handle for handle in handles if handle not in used_handles]
    for handle in left_handles:
        key = name_key(handles[handle].name())
        same = [phone for phone in left_phones if key and name_key(phones[phone].name()) == key]
        if same:
            for phone in same:
                result.review.append(Review(phone, phones[phone].name(), handles[handle].name(), handle, "nhiều số cùng tên"))
                used_phones.add(phone)
            continue
        reason = "không đọc được tên trên hồ sơ" if not key else "đã mở hồ sơ nhưng không thấy số cùng tên"
        result.review.append(Review("", "", handles[handle].name(), handle, reason))

    for phone in phones:
        if phone not in used_phones:
            result.unopened.append((phone, phones[phone].name()))
    result.rows.sort(key=lambda row: phones[row.phone].first)
    result.unopened.sort(key=lambda item: phones[item[0]].first)
    return result


def _pair_printed_username(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Số còn trống mà dòng dưới số đã là @id thì lấy @id đó.

    Không đổi hàng đã ghép. @id đã thuộc số khác thì bỏ qua. Hai số cùng in một
    @id thì không gán, vì không biết số nào đúng.
    """
    claims: dict[str, list[str]] = {}
    for phone, seen in phones.items():
        if phone in used_phones:
            continue
        handle = subtitle_handle(seen.name())
        if not handle or handle in used_handles:
            continue
        claims.setdefault(handle, []).append(phone)
    for handle, found in claims.items():
        if len(found) != 1 or handle in used_handles:
            continue
        phone = found[0]
        if phone in used_phones:
            continue
        if handle in handles:
            _add(result, phones, handles, phone, handle, used_phones, used_handles)
            continue
        used_phones.add(phone)
        used_handles.add(handle)
        result.rows.append(Row(phone, phones[phone].name(), handle))


def _pair_same_name(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Ghép tên trùng hoặc @id sinh từ tên. Bỏ qua số và @id hướng 1 đã lấy."""
    by_key_phone: dict[str, list[str]] = {}
    by_key_handle: dict[str, list[str]] = {}
    for phone, seen in phones.items():
        key = name_key(seen.name())
        if key:
            by_key_phone.setdefault(key, []).append(phone)
    for handle, seen in handles.items():
        key = name_key(seen.name())
        if key:
            by_key_handle.setdefault(key, []).append(handle)

    for key, handle_list in by_key_handle.items():
        phone_list = by_key_phone.get(key, [])
        if len(phone_list) == 1 and len(handle_list) == 1:
            phone, handle = phone_list[0], handle_list[0]
            if phone not in used_phones and handle not in used_handles:
                _add(result, phones, handles, phone, handle, used_phones, used_handles)
            continue
        for handle in handle_list:
            if handle in used_handles:
                continue
            nearby = [
                phone
                for phone in phone_list
                if phone in handles[handle].near and phone not in used_phones
            ]
            if len(nearby) == 1:
                _add(result, phones, handles, nearby[0], handle, used_phones, used_handles)

    for handle in handles:
        if handle in used_handles:
            continue
        agreed = [
            phone
            for phone in phones
            if phone not in used_phones and handle_matches_name(handle, phones[phone].name())
        ]
        on_screen = [phone for phone in agreed if phone in handles[handle].near]
        chosen = on_screen if len(on_screen) == 1 else agreed if len(agreed) == 1 else []
        if len(chosen) == 1:
            _add(result, phones, handles, chosen[0], handle, used_phones, used_handles)


def _pair_tapped(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Ghép hồ sơ với dòng vừa bấm trước khi nó mở.

    Mọi lần mở hồ sơ đó phải chỉ cùng một dòng, và dòng đó không được là dòng
    vừa bấm của một hồ sơ khác. Có mâu thuẫn thì bỏ, để cách khác quyết định.
    """
    claims: dict[str, str] = {}
    for handle, seen in handles.items():
        if handle in used_handles:
            continue
        tapped = {phone for phone in seen.taps if phone in phones}
        if len(tapped) == 1:
            claims[handle] = next(iter(tapped))
    claimed = Counter(claims.values())
    for handle, phone in claims.items():
        if claimed[phone] == 1 and phone not in used_phones:
            _add(result, phones, handles, phone, handle, used_phones, used_handles)


def _contains(left: str, right: str) -> bool:
    """Tên này chứa trọn tên kia (Lê Trang Phan Thiết trong Lê Trang Phan Thiết II), phần chung đủ dài."""
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 6 and len(short) >= 0.6 * len(long) and short in long


def _pair_contained(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Ghép khi tên hồ sơ và tên danh bạ chứa nhau, số đó đang hiện quanh lúc mở hồ sơ, và cả hai phía chỉ có một cặp."""
    options: dict[str, list[str]] = {}
    for handle, seen in handles.items():
        if handle in used_handles:
            continue
        key = name_key(seen.name())
        if not key:
            continue
        options[handle] = [
            phone
            for phone in seen.near
            if phone in phones and phone not in used_phones and _contains(key, name_key(phones[phone].name()))
        ]
    claimed = Counter(phone for found in options.values() for phone in found)
    for handle, found in options.items():
        if len(found) == 1 and claimed[found[0]] == 1 and found[0] not in used_phones:
            _add(result, phones, handles, found[0], handle, used_phones, used_handles)


def _pair_similar_on_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Tên đọc lệch một hai chữ (LêlLee với LêLee, Sade_s với Sads): ghép khi số đó nằm trên màn danh bạ
    ngay trước lúc mở hồ sơ, và cả hai phía chỉ có đúng một cặp gần giống.

    Không xét số ở màn khác: so tên gần giống trên cả danh bạ từng gắn nhầm người.
    """
    shown: dict[str, set[str]] = {}
    for visit in visits:
        shown.setdefault(visit.handle, set()).update(visit.visible)
    options: dict[str, list[str]] = {}
    for handle, seen in handles.items():
        if handle in used_handles:
            continue
        key = name_key(seen.name())
        if len(key) < 3:
            continue
        options[handle] = [
            phone
            for phone in shown.get(handle, ())
            if phone in phones
            and phone not in used_phones
            and _similar(key, name_key(phones[phone].name()))
        ]
    claimed = Counter(phone for found in options.values() for phone in found)
    for handle, found in options.items():
        if len(found) == 1 and claimed[found[0]] == 1 and found[0] not in used_phones:
            _add(result, phones, handles, found[0], handle, used_phones, used_handles)


def _pair_unnamed_on_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Tên danh bạ chỉ gồm biểu tượng nên không đọc được. Hồ sơ còn lại ghép với số đó khi trên màn
    danh bạ ngay trước lúc mở chỉ có đúng một số chưa ghép không đọc được tên, tên hồ sơ không gần
    với số chưa ghép nào khác trên màn đó, và số đó không được hồ sơ nào khác nhắm tới.
    """
    shown: dict[str, set[str]] = {}
    for visit in visits:
        shown.setdefault(visit.handle, set()).update(visit.visible)
    options: dict[str, str] = {}
    for handle, seen in handles.items():
        if handle in used_handles:
            continue
        free = [phone for phone in shown.get(handle, ()) if phone in phones and phone not in used_phones]
        unnamed = [phone for phone in free if not name_key(phones[phone].name())]
        key = name_key(seen.name())
        lookalike = [
            phone
            for phone in free
            if key and name_key(phones[phone].name())
            and SequenceMatcher(None, key, name_key(phones[phone].name()), autojunk=False).ratio() >= 0.5
        ]
        if len(unnamed) == 1 and not lookalike:
            options[handle] = unnamed[0]
    claimed = Counter(options.values())
    for handle, phone in options.items():
        if claimed[phone] == 1 and phone not in used_phones:
            _add(result, phones, handles, phone, handle, used_phones, used_handles)


def _similar(left: str, right: str) -> bool:
    if len(left) < 3 or len(right) < 3:
        return False
    return SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.75


def _pair_opened_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Hướng 1: cuộn danh bạ và mở từng dòng từ trên xuống thì ghép theo thứ tự.

    Hai màn kề nhau còn chung phần lớn số thì là cùng một lượt cuộn. Đã mở hết
    thì ghép đúng chỗ, kể cả khi tên TikTok khác tên danh bạ. Hàng đã có không
    bị đổi. Mở thiếu một dòng thì không dồn các dòng còn lại, trừ khi chỉ còn
    đúng một số và một hồ sơ.
    """
    for shown, opened in _scroll_groups(visits):
        if _pair_aligned_open(result, phones, handles, shown, opened, used_phones, used_handles):
            continue
        pending = [
            handle
            for handle in opened
            if handle not in used_handles and not _named_elsewhere(handle, tuple(shown), phones, handles, used_phones)
        ]
        left = [phone for phone in shown if phone not in used_phones]
        if pending and len(pending) == len(left) == len(shown):
            for phone, handle in zip(left, pending):
                _add(result, phones, handles, phone, handle, used_phones, used_handles)
        elif len(pending) == 1 and len(left) == 1:
            _add(result, phones, handles, left[0], pending[0], used_phones, used_handles)


def _pair_aligned_open(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    shown: list[str],
    opened: list[str],
    used_phones: set[str],
    used_handles: set[str],
) -> bool:
    """Ghép các chỗ còn trống khi đã mở hết dòng và các dòng đã ghép vẫn đúng chỗ.

    Tên hồ sơ trùng tên một số khác trên cùng màn thì không dồn phần còn lại,
    vì thứ tự đã lệch.
    """
    if not shown or len(shown) != len(opened):
        return False
    paired = {row.phone: row.handle for row in result.rows}
    taken = {row.handle for row in result.rows}
    free: list[tuple[str, str]] = []
    visible = tuple(shown)
    for phone, handle in zip(shown, opened):
        got = paired.get(phone)
        if got == handle:
            continue
        if (
            got is None
            and phone in phones
            and handle in handles
            and phone not in used_phones
            and handle not in taken
            and handle not in used_handles
            and not _named_elsewhere(handle, visible, phones, handles, used_phones)
        ):
            free.append((phone, handle))
            continue
        return False
    for phone, handle in free:
        _add(result, phones, handles, phone, handle, used_phones, used_handles)
    return True


def _scroll_groups(visits: list[_Visit]) -> list[tuple[list[str], list[str]]]:
    groups: list[tuple[list[str], list[str]]] = []
    shown: list[str] = []
    opened: list[str] = []
    shown_set: set[str] = set()
    opened_set: set[str] = set()

    def close() -> None:
        nonlocal shown, opened, shown_set, opened_set
        if opened:
            groups.append((shown, opened))
        shown, opened = [], []
        shown_set, opened_set = set(), set()

    for visit in visits:
        if not visit.visible:
            continue
        current = set(visit.visible)
        if shown_set and len(current & shown_set) / len(current | shown_set) < 0.5:
            close()
        for phone in visit.visible:
            if phone not in shown_set:
                shown_set.add(phone)
                shown.append(phone)
        if visit.handle not in opened_set:
            opened_set.add(visit.handle)
            opened.append(visit.handle)
    close()
    return groups


def _named_elsewhere(
    handle: str,
    visible: tuple[str, ...],
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
) -> bool:
    """Hồ sơ trùng tên một số ở màn khác thì không gán cho dòng đang hiện."""
    key = name_key(handles[handle].name())
    if not key:
        return False
    shown = set(visible)
    return any(
        phone not in used_phones and phone not in shown and name_key(seen.name()) == key
        for phone, seen in phones.items()
    )


def _voted_name(names: Counter[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return next(iter(names))
    voted = " ".join(vote_chars(list(names.items()), loose=True).split())
    return voted or names.most_common(1)[0][0]


def add_side_rows(result: Result, frames: list[FrameRead]) -> Result:
    """Thêm hàng ba ô khi tên cột phải trùng tên bên trái, mỗi bên chỉ đúng một cặp.

    Hướng bổ sung, chạy sau hướng 1. Tên còn trống trên hàng đã ghép được điền từ
    đúng số hoặc đúng @id đó. Số và @id của hàng đã có không đổi. Số đang ở bảng
    cần xem không bị kéo sang. @id đã ghép với số khác cũng không bị lấy.
    """
    phone_names: dict[str, Counter[str]] = {}
    phone_order: dict[str, int] = {}
    handle_names: dict[str, Counter[str]] = {}
    handle_order: dict[str, int] = {}
    seen = 0
    for frame in frames:
        for contact in frame.side_contacts:
            if not contact.phone:
                continue
            phone_names.setdefault(contact.phone, Counter())
            if contact.name:
                phone_names[contact.phone][contact.name] += 1
            phone_order.setdefault(contact.phone, seen)
            seen += 1
        if not frame.side_handle.startswith("@"):
            continue
        handle_names.setdefault(frame.side_handle, Counter())
        if frame.side_profile_name:
            handle_names[frame.side_handle][frame.side_profile_name] += 1
        handle_order.setdefault(frame.side_handle, seen)
        seen += 1

    phone_name = {phone: _voted_name(names) for phone, names in phone_names.items()}
    handle_name = {handle: _voted_name(names) for handle, names in handle_names.items()}
    by_phone: dict[str, list[str]] = {}
    by_handle: dict[str, list[str]] = {}
    for phone, name in phone_name.items():
        key = name_key(name)
        if key:
            by_phone.setdefault(key, []).append(phone)
    for handle, name in handle_name.items():
        key = name_key(name)
        if key:
            by_handle.setdefault(key, []).append(handle)

    result.rows = [
        _with_name(row, phone_name.get(row.phone, ""), handle_name.get(row.handle, ""))
        if not row.name
        else row
        for row in result.rows
    ]
    saved_phones = {row.phone for row in result.rows}
    saved_handles = {row.handle for row in result.rows}
    blocked_phones = {item.phone for item in result.review if item.phone}
    blocked_keys = {
        key
        for item in result.review
        if item.reason == "nhiều số cùng tên"
        for key in (name_key(item.list_name), name_key(item.profile_name))
        if key
    }
    added: list[Row] = []
    for key, handles in by_handle.items():
        phones = by_phone.get(key, [])
        if key in blocked_keys or len(phones) != 1 or len(handles) != 1:
            continue
        phone, handle = phones[0], handles[0]
        if phone in saved_phones or phone in blocked_phones or handle in saved_handles:
            continue
        list_name = phone_name[phone]
        profile_name = handle_name[handle]
        name = list_name if _marks(list_name) > _marks(profile_name) else profile_name
        added.append(Row(phone, name, handle))
        saved_phones.add(phone)
        saved_handles.add(handle)
    if not added:
        return result
    added.sort(key=lambda row: phone_order[row.phone])
    result.rows.extend(added)
    added_phones = {row.phone for row in added}
    added_handles = {row.handle for row in added}
    result.unopened = [item for item in result.unopened if item[0] not in added_phones]
    result.review = [item for item in result.review if item.phone not in added_phones and item.handle not in added_handles]
    return result


def add_full_name_rows(result: Result, frames: list[FrameRead]) -> Result:
    """Ghép số với @id khi luồng đọc toàn bộ thấy cùng một tên.

    Luồng này đọc kỹ những khung luồng chính chưa đọc. Chỉ thêm cặp còn trống,
    và chỉ khi đúng một số với đúng một @id mang tên đó. Số và @id đã ghép
    giữ nguyên. Tên còn trống trên hàng đã có được điền từ đúng số hoặc đúng @id.
    """
    phone_names: dict[str, Counter[str]] = {}
    phone_order: dict[str, int] = {}
    handle_names: dict[str, Counter[str]] = {}
    handle_order: dict[str, int] = {}
    seen = 0
    for frame in frames:
        for contact in frame.full_contacts:
            if not contact.phone or not contact.name:
                continue
            phone_names.setdefault(contact.phone, Counter())[contact.name] += 1
            phone_order.setdefault(contact.phone, seen)
            seen += 1
        if not frame.full_handle.startswith("@") or not frame.full_profile_name:
            continue
        handle_names.setdefault(frame.full_handle, Counter())[frame.full_profile_name] += 1
        handle_order.setdefault(frame.full_handle, seen)
        seen += 1
    _merge_full_handles(handle_names, handle_order)

    phone_name = {phone: _voted_name(names) for phone, names in phone_names.items()}
    handle_name = {handle: _voted_name(names) for handle, names in handle_names.items()}
    by_phone: dict[str, list[str]] = {}
    by_handle: dict[str, list[str]] = {}
    for phone, name in phone_name.items():
        key = name_key(name)
        if len(key) >= 2:
            by_phone.setdefault(key, []).append(phone)
    for handle, name in handle_name.items():
        key = name_key(name)
        if len(key) >= 2:
            by_handle.setdefault(key, []).append(handle)

    result.rows = [
        _with_name(row, phone_name.get(row.phone, ""), handle_name.get(row.handle, ""))
        if not row.name
        else row
        for row in result.rows
    ]
    saved_phones = {row.phone for row in result.rows}
    saved_handles = {row.handle for row in result.rows}
    added: list[Row] = []
    for key, handles in by_handle.items():
        phones = by_phone.get(key, [])
        if len(phones) != 1 or len(handles) != 1:
            continue
        phone, handle = phones[0], handles[0]
        if phone in saved_phones or handle in saved_handles:
            continue
        list_name = phone_name[phone]
        profile_name = handle_name[handle]
        name = list_name if _marks(list_name) > _marks(profile_name) else profile_name
        added.append(Row(phone, name, handle))
        saved_phones.add(phone)
        saved_handles.add(handle)
    if added:
        added.sort(key=lambda row: phone_order.get(row.phone, 0))
        result.rows.extend(added)
        added_phones = {row.phone for row in added}
        added_handles = {row.handle for row in added}
        result.unopened = [item for item in result.unopened if item[0] not in added_phones]
        result.review = [
            item for item in result.review if item.phone not in added_phones and item.handle not in added_handles
        ]
    return result


def _merge_full_handles(names: dict[str, Counter[str]], order: dict[str, int]) -> None:
    """@id đọc lệch một ký tự dễ lẫn, cùng tên, gộp vào @id thấy nhiều hơn."""
    counts = {handle: sum(seen.values()) for handle, seen in names.items()}
    for weak in sorted(list(names), key=lambda handle: counts.get(handle, 0)):
        if weak not in names:
            continue
        for strong in sorted(names, key=lambda handle: -counts.get(handle, 0)):
            if strong == weak or strong not in names or counts.get(strong, 0) < counts.get(weak, 0):
                continue
            if not lookalike_handles(weak, strong):
                continue
            weak_name = name_key(_voted_name(names[weak]))
            strong_name = name_key(_voted_name(names[strong]))
            if weak_name and strong_name and weak_name != strong_name:
                continue
            names[strong].update(names.pop(weak))
            order.pop(weak, None)
            counts[strong] = counts.get(strong, 0) + counts.pop(weak, 0)
            break


def _with_name(row: Row, list_name: str, profile_name: str) -> Row:
    """Điền tên còn trống. Không đổi số điện thoại và @id của hàng đã ghép."""
    name = list_name if _marks(list_name) > _marks(profile_name) else profile_name
    if not name:
        name = list_name or profile_name
    return Row(row.phone, name, row.handle) if name else row


def _add(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    phone: str,
    handle: str,
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    used_phones.add(phone)
    used_handles.add(handle)
    list_name = phones[phone].name()
    profile_name = handles[handle].name()
    name = list_name if _marks(list_name) > _marks(profile_name) else profile_name
    result.rows.append(Row(phone, name, handle))
