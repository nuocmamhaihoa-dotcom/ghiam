"""Ghép số, tên và username từ các khung hình danh bạ và hồ sơ.

Cách 2: ghép khi tên trên danh bạ và tên trên hồ sơ trùng nhau sau khi
chuẩn hóa (viết thường, gom khoảng trắng, giữ dấu tiếng Việt).

Cách 3: khi video đang ở danh bạ rồi mở hồ sơ, hồ sơ đó gắn với dòng vừa
được chọn. Tên trùng thì nhận. Tên lệch, hoặc một tên có hai số, thì để
vào danh sách cần xem, không đưa vào bảng chính.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


SKIP_FOLDED = {
    "thong bao he thong",
    "danh ba",
    "follow",
    "tin nhan",
    "da follow",
    "follower",
    "thich",
}


@dataclass(frozen=True)
class ContactHit:
    phone: str
    name: str
    selected: bool = False


@dataclass(frozen=True)
class FrameObs:
    """Một khung hình đã đọc xong. kind là list, profile hoặc unknown."""

    kind: str
    contacts: tuple[ContactHit, ...] = ()
    profile_name: str = ""
    profile_username: str = ""


@dataclass(frozen=True)
class Row:
    phone: str
    name: str
    username: str


@dataclass(frozen=True)
class Review:
    phone: str
    contact_name: str
    profile_name: str
    username: str
    reason: str


@dataclass(frozen=True)
class Unopened:
    phone: str
    name: str


@dataclass
class Table:
    rows: list[Row] = field(default_factory=list)
    review: list[Review] = field(default_factory=list)
    unopened: list[Unopened] = field(default_factory=list)


@dataclass
class _Tally:
    counts: dict[str, int] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    marks: dict[str, int] = field(default_factory=dict)

    def add(self, raw: str) -> None:
        text = clean_name(raw)
        if not text:
            return
        key = name_key(text)
        self.counts[key] = self.counts.get(key, 0) + 1
        score = mark_count(text)
        current = self.marks.get(key, -1)
        if score > current or (score == current and len(text) > len(self.display.get(key, ""))):
            self.display[key] = text
            self.marks[key] = score

    def best(self) -> str:
        if not self.counts:
            return ""
        key = max(self.counts, key=lambda item: (self.counts[item], self.marks.get(item, 0), len(self.display[item])))
        return self.display[key]


@dataclass
class _Visit:
    phone: str = ""
    names: _Tally = field(default_factory=_Tally)
    usernames: dict[str, int] = field(default_factory=dict)

    def username(self) -> str:
        if not self.usernames:
            return ""
        return max(self.usernames, key=lambda item: (self.usernames[item], item))

    def name(self) -> str:
        return self.names.best()


def name_key(name: str) -> str:
    """Khóa so tên. Giữ dấu: Đặng Thị Tâm khác Dang Thi Tam."""
    text = unicodedata.normalize("NFC", str(name or ""))
    return " ".join(text.casefold().split())


def fold_marks(name: str) -> str:
    """Bỏ dấu chỉ để nhận dòng hệ thống, không dùng để ghép người."""
    text = unicodedata.normalize("NFD", name_key(name))
    stripped = "".join(ch for ch in text if not unicodedata.combining(ch))
    return stripped.replace("đ", "d")


def mark_count(name: str) -> int:
    return sum(1 for ch in name if ord(ch) > 127)


def clean_name(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    kept = []
    for ch in text:
        if ch.isalnum() or ch.isspace() or ch in "._-":
            kept.append(ch)
        else:
            kept.append(" ")
    return " ".join("".join(kept).split())[:80]


def is_skipped(name: str) -> bool:
    folded = fold_marks(name)
    if not folded or folded in SKIP_FOLDED:
        return True
    if folded.replace(" ", "").isdigit():
        return True
    return False


def clean_username(value: str) -> str:
    text = "".join(str(value or "").split())
    if not text.startswith("@"):
        return ""
    handle = text[1:]
    if not 2 <= len(handle) <= 24:
        return ""
    if not all(ch.isascii() and (ch.isalnum() or ch in "._") for ch in handle):
        return ""
    if handle.startswith(".") or handle.endswith("."):
        return ""
    return "@" + handle


def normalize_phone(raw: str) -> str:
    """Số di động Việt Nam 10 số. O đứng giữa chữ số được đọc thành 0."""
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
    text = "".join(digits)
    if len(text) == 11 and text.startswith("84"):
        text = "0" + text[2:]
    if len(text) == 10 and text[0] == "0" and text[1] in "35789":
        return text
    return ""


def phone_in_text(value: str) -> str:
    """Lấy số điện thoại trong một dòng chữ. Không nuốt số follower."""
    buffer: list[str] = []
    saw_digit = False

    def flush() -> str:
        return normalize_phone("".join(buffer))

    for ch in str(value or ""):
        if ch.isdigit():
            buffer.append(ch)
            saw_digit = True
            continue
        if ch in "Oo" and saw_digit:
            buffer.append("0")
            continue
        if ch.isspace() or ch in "-.()":
            continue
        found = flush()
        if found:
            return found
        buffer = []
        saw_digit = False
    return flush()


def _prettier(left: str, right: str) -> str:
    if mark_count(left) != mark_count(right):
        return left if mark_count(left) > mark_count(right) else right
    return left or right


def build_table(frames: list[FrameObs]) -> Table:
    contacts: dict[str, _Tally] = {}
    profiles: dict[str, _Tally] = {}
    phone_seen: dict[str, int] = {}
    user_seen: dict[str, int] = {}
    visits = _walk(frames, contacts, phone_seen)

    for index, visit in enumerate(visits):
        username = visit.username()
        if not username:
            continue
        profiles.setdefault(username, _Tally()).add(visit.name())
        user_seen.setdefault(username, index)

    phone_name = {phone: tally.best() for phone, tally in contacts.items()}
    phone_name = {phone: name for phone, name in phone_name.items() if name and not is_skipped(name)}
    user_name = {user: tally.best() for user, tally in profiles.items()}
    user_name = {user: name for user, name in user_name.items() if name and not is_skipped(name)}

    used_phones: set[str] = set()
    used_users: set[str] = set()
    table = Table()
    _apply_visits(visits, phone_name, user_name, used_phones, used_users, table)
    _apply_names(phone_name, user_name, phone_seen, user_seen, used_phones, used_users, table)
    table.rows.sort(key=lambda row: phone_seen.get(row.phone, 0))
    table.unopened.sort(key=lambda row: phone_seen.get(row.phone, 0))
    table.review.sort(key=lambda row: (phone_seen.get(row.phone, 10**9), user_seen.get(row.username, 10**9)))
    return table


def _walk(
    frames: list[FrameObs],
    contacts: dict[str, _Tally],
    phone_seen: dict[str, int],
) -> list[_Visit]:
    phase = "none"
    armed = ""
    clear_run = 0
    visits: list[_Visit] = []
    open_visit: _Visit | None = None

    def close() -> None:
        nonlocal open_visit
        if open_visit and open_visit.username():
            visits.append(open_visit)
        open_visit = None

    for frame_index, frame in enumerate(frames):
        if frame.kind == "list" and frame.contacts:
            close()
            phase = "list"
            visible: set[str] = set()
            selected = ""
            multi = False
            for hit in frame.contacts:
                phone = normalize_phone(hit.phone)
                name = clean_name(hit.name)
                if not phone or not name or is_skipped(name):
                    continue
                visible.add(phone)
                tally = contacts.get(phone)
                if tally is None:
                    tally = _Tally()
                    contacts[phone] = tally
                    phone_seen[phone] = frame_index
                tally.add(name)
                if hit.selected:
                    if selected:
                        multi = True
                    selected = phone
            if multi:
                armed = ""
                clear_run = 0
            elif selected:
                armed = selected
                clear_run = 0
            elif armed and armed in visible:
                clear_run += 1
                if clear_run >= 2:
                    armed = ""
                    clear_run = 0
            continue

        username = clean_username(frame.profile_username)
        if frame.kind != "profile" or not username:
            continue
        if open_visit is None or phase != "profile":
            close()
            phone = armed if phase == "list" else ""
            open_visit = _Visit(phone=phone)
            if phase == "list":
                armed = ""
                clear_run = 0
            phase = "profile"
        elif open_visit.username() and open_visit.username() != username:
            close()
            open_visit = _Visit(phone="")
        open_visit.usernames[username] = open_visit.usernames.get(username, 0) + 1
        if frame.profile_name:
            open_visit.names.add(frame.profile_name)
    close()
    return visits


def _names_for(key: str, named: dict[str, str]) -> list[str]:
    return [item for item, name in named.items() if name_key(name) == key]


def _apply_visits(
    visits: list[_Visit],
    phone_name: dict[str, str],
    user_name: dict[str, str],
    used_phones: set[str],
    used_users: set[str],
    table: Table,
) -> None:
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for visit in visits:
        phone = visit.phone
        username = visit.username()
        if not phone or not username or phone not in phone_name:
            continue
        if username not in user_name:
            if phone not in used_phones:
                used_phones.add(phone)
                used_users.add(username)
                table.review.append(Review(phone, phone_name[phone], "", username, "không đọc được tên hồ sơ"))
            continue
        if phone in grouped and username in grouped[phone]:
            continue
        if phone not in grouped:
            grouped[phone] = []
            order.append(phone)
        grouped[phone].append(username)

    claimed_users: set[str] = set()
    for phone in order:
        usernames = [item for item in grouped[phone] if item not in claimed_users]
        if not usernames:
            continue
        contact_name = phone_name[phone]
        if len(usernames) > 1:
            used_phones.add(phone)
            for username in usernames:
                used_users.add(username)
                claimed_users.add(username)
                table.review.append(
                    Review(phone, contact_name, user_name[username], username, "một số mở nhiều hồ sơ")
                )
            continue
        username = usernames[0]
        profile_name = user_name[username]
        same = name_key(contact_name) == name_key(profile_name)
        crowded = len(_names_for(name_key(contact_name), phone_name)) > 1 or len(
            _names_for(name_key(profile_name), user_name)
        ) > 1
        used_phones.add(phone)
        used_users.add(username)
        claimed_users.add(username)
        if same and not crowded:
            table.rows.append(Row(phone, _prettier(profile_name, contact_name), username))
            continue
        reason = "trùng tên, cần xem" if same else "tên danh bạ và tên hồ sơ khác nhau"
        table.review.append(Review(phone, contact_name, profile_name, username, reason))


def _apply_names(
    phone_name: dict[str, str],
    user_name: dict[str, str],
    phone_seen: dict[str, int],
    user_seen: dict[str, int],
    used_phones: set[str],
    used_users: set[str],
    table: Table,
) -> None:
    phones_by_name: dict[str, list[str]] = {}
    users_by_name: dict[str, list[str]] = {}
    for phone, name in phone_name.items():
        if phone in used_phones:
            continue
        phones_by_name.setdefault(name_key(name), []).append(phone)
    for username, name in user_name.items():
        if username in used_users:
            continue
        users_by_name.setdefault(name_key(name), []).append(username)

    for key in phones_by_name:
        phones_by_name[key].sort(key=lambda phone: phone_seen.get(phone, 0))
    for key in users_by_name:
        users_by_name[key].sort(key=lambda username: user_seen.get(username, 0))

    for key in set(phones_by_name) | set(users_by_name):
        phones = phones_by_name.get(key, [])
        usernames = users_by_name.get(key, [])
        if len(phones) == 1 and len(usernames) == 1:
            phone = phones[0]
            username = usernames[0]
            table.rows.append(Row(phone, _prettier(user_name[username], phone_name[phone]), username))
            continue
        if len(phones) == 1 and not usernames:
            table.unopened.append(Unopened(phones[0], phone_name[phones[0]]))
            continue
        if not phones and len(usernames) == 1:
            username = usernames[0]
            table.review.append(
                Review("", "", user_name[username], username, "đã mở hồ sơ nhưng chưa thấy số")
            )
            continue
        if usernames:
            for phone in phones:
                table.review.append(Review(phone, phone_name[phone], "", "", "trùng tên, không tự ghép"))
            for username in usernames:
                table.review.append(Review("", "", user_name[username], username, "trùng tên, không tự ghép"))
            continue
        for phone in phones:
            table.unopened.append(Unopened(phone, phone_name[phone]))
