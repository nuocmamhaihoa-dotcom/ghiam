"""Ghép số, tên và username từ các khung hình danh bạ và hồ sơ.

Cách 2: trong cùng một video, ghép tên gần giống. Giữ ghép đúng từng chữ.
Thêm các tên lệch dấu hoặc lệch vài chữ do đọc hình. Không ghép tên một
từ ngắn, không ghép khi một tên có hai số.

Cách 3: dòng vừa bấm rồi mở hồ sơ thì số đó đi với username vừa mở, kể
cả khi hai tên khác nhau. Không thấy dòng bấm thì không đoán.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher


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
    text = unicodedata.normalize("NFC", str(value or "")).replace("ÿ", "y").replace("Ÿ", "Y")
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
    letters = "".join(ch for ch in folded if ch.isalpha())
    if len(letters) < 2:
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


# Đầu số di động Việt Nam đang cấp. Đầu số không có trong danh sách là chữ số đọc nhầm.
MOBILE_PREFIXES = frozenset(
    {
        "032", "033", "034", "035", "036", "037", "038", "039",
        "052", "055", "056", "058", "059",
        "070", "076", "077", "078", "079",
        "081", "082", "083", "084", "085", "086", "087", "088", "089",
        "090", "091", "092", "093", "094", "096", "097", "098", "099",
    }
)


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
    if len(text) == 10 and text[:3] in MOBILE_PREFIXES:
        return text
    return ""


def choose_phone(*reads: str) -> str:
    """Giữ số khi ít nhất hai lần đọc ra cùng một kết quả. Lệch nhau thì lấy lần đọc sau."""
    found: list[str] = []
    for item in reads:
        phone = normalize_phone(item)
        if phone:
            found.append(phone)
    if not found:
        return ""
    counts: dict[str, int] = {}
    for phone in found:
        counts[phone] = counts.get(phone, 0) + 1
    best = max(counts.values())
    if best >= 2:
        agreed = [phone for phone in found if counts[phone] == best]
        return agreed[-1]
    return found[-1]


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


def _viet_marks(name: str) -> int:
    count = 0
    for ch in name:
        if ch in "ÿŸ":
            continue
        if ord(ch) < 128:
            continue
        if fold_marks(ch) in "aeiouyd":
            count += 1
    return count


def _mark_sits_on_digits(name: str) -> bool:
    chars = list(name)
    for index, ch in enumerate(chars):
        if ord(ch) < 128 or ch in "ÿŸ":
            continue
        neighbors = chars[max(0, index - 1) : index] + chars[index + 1 : index + 2]
        if any(item.isdigit() for item in neighbors):
            return True
    return False


def joined_name(contact: str, profile: str) -> str:
    """Tên hiển thị khi ghép. Ưu tiên dấu tiếng Việt, bỏ ký tự đọc nhầm."""
    contact = clean_name(contact)
    profile = clean_name(profile)
    if not profile:
        return contact
    if not contact:
        return profile
    contact_marks = 0 if _mark_sits_on_digits(contact) else _viet_marks(contact)
    profile_marks = 0 if _mark_sits_on_digits(profile) else _viet_marks(profile)
    if profile_marks > contact_marks:
        return profile
    if contact_marks > profile_marks:
        return contact
    contact_compact = _compact(contact)
    profile_compact = _compact(profile)
    if contact_compact and profile_compact and contact_compact != profile_compact:
        if contact_compact in profile_compact or profile_compact in contact_compact:
            return contact if len(contact_compact) <= len(profile_compact) else profile
    return contact


def _compact(name: str) -> str:
    return "".join(ch for ch in fold_marks(name) if ch.isalnum())


def _significant_tokens(name: str) -> list[str]:
    tokens = []
    for token in fold_marks(name).split():
        compact = "".join(ch for ch in token if ch.isalnum())
        if len(compact) >= 2:
            tokens.append(compact)
    return tokens


def names_close(left: str, right: str) -> bool:
    """Tên gần giống trong một video. Tên ngắn thì không ghép."""
    a = _compact(left)
    b = _compact(right)
    if len(a) < 6 or len(b) < 6:
        return False
    tokens_a = _significant_tokens(left)
    tokens_b = _significant_tokens(right)
    if (len(tokens_a) <= 1 and len(a) < 8) or (len(tokens_b) <= 1 and len(b) < 8):
        return False
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.84 and abs(len(a) - len(b)) <= 2:
        return True
    if len(tokens_a) < 2 or len(tokens_b) < 2 or ratio < 0.72:
        return False
    shorter, longer = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    if not set(shorter) <= set(longer):
        return False
    extras = [token for token in longer if token not in set(shorter)]
    return all(len(token) <= 2 for token in extras)


def pair_close_names(phones: list[tuple[str, str]], users: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Chỉ ghép khi một số khớp đúng một username và ngược lại."""
    phone_hits: dict[str, list[str]] = {}
    user_hits: dict[str, list[str]] = {}
    for phone_id, phone_name in phones:
        for user_id, user_name in users:
            if not names_close(phone_name, user_name):
                continue
            phone_hits.setdefault(phone_id, []).append(user_id)
            user_hits.setdefault(user_id, []).append(phone_id)
    pairs = []
    for phone_id, user_ids in phone_hits.items():
        if len(user_ids) != 1:
            continue
        user_id = user_ids[0]
        if len(user_hits.get(user_id, [])) != 1:
            continue
        pairs.append((phone_id, user_id))
    return pairs


def build_table(frames: list[FrameObs]) -> Table:
    contacts: dict[str, _Tally] = {}
    profiles: dict[str, _Tally] = {}
    phone_seen: dict[str, int] = {}
    user_seen: dict[str, int] = {}
    phone_hits: dict[str, int] = {}
    visits = _walk(frames, contacts, phone_seen, phone_hits)
    _collapse_rare_digits(contacts, phone_hits, phone_seen, visits)

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


def _digit_hamming(left: str, right: str) -> int:
    if len(left) != len(right):
        return 99
    return sum(a != b for a, b in zip(left, right))


def _collapse_rare_digits(
    contacts: dict[str, _Tally],
    phone_hits: dict[str, int],
    phone_seen: dict[str, int],
    visits: list[_Visit],
) -> None:
    """Một khung đọc lệch một chữ số thì gộp vào số đã thấy nhiều lần, cùng tên."""
    redirect: dict[str, str] = {}
    for weak in sorted(phone_hits, key=lambda phone: (phone_hits[phone], phone)):
        best_strong = ""
        best_hits = 0
        for strong, hits in phone_hits.items():
            if strong == weak or hits < 3 or hits < phone_hits[weak] * 2 or hits <= best_hits:
                continue
            if _digit_hamming(strong, weak) != 1:
                continue
            weak_name = contacts[weak].best() if weak in contacts else ""
            strong_name = contacts[strong].best() if strong in contacts else ""
            same = name_key(weak_name) == name_key(strong_name) or names_close(weak_name, strong_name)
            if weak_name and strong_name and not same:
                continue
            best_strong = strong
            best_hits = hits
        if best_strong:
            redirect[weak] = best_strong

    def target(phone: str) -> str:
        seen: set[str] = set()
        while phone in redirect and phone not in seen:
            seen.add(phone)
            phone = redirect[phone]
        return phone

    for weak in sorted(redirect, key=lambda phone: phone_hits.get(phone, 0)):
        strong = target(weak)
        if strong == weak:
            continue
        weak_tally = contacts.pop(weak, None)
        if weak_tally is not None and weak_tally.best():
            strong_tally = contacts.setdefault(strong, _Tally())
            for _ in range(max(1, phone_hits.get(weak, 1))):
                strong_tally.add(weak_tally.best())
        phone_hits[strong] = phone_hits.get(strong, 0) + phone_hits.pop(weak, 0)
        phone_seen.pop(weak, None)
        for visit in visits:
            if visit.phone == weak:
                visit.phone = strong


def _walk(
    frames: list[FrameObs],
    contacts: dict[str, _Tally],
    phone_seen: dict[str, int],
    phone_hits: dict[str, int],
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
                phone_hits[phone] = phone_hits.get(phone, 0) + 1
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
        used_phones.add(phone)
        used_users.add(username)
        claimed_users.add(username)
        if name_key(contact_name) == name_key(profile_name):
            display = _prettier(profile_name, contact_name)
        elif names_close(contact_name, profile_name):
            display = joined_name(contact_name, profile_name)
        else:
            display = contact_name
        table.rows.append(Row(phone, display, username))


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

    paired_phones: set[str] = set()
    paired_users: set[str] = set()
    for key in set(phones_by_name) | set(users_by_name):
        phones = phones_by_name.get(key, [])
        usernames = users_by_name.get(key, [])
        if len(phones) == 1 and len(usernames) == 1:
            phone = phones[0]
            username = usernames[0]
            paired_phones.add(phone)
            paired_users.add(username)
            table.rows.append(Row(phone, _prettier(user_name[username], phone_name[phone]), username))

    left_phones = [
        phone
        for phones in phones_by_name.values()
        for phone in phones
        if phone not in paired_phones
    ]
    left_users = [
        username
        for usernames in users_by_name.values()
        for username in usernames
        if username not in paired_users
    ]
    for phone, username in pair_close_names(
        [(phone, phone_name[phone]) for phone in left_phones],
        [(username, user_name[username]) for username in left_users],
    ):
        paired_phones.add(phone)
        paired_users.add(username)
        table.rows.append(Row(phone, joined_name(phone_name[phone], user_name[username]), username))

    rest_phones: dict[str, list[str]] = {}
    rest_users: dict[str, list[str]] = {}
    for phone in left_phones:
        if phone in paired_phones:
            continue
        rest_phones.setdefault(name_key(phone_name[phone]), []).append(phone)
    for username in left_users:
        if username in paired_users:
            continue
        rest_users.setdefault(name_key(user_name[username]), []).append(username)
    for key in set(rest_phones) | set(rest_users):
        _emit_unpaired(
            rest_phones.get(key, []),
            rest_users.get(key, []),
            phone_name,
            user_name,
            table,
        )


def _emit_unpaired(
    phones: list[str],
    usernames: list[str],
    phone_name: dict[str, str],
    user_name: dict[str, str],
    table: Table,
) -> None:
    if len(phones) == 1 and not usernames:
        table.unopened.append(Unopened(phones[0], phone_name[phones[0]]))
        return
    if not phones and len(usernames) == 1:
        username = usernames[0]
        table.review.append(Review("", "", user_name[username], username, "đã mở hồ sơ nhưng chưa thấy số"))
        return
    if usernames:
        for phone in phones:
            table.review.append(Review(phone, phone_name[phone], "", "", "trùng tên, không tự ghép"))
        for username in usernames:
            table.review.append(Review("", "", user_name[username], username, "trùng tên, không tự ghép"))
        return
    for phone in phones:
        table.unopened.append(Unopened(phone, phone_name[phone]))
