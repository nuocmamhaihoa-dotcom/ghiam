"""Gom kết quả mọi khung hình rồi ghép số điện thoại với @id.

Tên trên danh bạ là tên trong máy, tên trên hồ sơ là tên TikTok. Hai tên này
thường khác nhau dù người dùng đã mở đúng hồ sơ. Ghép theo ba cách chắc chắn,
không đoán tên gần giống:

1. Cùng tên, và tên đó chỉ có một số với một @id. Nhiều số cùng tên thì lấy
   số đang hiện trên danh bạ ngay trước hồ sơ, nếu chỉ còn một số như vậy.
2. @id trùng với tên danh bạ (TikTok tạo @id từ tên đó).
3. Dòng vừa bấm: khung danh bạ ngay trước hồ sơ có vòng tròn chạm màn hình
   nằm trên đúng một dòng. Các lần mở cùng hồ sơ phải chỉ cùng một dòng.
4. Một màn danh bạ được mở lần lượt hết từng dòng, từ trên xuống: dòng thứ
   nhất ứng với hồ sơ mở đầu tiên. Mở thiếu một dòng thì không ghép theo thứ tự.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from doc_video.frame import FrameRead
from doc_video.text import find_handle, handle_matches_name, lookalike_handles, name_key, vote_chars, vote_is_tied

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
    # Khung hồ sơ mà phiếu @id đã ngã ngũ, và khung thuộc một lượt phiếu hoà.
    clear_frames: int = 0
    tied_frames: int = 0

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

    def name_tied(self) -> bool:
        """Phiếu tên hoà đúng một vị trí: hai ký tự cùng trọng số, không bên nào nhiều hơn."""
        if len(self.names) <= 1:
            return False
        _text, tied = vote_is_tied(list(self.names.items()), loose=True)
        return tied

    def handle_uncertain(self) -> bool:
        """@id này chỉ xuất hiện trong lượt mở mà phiếu từng ký tự bị hoà."""
        return self.tied_frames > 0 and self.clear_frames == 0


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


def _plain_one_digit(strong: str, weak: str) -> bool:
    """Lệch đúng một chữ số, và chỗ lệch không thuộc cặp dễ nhầm (3/5, 6/8…)."""
    diffs = [(a, b) for a, b in zip(strong, weak) if a != b]
    if len(diffs) != 1:
        return False
    left, right = diffs[0]
    return not any(left in group and right in group for group in _DIGIT_LOOKALIKE)


def _merge_misreads(phones: dict[str, _Seen], frames_with: dict[str, set[int]]) -> tuple[dict[str, str], set[str]]:
    """Số lệch vài chữ số so với số đọc được nhiều hơn, chưa từng cùng hiện trên một khung: là đọc nhầm.

    Cần cùng tên. Riêng số chỉ lệch ở các chữ số dễ nhầm (3 với 5…) mà hiếm khi thấy (tối đa
    3 khung) trong khi số kia thấy nhiều gấp ba trở lên thì gộp dù tên đọc ra khác: tên trên
    vài khung đó cũng hay đọc sai. Trả về số đọc nhầm và số đúng tương ứng, cùng các số đúng
    bị gộp vì lệch một chữ số không dễ nhầm: những số đó chưa chắc nên đưa sang mục cần xem.
    """
    merged: dict[str, str] = {}
    uncertain: set[str] = set()
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
            if _plain_one_digit(strong, weak):
                uncertain.add(strong)
            seen.names.update(phones[weak].names)
            seen.count += phones[weak].count
            frames_with[strong] |= frames_with.pop(weak)
            del phones[weak]
            merged[weak] = strong
            break
    return merged, uncertain


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
            body, tied = vote_is_tied(list(counts.items()), loose=False)
            voted = find_handle("@" + body) if body else ""
            if tied:
                for index in run:
                    out[index] = replace(out[index], handle_uncertain=True)
            elif voted:
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
            if frame.handle_uncertain:
                seen.tied_frames += 1
            else:
                seen.clear_frames += 1
            seen.near.update(visible)
            if not visits or visits[-1].handle != frame.handle:
                visits.append(_Visit(frame.handle, visible))
                if tap is not None and _recent_tap(tap, index, frame.at, visible, last_list):
                    seen.taps[tap[0]] += 1
            tap = None
    merged, uncertain_phones = _merge_misreads(phones, frames_with)
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
    return _pair(phones, handles, visits, uncertain_phones)


def _pair(
    phones: dict[str, _Seen], handles: dict[str, _Seen], visits: list[_Visit], uncertain_phones: set[str]
) -> Result:
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
            _add(result, phones, handles, phone_list[0], handle_list[0], used_phones, used_handles, uncertain_phones)
            continue
        for handle in handle_list:
            nearby = [phone for phone in phone_list if phone in handles[handle].near and phone not in used_phones]
            if len(nearby) == 1:
                _add(result, phones, handles, nearby[0], handle, used_phones, used_handles, uncertain_phones)

    for handle in handles:
        if handle in used_handles:
            continue
        agreed = [phone for phone in phones if phone not in used_phones and handle_matches_name(handle, phones[phone].name())]
        on_screen = [phone for phone in agreed if phone in handles[handle].near]
        chosen = on_screen if len(on_screen) == 1 else agreed if len(agreed) == 1 else []
        if len(chosen) == 1:
            _add(result, phones, handles, chosen[0], handle, used_phones, used_handles, uncertain_phones)

    _pair_tapped(result, phones, handles, used_phones, used_handles, uncertain_phones)
    _pair_contained(result, phones, handles, used_phones, used_handles, uncertain_phones)
    _pair_opened_screen(result, phones, handles, visits, used_phones, used_handles, uncertain_phones)
    _pair_similar_on_screen(result, phones, handles, visits, used_phones, used_handles, uncertain_phones)
    _pair_unnamed_on_screen(result, phones, handles, visits, used_phones, used_handles, uncertain_phones)

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
            if phone in uncertain_phones:
                result.review.append(Review(phone, phones[phone].name(), "", "", "số đọc chưa chắc"))
            else:
                result.unopened.append((phone, phones[phone].name()))
    result.rows.sort(key=lambda row: phones[row.phone].first)
    result.unopened.sort(key=lambda item: phones[item[0]].first)
    return result


def _pair_tapped(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    used_phones: set[str],
    used_handles: set[str],
    uncertain_phones: set[str],
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
            _add(result, phones, handles, phone, handle, used_phones, used_handles, uncertain_phones)


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
    uncertain_phones: set[str],
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
            _add(result, phones, handles, found[0], handle, used_phones, used_handles, uncertain_phones)


def _pair_similar_on_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
    uncertain_phones: set[str],
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
            _add(result, phones, handles, found[0], handle, used_phones, used_handles, uncertain_phones)


def _pair_unnamed_on_screen(
    result: Result,
    phones: dict[str, _Seen],
    handles: dict[str, _Seen],
    visits: list[_Visit],
    used_phones: set[str],
    used_handles: set[str],
    uncertain_phones: set[str],
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
            _add(result, phones, handles, phone, handle, used_phones, used_handles, uncertain_phones)


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
    uncertain_phones: set[str],
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
                _add(result, phones, handles, phone, handle, used_phones, used_handles, uncertain_phones)
        elif len(pending) == 1 and len(left) == 1:
            _add(result, phones, handles, left[0], pending[0], used_phones, used_handles, uncertain_phones)


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
    uncertain_phones: set[str],
) -> None:
    used_phones.add(phone)
    used_handles.add(handle)
    list_name = phones[phone].name()
    profile_name = handles[handle].name()
    use_list = _marks(list_name) > _marks(profile_name)
    name = list_name if use_list else profile_name
    name_tied = phones[phone].name_tied() if use_list else handles[handle].name_tied()
    if phone in uncertain_phones:
        reason = "số đọc chưa chắc"
    elif handles[handle].handle_uncertain():
        reason = "id đọc chưa chắc"
    elif name_tied:
        reason = "tên đọc chưa chắc"
    else:
        result.rows.append(Row(phone, name, handle))
        return
    result.review.append(Review(phone, list_name, profile_name, handle, reason))
