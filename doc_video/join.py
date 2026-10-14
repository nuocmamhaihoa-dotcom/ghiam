"""Gom kết quả mọi khung hình rồi ghép số điện thoại với @id có cùng tên.

Một người hiện ở nhiều khung, nên mỗi số và mỗi @id lấy tên được đọc nhiều
lần nhất. Hai tên coi là giống nhau khi trùng sau khi bỏ dấu, bỏ hoa thường,
bỏ khoảng trắng (Trúc = Truc = TRUC).

Bảng chính chỉ nhận cặp chắc chắn: tên đó có đúng một số và đúng một @id.
Nếu một tên có nhiều số, dùng thứ tự video để chọn: hồ sơ được mở ngay sau
màn hình danh bạ có đúng một trong các số đó. Còn lại đưa sang bảng cần xem,
không đoán.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from doc_video.frame import FrameRead
from doc_video.text import name_key


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
    on_screen: set[str] = set()
    order = 0
    for index, frame in enumerate(frames):
        if frame.kind == "list":
            on_screen = set()
            for contact in frame.contacts:
                seen = phones.get(contact.phone)
                if seen is None:
                    seen = phones[contact.phone] = _Seen(first=order)
                    order += 1
                seen.names[contact.name] += 1
                seen.count += 1
                frames_with.setdefault(contact.phone, set()).add(index)
                on_screen.add(contact.phone)
        elif frame.kind == "profile" and frame.handle:
            seen = handles.get(frame.handle)
            if seen is None:
                seen = handles[frame.handle] = _Seen(first=order)
                order += 1
            if frame.profile_name:
                seen.names[frame.profile_name] += 1
            seen.count += 1
            seen.near |= on_screen
    _merge_misreads(phones, frames_with)
    return _pair(phones, handles)


def _pair(phones: dict[str, _Seen], handles: dict[str, _Seen]) -> Result:
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
        else:
            result.review.append(Review("", "", "", handle, "không đọc được tên trên hồ sơ"))

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

    left_phones = [phone for phone in phones if phone not in used_phones]
    left_handles = [handle for handle in handles if handle not in used_handles and name_key(handles[handle].name())]
    for handle in left_handles:
        key = name_key(handles[handle].name())
        same = [phone for phone in left_phones if name_key(phones[phone].name()) == key]
        if same:
            for phone in same:
                result.review.append(Review(phone, phones[phone].name(), handles[handle].name(), handle, "nhiều số cùng tên"))
                used_phones.add(phone)
            continue
        close = [phone for phone in left_phones if _close(name_key(phones[phone].name()), key)]
        if len(close) == 1:
            phone = close[0]
            used_phones.add(phone)
            result.review.append(Review(phone, phones[phone].name(), handles[handle].name(), handle, "tên gần giống, cần xem"))
        else:
            result.review.append(Review("", "", handles[handle].name(), handle, "đã mở hồ sơ nhưng không thấy số cùng tên"))

    for phone in phones:
        if phone not in used_phones:
            result.unopened.append((phone, phones[phone].name()))
    result.rows.sort(key=lambda row: phones[row.phone].first)
    result.unopened.sort(key=lambda item: phones[item[0]].first)
    return result


def _close(left: str, right: str) -> bool:
    if min(len(left), len(right)) < 5:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.88


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
