"""Ghép số, tên và username từ các khung hình danh bạ và hồ sơ.

Quét danh bạ lấy số + tên. Đọc hồ sơ lấy tên + @. Chỉ ghép thành một hàng
khi danh bạ đứng ngay trước hồ sơ, hai tên là cùng một khách, và không có
khách khác chen giữa. Tên khác nhau thì không ghép, kể cả vừa bấm dòng đó.
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
    """Một khung hình đã đọc xong.

    kind là list, profile, unknown, hoặc tap: khung ngay trước lúc mở hồ sơ,
    chỉ chứa dòng vừa bấm. at là giây trong video, -1 với ảnh chụp.
    """

    kind: str
    contacts: tuple[ContactHit, ...] = ()
    profile_name: str = ""
    profile_username: str = ""
    at: float = -1.0


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
    window: tuple[tuple[str, str], ...] = ()
    at: float = -1.0

    def username(self) -> str:
        if not self.usernames:
            return ""
        return _prefer_handle(list(self.usernames), self.name(), self.usernames)

    def name(self) -> str:
        return self.names.best()


@dataclass
class _PhoneCue:
    """SĐT vừa bấm / vừa chọn — chờ ghép với @ theo thời gian (P1)."""

    phone: str
    at: float
    window: tuple[tuple[str, str], ...] = ()


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
    """Lấy số được nhiều lần đọc nhất. Không có đa số thì lấy lần đọc đứng trước, là cách đọc tin nhất."""
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
        return next(phone for phone in found if counts[phone] == best)
    return found[0]


_SIX_LIKE = frozenset("óòöéèÓÒÖÉÈ")


def fix_digit_confusions(name: str) -> str:
    """Trong một cụm có chữ số, sửa chữ cái mà bộ đọc tiếng Việt hay nhầm với số: ó7AG thành 67AG."""
    fixed: list[str] = []
    for token in str(name or "").split(" "):
        if not any(ch.isascii() and ch.isdigit() for ch in token):
            fixed.append(token)
            continue
        chars = list(token)
        for index, ch in enumerate(chars):
            prev = chars[index - 1] if index > 0 else ""
            nxt = chars[index + 1] if index + 1 < len(chars) else ""
            prev_digit = prev.isascii() and prev.isdigit()
            next_digit = nxt.isascii() and nxt.isdigit()
            if ch in _SIX_LIKE and (prev_digit or next_digit):
                chars[index] = "6"
            elif ch in "Oo" and prev_digit and next_digit:
                chars[index] = "0"
            elif ch in "lI|" and prev_digit and next_digit:
                chars[index] = "1"
            elif ch == "S" and prev_digit and next_digit:
                chars[index] = "5"
            elif ch == "T" and nxt == "1" and (prev_digit or prev.islower()):
                chars[index] = "1"
        fixed.append("".join(chars))
    return " ".join(fixed)


def mark_weight(name: str) -> int:
    """Số dấu tiếng Việt trong tên. Bộ đọc hay làm rơi dấu, hiếm khi thêm dấu."""
    text = unicodedata.normalize("NFD", str(name or ""))
    return sum(1 for ch in text if unicodedata.combining(ch) or ch in "đĐ")


def choose_name(*reads: str) -> str:
    """Chọn tên giữa nhiều lần đọc, xếp theo độ tin từ cao xuống thấp."""
    present: list[str] = []
    for raw in reads:
        text = fix_digit_confusions(clean_name(raw))
        if text and not is_skipped(text):
            present.append(text)
    if not present:
        return ""
    groups: dict[str, list[str]] = {}
    for text in present:
        groups.setdefault(fold_marks(text), []).append(text)
    largest = max(groups.values(), key=len)
    if len(largest) >= 2:
        for text in largest:
            if sum(1 for other in largest if name_key(other) == name_key(text)) >= 2:
                return text
        return max(largest, key=lambda text: (mark_weight(text), -present.index(text)))
    first = present[0]
    for other in present[1:]:
        for longer, shorter in ((first, other), (other, first)):
            extra = longer[len(shorter) :].strip() if longer.startswith(shorter) else ""
            if extra and extra.replace(" ", "").isdigit():
                return longer
            long_tokens = longer.split()
            short_tokens = shorter.split()
            if len(long_tokens) == len(short_tokens) + 1 and len(long_tokens[-1]) <= 1 and long_tokens[:-1] == short_tokens:
                return shorter
    return first


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
    """Tên gần giống trong một video. Trùng hết chữ thì ghép; tên ngắn lệch dấu thì không."""
    a = _compact(left)
    b = _compact(right)
    if len(a) < 4 or len(b) < 4:
        return False
    if a == b:
        # "liên"/"lien" cùng chữ bỏ dấu nhưng là tên ngắn: chỉ nhận khi còn đúng dấu.
        return len(a) >= 6 or name_key(left) == name_key(right)
    if len(a) < 6 or len(b) < 6:
        return False
    tokens_a = _significant_tokens(left)
    tokens_b = _significant_tokens(right)
    if (len(tokens_a) <= 1 and len(a) < 8) or (len(tokens_b) <= 1 and len(b) < 8):
        return False
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


def same_person_name(left: str, right: str) -> bool:
    """Cùng một người theo tên đọc được: khớp chữ, khớp bỏ dấu, hoặc gần giống."""
    if not left or not right:
        return False
    if name_key(left) == name_key(right):
        return True
    if fold_marks(left) == fold_marks(right):
        return True
    return names_close(left, right)


def _handles_are_same(left: str, right: str) -> bool:
    """Hai chuỗi @ là một username bị đọc lệch, không phải hai người.

    @nggialinh.2014 và @nggialinh.2014C3 là một. @dangtam.3 và @khactam60 là hai.
    """
    a = clean_username(left).lstrip("@").casefold()
    b = clean_username(right).lstrip("@").casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) > len(b):
        a, b = b, a
    if len(a) >= 8 and b.startswith(a) and len(b) - len(a) <= 4:
        return True
    if min(len(a), len(b)) < 8 or abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    limit = 1 if min(len(a), len(b)) < 12 else 2
    return prev[-1] <= limit


def _handle_rank(username: str, name: str, counts: dict[str, int] | None = None) -> tuple[int, int, int, int]:
    """Ưu tiên @ khớp tên danh bạ, đọc nhiều lần, viết thường, đủ dài."""
    raw = clean_username(username).lstrip("@")
    match = 1 if name and handle_matches_name(username, name) else 0
    cleaned = clean_username(username)
    count = (counts or {}).get(username, (counts or {}).get(cleaned, 1))
    lower = 1 if raw.islower() else 0
    long_enough = 1 if len(raw) >= 5 else 0
    return (match, count, lower, long_enough)


def _prefer_handle(usernames: list[str], name: str, counts: dict[str, int] | None = None) -> str:
    """Chọn một @. Đuôi OCR dính thêm (@tenC3 sau @ten) thì giữ bản ngắn."""
    present: list[str] = []
    seen: set[str] = set()
    for item in usernames:
        cleaned = clean_username(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        present.append(cleaned)
    if not present:
        return ""
    best_rank = max(_handle_rank(item, name, counts) for item in present)
    tied = [item for item in present if _handle_rank(item, name, counts) == best_rank]
    folded = {item: item.lstrip("@").casefold() for item in tied}

    def penalty(item: str) -> tuple[int, int]:
        raw = folded[item]
        extended = any(
            other != raw and raw.startswith(other) and 0 < len(raw) - len(other) <= 4
            for other in folded.values()
        )
        return (1 if extended else 0, len(raw) if extended else 0)

    return min(tied, key=penalty)


def collapse_handles(usernames: list[str], name: str = "") -> list[str]:
    """Gom @ đọc lệch của cùng một hồ sơ. Giữ một @ cho mỗi người thật."""
    kept: list[str] = []
    for username in usernames:
        if not clean_username(username):
            continue
        for index, previous in enumerate(kept):
            if _handles_are_same(previous, username):
                kept[index] = _prefer_handle([previous, username], name)
                break
        else:
            kept.append(username)
    return kept


def handle_matches_name(username: str, name: str) -> bool:
    """Username chứa tên gộp (vd nvchien → @nvchien89, gialinh → @nggialinh.2014).

    Tên quá ngắn thì bỏ. Tên chỉ được đứng sau tối đa 3 ký tự, không khớp giữa handle.
    """
    handle = clean_username(username).lstrip("@").casefold()
    compact = _compact(name)
    if len(compact) < 5 or len(handle) < 5:
        return False
    if handle.startswith(compact) or compact.startswith(handle):
        return True
    if len(compact) < 6:
        return False
    at = handle.find(compact)
    return 0 < at <= 3


def pair_close_names(
    phones: list[tuple[str, str]],
    users: list[tuple[str, str]],
    handles: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Chỉ ghép khi một số khớp đúng một username và ngược lại.

    handles: id dòng → @username khi id không phải là chính username (ghép lại dữ liệu cũ).
    """
    phone_hits: dict[str, list[str]] = {}
    user_hits: dict[str, list[str]] = {}
    for phone_id, phone_name in phones:
        for user_id, user_name in users:
            handle = (handles or {}).get(user_id, user_id if str(user_id).startswith("@") else "")
            if not same_person_name(phone_name, user_name) and not (
                handle and handle_matches_name(handle, phone_name)
            ):
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
    shown: dict[str, set[int]] = {}
    visits = _walk(frames, contacts, phone_seen, phone_hits, shown)
    _pick_from_window(visits)
    _collapse_rare_digits(contacts, phone_hits, phone_seen, visits, shown)

    for index, visit in enumerate(visits):
        username = visit.username()
        if not username:
            continue
        profiles.setdefault(username, _Tally()).add(visit.name())
        user_seen.setdefault(username, index)

    phone_name = {phone: tally.best() for phone, tally in contacts.items()}
    phone_name = {phone: name for phone, name in phone_name.items() if name and not is_skipped(name)}
    user_name: dict[str, str] = {}
    for user, tally in profiles.items():
        name = tally.best()
        if name and is_skipped(name):
            continue
        # Tên hồ sơ trống vẫn giữ @ để xếp lỗi quét, không ghép vào số.
        user_name[user] = name

    used_phones: set[str] = set()
    used_users: set[str] = set()
    table = Table()
    _apply_visits(visits, phone_name, user_name, used_phones, used_users, table)
    _apply_names(phone_name, user_name, phone_seen, user_seen, used_phones, used_users, table)
    table.rows.sort(key=lambda row: phone_seen.get(row.phone, 0))
    table.unopened.sort(key=lambda row: phone_seen.get(row.phone, 0))
    table.review.sort(key=lambda row: (phone_seen.get(row.phone, 10**9), user_seen.get(row.username, 10**9)))
    return table


def phone_distance(left: str, right: str) -> int:
    """Số chữ số khác nhau giữa hai số cùng độ dài. Khác độ dài → 99."""
    if len(left) != len(right):
        return 99
    return sum(a != b for a, b in zip(left, right))


def _digit_hamming(left: str, right: str) -> int:
    return phone_distance(left, right)


def _phones_compatible(left: str, right: str, contacts: dict[str, _Tally], shown: dict[str, set[int]]) -> bool:
    """Hai số có thể là cùng một dòng OCR lệch: cùng tên, lệch ≤2 chữ số, không cùng khung."""
    if left == right:
        return False
    if _digit_hamming(left, right) > 2:
        return False
    if shown.get(left, set()) & shown.get(right, set()):
        return False
    left_name = contacts[left].best() if left in contacts else ""
    right_name = contacts[right].best() if right in contacts else ""
    if left_name and right_name and not same_person_name(left_name, right_name):
        return False
    return True


def _pick_canonical_phone(
    group: list[str],
    phone_hits: dict[str, int],
    phone_seen: dict[str, int],
) -> str:
    """Giữ số được đọc nhiều nhất; hòa thì lấy số gần các biến thể khác nhất (medoid)."""

    def medoid_score(candidate: str) -> int:
        return sum(_digit_hamming(candidate, other) for other in group)

    return min(
        group,
        key=lambda phone: (
            -phone_hits.get(phone, 0),
            medoid_score(phone),
            phone_seen.get(phone, 10**9),
            phone,
        ),
    )


def _collapse_rare_digits(
    contacts: dict[str, _Tally],
    phone_hits: dict[str, int],
    phone_seen: dict[str, int],
    visits: list[_Visit],
    shown: dict[str, set[int]],
) -> None:
    """Phương án B: số lệch 1–2 chữ số, cùng tên, chưa từng hiện cùng khung → gộp một số.

    Hai dòng cùng hiện trên một khung là hai người khác nhau, không bao giờ gộp.
    Biến thể chỉ thấy một lần cũng gộp vào cụm gần nhất.
    """
    phones = list(phone_hits)
    parent = {phone: phone for phone in phones}

    def find(phone: str) -> str:
        while parent[phone] != phone:
            parent[phone] = parent[parent[phone]]
            phone = parent[phone]
        return phone

    def unite(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for index, left in enumerate(phones):
        for right in phones[index + 1 :]:
            if _phones_compatible(left, right, contacts, shown):
                unite(left, right)

    groups: dict[str, list[str]] = {}
    for phone in phones:
        groups.setdefault(find(phone), []).append(phone)

    redirect: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        keep = _pick_canonical_phone(group, phone_hits, phone_seen)
        for phone in group:
            if phone != keep:
                redirect[phone] = keep

    for weak in sorted(redirect, key=lambda phone: phone_hits.get(phone, 0)):
        strong = redirect[weak]
        while strong in redirect:
            strong = redirect[strong]
        if strong == weak or weak not in phone_hits:
            continue
        weak_tally = contacts.pop(weak, None)
        if weak_tally is not None and weak_tally.best():
            strong_tally = contacts.setdefault(strong, _Tally())
            for _ in range(max(1, phone_hits.get(weak, 1))):
                strong_tally.add(weak_tally.best())
        phone_hits[strong] = phone_hits.get(strong, 0) + phone_hits.pop(weak, 0)
        phone_seen[strong] = min(phone_seen.get(strong, 10**9), phone_seen.pop(weak, 10**9))
        shown.setdefault(strong, set()).update(shown.pop(weak, set()))
        for visit in visits:
            if visit.phone == weak:
                visit.phone = strong
            if visit.window:
                visit.window = tuple((strong if phone == weak else phone, name) for phone, name in visit.window)


def _pick_from_window(visits: list[_Visit]) -> None:
    """Không thấy lần bấm: hồ sơ vừa mở thuộc một dòng đang hiện trong cùng khung trước đó.

    Chỉ nhận khi đúng một dòng trên màn hình khớp tên hồ sơ hoặc khớp @username.
    """
    for visit in visits:
        if visit.phone or not visit.window:
            continue
        profile_name = visit.name()
        username = visit.username()
        matches = {
            phone
            for phone, name in visit.window
            if profile_name and same_person_name(name, profile_name)
        }
        if len(matches) == 1:
            visit.phone = next(iter(matches))


# iPhone đôi khi animate mở hồ sơ chậm; nới cửa sổ ghép số đã bấm với @ vừa đọc.
# Hồ sơ mở chậm / animation Messenger dài: vẫn gắn với lần bấm trước đó.
TAP_TO_PROFILE_SECONDS = 8.0


def _cue_still_fresh(cue_at: float, profile_at: float) -> bool:
    if cue_at < 0 or profile_at < 0:
        return True
    return profile_at - cue_at <= TAP_TO_PROFILE_SECONDS


def _name_is_usable(name: str) -> bool:
    text = clean_name(name)
    return bool(text) and not is_skipped(text)


def _same_open(visit: _Visit, username: str, profile_name: str) -> bool:
    """Cùng một lần mở hồ sơ, không phải người kế tiếp.

    @nggialinh.2014 và @nggialinh.2014C3 là một. Hai tên khác nhau (@dangtam.3 rồi
    @khactam60) là hai người. Một khung không đọc được tên thì không tách.
    """
    current = visit.username()
    if current and username and _handles_are_same(current, username):
        return True
    known = visit.name()
    if not _name_is_usable(profile_name):
        return True
    if not _name_is_usable(known):
        return False
    return same_person_name(known, profile_name)


def _walk(
    frames: list[FrameObs],
    contacts: dict[str, _Tally],
    phone_seen: dict[str, int],
    phone_hits: dict[str, int],
    shown: dict[str, set[int]],
) -> list[_Visit]:
    phase = "none"
    armed = ""
    clear_run = 0
    tap_at: float | None = None
    held_by_tap = False
    window: tuple[tuple[str, str], ...] = ()
    visits: list[_Visit] = []
    open_visit: _Visit | None = None
    # P1: hàng đợi SĐT theo thời gian — không phụ thuộc khớp tên.
    cues: list[_PhoneCue] = []

    def close() -> None:
        nonlocal open_visit
        if open_visit and open_visit.username():
            visits.append(open_visit)
        open_visit = None

    def arm(phone: str, name: str, at: float, from_tap: bool) -> None:
        nonlocal armed, clear_run, tap_at, window, held_by_tap
        armed = phone
        clear_run = 0
        held_by_tap = from_tap or held_by_tap
        if from_tap:
            tap_at = at
            held_by_tap = True
        elif tap_at is None:
            tap_at = at
        if all(item[0] != phone for item in window):
            window = window + ((phone, name),)
        cue = _PhoneCue(phone=phone, at=at, window=window)
        if from_tap or not cues or cues[-1].phone != phone:
            cues.append(cue)
        else:
            cues[-1] = cue

    def take_phone_for_profile(profile_at: float) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Lấy SĐT đang armed hoặc cue gần nhất còn trong cửa sổ thời gian."""
        nonlocal armed, clear_run, tap_at, held_by_tap
        phone = ""
        visit_window: tuple[tuple[str, str], ...] = window if phase == "list" else ()
        if phase == "list" and armed:
            if tap_at is None or _cue_still_fresh(tap_at, profile_at):
                phone = armed
        if not phone:
            for cue in reversed(cues):
                if not _cue_still_fresh(cue.at, profile_at):
                    continue
                # Chỉ dùng lại cue chưa gắn visit nào.
                if any(item.phone == cue.phone for item in visits):
                    continue
                if open_visit and open_visit.phone == cue.phone:
                    continue
                phone = cue.phone
                visit_window = cue.window or visit_window
                break
        if phone:
            armed = ""
            clear_run = 0
            tap_at = None
            held_by_tap = False
            # Bỏ cue đã dùng (và cue cũ hơn cùng số).
            kept = [cue for cue in cues if cue.phone != phone]
            cues.clear()
            cues.extend(kept)
        return phone, visit_window

    def sighting(hit: ContactHit, frame_index: int) -> tuple[str, str]:
        phone = normalize_phone(hit.phone)
        name = clean_name(hit.name)
        if not phone or not name or is_skipped(name):
            return "", ""
        tally = contacts.get(phone)
        if tally is None:
            tally = _Tally()
            contacts[phone] = tally
            phone_seen[phone] = frame_index
        tally.add(name)
        phone_hits[phone] = phone_hits.get(phone, 0) + 1
        return phone, name

    for frame_index, frame in enumerate(frames):
        if frame.kind == "tap" and frame.contacts:
            close()
            phone, name = sighting(frame.contacts[0], frame_index)
            if phone:
                arm(phone, name, frame.at, from_tap=True)
            phase = "list"
            continue

        if frame.kind == "list" and frame.contacts:
            close()
            phase = "list"
            visible: set[str] = set()
            rows: list[tuple[str, str]] = []
            selected = ""
            multi = False
            for hit in frame.contacts:
                phone, name = sighting(hit, frame_index)
                if not phone:
                    continue
                visible.add(phone)
                rows.append((phone, name))
                shown.setdefault(phone, set()).add(frame_index)
                if hit.selected:
                    if selected:
                        multi = True
                    selected = phone
            window = tuple(rows)
            if multi:
                armed = ""
                clear_run = 0
                tap_at = None
                held_by_tap = False
            elif selected:
                selected_name = next((name for phone, name in rows if phone == selected), "")
                arm(selected, selected_name, frame.at, from_tap=False)
            elif armed and armed in visible:
                # P1: sau lần bấm (held_by_tap) giữ qua khung list chuyển cảnh.
                # Chỉ quên khi chọn bằng highlight rồi hiện lại list lạnh đủ 2 khung.
                if held_by_tap:
                    clear_run = 0
                else:
                    clear_run += 1
                    if clear_run >= 2:
                        stale = armed
                        armed = ""
                        clear_run = 0
                        tap_at = None
                        cues[:] = [cue for cue in cues if cue.phone != stale]
            continue

        username = clean_username(frame.profile_username)
        if frame.kind != "profile" or not username:
            continue
        profile_name = clean_name(frame.profile_name)
        if open_visit is None or phase != "profile":
            close()
            phone, visit_window = take_phone_for_profile(frame.at)
            open_visit = _Visit(phone=phone, window=visit_window, at=frame.at)
            phase = "profile"
        elif not _same_open(open_visit, username, profile_name):
            close()
            phone, visit_window = take_phone_for_profile(frame.at)
            open_visit = _Visit(phone=phone, window=visit_window, at=frame.at)
        if open_visit.at < 0:
            open_visit.at = frame.at
        open_visit.usernames[username] = open_visit.usernames.get(username, 0) + 1
        if profile_name:
            open_visit.names.add(profile_name)
    close()
    return visits


def _pair_orphan_visits_by_time(visits: list[_Visit]) -> None:
    """P1: @ mồ côi ngay sau một visit đã có SĐT — lấy đúng một số còn lại trên cùng list.

    Không đoán khi chỉ còn 1 số trên màn hình (dễ gắn nhầm người đã bỏ chọn).
    """
    used = {visit.phone for visit in visits if visit.phone}
    for index, visit in enumerate(visits):
        if visit.phone or not visit.username() or index == 0:
            continue
        prev = visits[index - 1]
        if not prev.phone:
            continue
        if visit.at >= 0 and prev.at >= 0 and visit.at - prev.at > TAP_TO_PROFILE_SECONDS:
            continue
        pool = list(visit.window) + list(prev.window)
        candidates = [
            phone
            for phone, _name in pool
            if phone and phone not in used and phone != prev.phone
        ]
        uniq = list(dict.fromkeys(candidates))
        if len(uniq) == 1:
            visit.phone = uniq[0]
            used.add(uniq[0])


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
        contact_name = phone_name[phone]
        profile_name = visit.name()
        # Chỉ ghép khi hồ sơ ngay sau mang cùng tên. Tên khác hoặc không đọc được tên thì bỏ.
        if not profile_name or not same_person_name(contact_name, profile_name):
            continue
        if phone not in grouped:
            grouped[phone] = []
            order.append(phone)
        if username not in grouped[phone]:
            grouped[phone].append(username)

    claimed_users: set[str] = set()
    for phone in order:
        raw = [item for item in grouped[phone] if item not in claimed_users]
        if not raw:
            continue
        contact_name = phone_name[phone]
        usernames = collapse_handles(raw, contact_name)
        if len(usernames) > 1:
            matched = [item for item in usernames if handle_matches_name(item, contact_name)]
            if len(matched) == 1:
                usernames = matched
        for item in raw:
            used_users.add(item)
            claimed_users.add(item)
        used_phones.add(phone)
        if len(usernames) > 1:
            for username in usernames:
                table.review.append(
                    Review(
                        phone,
                        contact_name,
                        user_name.get(username, ""),
                        username,
                        "một số mở nhiều hồ sơ",
                    )
                )
            continue
        username = usernames[0]
        profile_name = user_name.get(username, "")
        if profile_name and same_person_name(contact_name, profile_name):
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
        if not str(name or "").strip():
            # Không đọc được tên thì không biết cùng khách nào. Mỗi @ là một lỗi quét.
            table.review.append(
                Review("", "", "", username, "đã mở hồ sơ nhưng chưa thấy số")
            )
            continue
        users_by_name.setdefault(name_key(name), []).append(username)

    for key in phones_by_name:
        phones_by_name[key].sort(key=lambda phone: phone_seen.get(phone, 0))
    for key in users_by_name:
        users_by_name[key].sort(key=lambda username: user_seen.get(username, 0))

    # Số và @ còn lại không đứng liền nhau. Không ghép thêm theo tên ở chỗ khác trong video.
    for key in set(phones_by_name) | set(users_by_name):
        _emit_unpaired(
            phones_by_name.get(key, []),
            users_by_name.get(key, []),
            phone_name,
            user_name,
            table,
        )


def _pair_by_handle(phones: list[tuple[str, str]], users: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Một số khớp đúng một @username qua phần tên trong handle, và ngược lại."""
    phone_hits: dict[str, list[str]] = {}
    user_hits: dict[str, list[str]] = {}
    for phone_id, phone_name in phones:
        for user_id, _user_name in users:
            if not handle_matches_name(user_id, phone_name):
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


def _emit_unpaired(
    phones: list[str],
    usernames: list[str],
    phone_name: dict[str, str],
    user_name: dict[str, str],
    table: Table,
) -> None:
    if len(phones) == 1 and usernames:
        contact_name = phone_name[phones[0]]
        collapsed = collapse_handles(usernames, contact_name)
        if len(collapsed) > 1:
            matched = [item for item in collapsed if handle_matches_name(item, contact_name)]
            if len(matched) == 1:
                collapsed = matched
            else:
                # Cùng một tên: các @ lệch của một hồ sơ, giữ @ khớp tên nhất.
                collapsed = [_prefer_handle(collapsed, contact_name)]
        usernames = collapsed
    elif usernames:
        collapsed = collapse_handles(usernames, "")
        if not phones and len(collapsed) > 1:
            sample = user_name.get(collapsed[0], "")
            collapsed = [_prefer_handle(collapsed, sample)]
        usernames = collapsed
    if len(phones) == 1 and len(usernames) == 1:
        # Cùng tên nhưng không phải cặp liền nhau: không ghép.
        phone = phones[0]
        username = usernames[0]
        table.unopened.append(Unopened(phone, phone_name[phone]))
        table.review.append(
            Review("", "", user_name.get(username, ""), username, "đã mở hồ sơ nhưng chưa thấy số")
        )
        return
    if len(phones) == 1 and not usernames:
        table.unopened.append(Unopened(phones[0], phone_name[phones[0]]))
        return
    if not phones and len(usernames) == 1:
        username = usernames[0]
        table.review.append(Review("", "", user_name.get(username, ""), username, "đã mở hồ sơ nhưng chưa thấy số"))
        return
    if usernames:
        for phone in phones:
            table.review.append(Review(phone, phone_name[phone], "", "", "trùng tên, không tự ghép"))
        for username in usernames:
            table.review.append(Review("", "", user_name.get(username, ""), username, "trùng tên, không tự ghép"))
        return
    for phone in phones:
        table.unopened.append(Unopened(phone, phone_name[phone]))
