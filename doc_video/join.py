"""Gom kết quả mọi khung hình rồi ghép số điện thoại với @id.

Tên trên danh bạ là tên trong máy, tên trên hồ sơ là tên TikTok. Hai tên này
thường khác nhau dù người dùng đã mở đúng hồ sơ. Ghép theo ba cách chắc chắn,
không đoán tên gần giống:

1. Cùng tên, và tên đó chỉ có một số với một @id. Nhiều số cùng tên thì lấy
   số đang hiện trên danh bạ ngay trước hồ sơ, nếu chỉ còn một số như vậy.
2. @id trùng với tên danh bạ (TikTok tạo @id từ tên đó).
3. Một màn danh bạ được mở lần lượt hết từng dòng, từ trên xuống: dòng thứ
   nhất ứng với hồ sơ mở đầu tiên. Mở thiếu một dòng thì không ghép theo thứ tự.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from doc_video.frame import FrameRead
from doc_video.text import handle_matches_name, name_key


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

    def name(self) -> str:
        if not self.names:
            return ""
        top = max(self.names.values())
        return min((name for name, hits in self.names.items() if hits == top), key=lambda name: -_marks(name))


def _marks(name: str) -> int:
    return sum(1 for ch in name if ord(ch) > 127)


def _merge_misreads(phones: dict[str, _Seen], frames_with: dict[str, set[int]]) -> None:
    """Số lệch đúng một chữ số so với số đọc được nhiều hơn, cùng tên, chưa từng cùng hiện trên một khung: là đọc nhầm."""
    for weak in sorted(phones, key=lambda phone: phones[phone].count):
        if weak not in phones:
            continue
        for strong, seen in sorted(phones.items(), key=lambda item: -item[1].count):
            if strong == weak or seen.count <= phones[weak].count:
                continue
            if sum(a != b for a, b in zip(strong, weak)) != 1:
                continue
            if frames_with[strong] & frames_with[weak]:
                continue
            if name_key(seen.name()) != name_key(phones[weak].name()):
                continue
            seen.names.update(phones[weak].names)
            seen.count += phones[weak].count
            frames_with[strong] |= frames_with.pop(weak)
            del phones[weak]
            break


def build(frames: list[FrameRead]) -> Result:
    phones: dict[str, _Seen] = {}
    handles: dict[str, _Seen] = {}
    frames_with: dict[str, set[int]] = {}
    visits: list[_Visit] = []
    visible: tuple[str, ...] = ()
    order = 0
    for index, frame in enumerate(frames):
        if frame.kind == "list":
            ordered: list[str] = []
            for contact in frame.contacts:
                seen = phones.get(contact.phone)
                if seen is None:
                    seen = phones[contact.phone] = _Seen(first=order)
                    order += 1
                seen.names[contact.name] += 1
                seen.count += 1
                frames_with.setdefault(contact.phone, set()).add(index)
                if contact.phone not in ordered:
                    ordered.append(contact.phone)
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
    _merge_misreads(phones, frames_with)
    return _pair(phones, handles, visits)


def _pair(phones: dict[str, _Seen], handles: dict[str, _Seen], visits: list[_Visit]) -> Result:
    result = Result()
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

    used_phones: set[str] = set()
    used_handles: set[str] = set()
    for key, handle_list in by_key_handle.items():
        phone_list = by_key_phone.get(key, [])
        if len(phone_list) == 1 and len(handle_list) == 1:
            _add(result, phones, handles, phone_list[0], handle_list[0], used_phones, used_handles)
            continue
        for handle in handle_list:
            nearby = [phone for phone in phone_list if phone in handles[handle].near and phone not in used_phones]
            if len(nearby) == 1:
                _add(result, phones, handles, nearby[0], handle, used_phones, used_handles)

    for handle in handles:
        if handle in used_handles:
            continue
        agreed = [phone for phone in phones if phone not in used_phones and handle_matches_name(handle, phones[phone].name())]
        on_screen = [phone for phone in agreed if phone in handles[handle].near]
        chosen = on_screen if len(on_screen) == 1 else agreed if len(agreed) == 1 else []
        if len(chosen) == 1:
            _add(result, phones, handles, chosen[0], handle, used_phones, used_handles)

    _pair_opened_screen(result, phones, handles, visits, used_phones, used_handles)

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


def _pair_opened_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
) -> None:
    """Cuộn danh bạ và mở từng dòng từ trên xuống thì ghép theo thứ tự.

    Hai màn kề nhau còn chung phần lớn số thì là cùng một lượt cuộn. Chỉ ghép
    khi số hồ sơ trong lượt đó bằng số điện thoại, nghĩa là đã mở hết.
    """
    for shown, opened in _scroll_groups(visits):
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
