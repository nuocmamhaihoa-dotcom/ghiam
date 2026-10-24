"""Kho khách: chuẩn hoá, nhận diện trùng, bổ sung ô trống, không ghi đè ô đã có."""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from control_plane import db
from control_plane.vietnam_places import fold_vietnamese, has_address_keyword, has_vietnam_place

FIELDS = (
    "name",
    "phone",
    "address",
    "adminAddress",
    "orderContent",
    "codAmount",
    "facebookUid",
    "tiktokId",
)
LINE_FIELDS = ("address", "adminAddress", "orderContent")
_COD_THOUSAND = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*k\b")
_COD_MILLION = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*tr(?:ieu)?\b")

_SCI = re.compile(r"^([+-]?)(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$")
_DIGITS = re.compile(r"^\d{5,20}$")
_PHONE_BREAK = set(" -().\t")


def collapse(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def expand_scientific(value: str) -> str:
    """Giữ nguyên chữ số UID/SĐT trong dạng 1.00e+15, không đi qua float."""
    text = collapse(value)
    if not text:
        return text
    match = _SCI.fullmatch(text)
    if not match:
        return text
    sign, whole, frac, exp_text = match.groups()
    digits = whole + (frac or "")
    if not digits:
        return text
    shift = int(exp_text) - len(frac or "")
    if shift < 0:
        return text
    body = (digits + ("0" * shift)).lstrip("0") or "0"
    if len(body) > 20:
        return text
    return ("-" if sign == "-" else "") + body


def clean_name(value: Any) -> str:
    return collapse(value)[:120]


def name_key(value: str) -> str:
    return clean_name(value).casefold()


def clean_phone(value: Any) -> str:
    text = collapse(value)
    if text.lower().startswith("tel:"):
        text = text[4:].strip()
    if not text:
        return ""
    digits = ""
    leading_plus = False
    started = False
    for character in text:
        if not started and character == "+":
            leading_plus = True
            started = True
            continue
        started = True
        if character.isdigit():
            digits += character
            continue
        if character in _PHONE_BREAK:
            continue
        break
    if not (8 <= len(digits) <= 15):
        return ""
    if digits.startswith("0084") and len(digits) >= 13:
        rest = digits[4:]
        return rest if rest.startswith("0") else "0" + rest
    if digits.startswith("084") and len(digits) >= 12:
        rest = digits[3:]
        return rest if rest.startswith("0") else "0" + rest
    if digits.startswith("84") and len(digits) >= 11:
        rest = digits[2:]
        return rest if rest.startswith("0") else "0" + rest
    if leading_plus and digits.startswith("84") and len(digits) >= 10:
        return "0" + digits[2:]
    return ("+" + digits) if leading_plus and not digits.startswith("84") else digits


def phone_lookup_keys(value: Any) -> list[str]:
    """Các dạng cùng một số Việt Nam: 0xx, 84xx, thiếu số 0."""
    phone = clean_phone(value)
    if not phone:
        return []
    keys = [phone]
    if phone.startswith("0") and len(phone) >= 10:
        rest = phone[1:]
        keys.extend([rest, "84" + rest, "+84" + rest, "084" + rest, "0084" + rest])
    elif len(phone) == 9 and phone[:1] in "35789":
        keys.extend(["0" + phone, "84" + phone, "+84" + phone])
    seen: set[str] = set()
    unique: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def split_address_lines(value: Any) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.split("\n"):
        line = collapse(raw)
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line[:200])
        if len(lines) >= 12:
            break
    return lines


def join_address_lines(*parts: Any) -> str:
    return "\n".join(split_address_lines("\n".join(str(part or "") for part in parts)))


def clean_address(value: Any) -> str:
    return join_address_lines(value)[:800]


def address_key(value: str) -> str:
    return " | ".join(line.casefold() for line in split_address_lines(value))


def clean_admin_address(value: Any) -> str:
    return join_address_lines(value)[:800]


def clean_order_content(value: Any) -> str:
    return join_address_lines(value)[:800]


def _cod_number(text: str) -> str:
    try:
        return str(int(round(float(text.replace(",", ".")))))
    except ValueError:
        return ""


def clean_cod_amount(value: Any) -> str:
    text = collapse(value)
    if not text:
        return ""
    thousand = _COD_THOUSAND.search(text)
    if thousand:
        base = _cod_number(thousand.group(1))
        return str(int(base) * 1000) if base else ""
    million = _COD_MILLION.search(text)
    if million:
        amount = million.group(1).replace(",", ".")
        try:
            return str(int(round(float(amount) * 1_000_000)))
        except ValueError:
            return ""
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return ""
    if digits.startswith("0") and 9 <= len(digits) <= 15:
        return ""
    if len(digits) > 12:
        return ""
    return str(int(digits))


def clean_facebook_uid(value: Any) -> str:
    text = expand_scientific(collapse(value))
    if not text:
        return ""
    lowered = text.casefold()
    if "facebook.com" in lowered or "fb.com" in lowered or lowered.startswith("http"):
        parsed = urlparse(text if "://" in text else "https://" + text)
        query_id = parse_qs(parsed.query).get("id", [""])[0]
        if _DIGITS.fullmatch(query_id):
            return query_id
        path = parsed.path.rstrip("/").split("/")[-1]
        if _DIGITS.fullmatch(path):
            return path
    if _DIGITS.fullmatch(text):
        return text
    return text[:80]


def facebook_key(value: str) -> str:
    return clean_facebook_uid(value).casefold()


def clean_tiktok_id(value: Any) -> str:
    text = expand_scientific(collapse(value))
    if not text:
        return ""
    lowered = text.casefold()
    if "tiktok.com" in lowered:
        parsed = urlparse(text if "://" in text else "https://" + text)
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            text = parts[0]
    text = text.lstrip("@")
    return text[:80]


def tiktok_key(value: str) -> str:
    return clean_tiktok_id(value).casefold()


_CLEANERS = {
    "name": clean_name,
    "phone": clean_phone,
    "address": clean_address,
    "adminAddress": clean_admin_address,
    "orderContent": clean_order_content,
    "codAmount": clean_cod_amount,
    "facebookUid": clean_facebook_uid,
    "tiktokId": clean_tiktok_id,
}

_KEYS = {
    "name": name_key,
    "phone": clean_phone,
    "address": address_key,
    "adminAddress": address_key,
    "orderContent": address_key,
    "codAmount": clean_cod_amount,
    "facebookUid": facebook_key,
    "tiktokId": tiktok_key,
}


def clean_row(raw: dict[str, Any] | None) -> dict[str, str]:
    source = raw or {}
    return {field: _CLEANERS[field](source.get(field) or "") for field in FIELDS}


def has_data(row: dict[str, str]) -> bool:
    return any(row.get(field) for field in FIELDS)


def identity_keys(row: dict[str, str]) -> list[tuple[str, str]]:
    """Chỉ gộp khi trùng số điện thoại. Không có số thì mỗi dòng là một khách."""
    phone = clean_phone(row.get("phone") or "")
    if not phone:
        return []
    return [("phone", phone)]


def _new_id() -> str:
    return uuid.uuid4().hex


def _as_customer(raw: dict[str, Any], existing: bool) -> dict[str, Any]:
    cleaned = clean_row(raw)
    return {
        "id": str(raw.get("id") or "") or _new_id(),
        "existing": existing,
        "createdAt": str(raw.get("createdAt") or ""),
        "updatedAt": str(raw.get("updatedAt") or ""),
        **cleaned,
    }


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            self.parent[root_left] = root_right
            return
        if self.rank[root_left] > self.rank[root_right]:
            self.parent[root_right] = root_left
            return
        self.parent[root_right] = root_left
        self.rank[root_left] += 1


def _pick(members: list[dict[str, Any]], field: str) -> str:
    if field in LINE_FIELDS:
        existing = [str(row.get(field) or "") for row in members if row["existing"] and row.get(field)]
        if existing:
            return join_address_lines(*existing)
        return join_address_lines(*(str(row.get(field) or "") for row in members if not row["existing"]))
    for row in members:
        if row["existing"] and row.get(field):
            return str(row[field])
    for row in members:
        if not row["existing"] and row.get(field):
            return str(row[field])
    return ""


def _merge_group(members: list[dict[str, Any]], now: str) -> dict[str, Any]:
    existing = [row for row in members if row["existing"]]
    incoming = [row for row in members if not row["existing"]]
    seed = existing[0] if existing else incoming[0]
    merged = {
        "id": seed["id"] if existing else _new_id(),
        "createdAt": seed.get("createdAt") or now,
        "updatedAt": now,
        "existing": bool(existing),
    }
    for field in FIELDS:
        merged[field] = _pick(members, field)
    changed = False
    if existing:
        before = {field: existing[0].get(field) or "" for field in FIELDS}
        if any(merged[field] != before[field] for field in FIELDS):
            changed = True
        if len(existing) > 1:
            changed = True
    else:
        changed = True
    merged["changed"] = changed
    merged["absorbed"] = max(0, len(existing) - 1)
    return merged


def fold_customers(
    stored: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    now: str,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Ghép khách mới vào kho khi trùng số điện thoại.

    Ô đã có giữ nguyên, ô trống được điền thêm. Không có số, hoặc khác số,
    thì mỗi dòng là một khách — không gộp vì tên, địa chỉ, Facebook hay TikTok.
    """
    kept_incoming: list[dict[str, Any]] = []
    skipped = 0
    for raw in incoming:
        row = clean_row(raw)
        if not has_data(row):
            skipped += 1
            continue
        kept_incoming.append(_as_customer(row, False))
    records = [_as_customer(row, True) for row in stored] + kept_incoming
    union = _UnionFind(len(records))
    buckets: dict[tuple[str, str], int] = {}
    for index, row in enumerate(records):
        for key in identity_keys(row):
            seen = buckets.get(key)
            if seen is None:
                buckets[key] = index
            else:
                union.union(seen, index)
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, row in enumerate(records):
        groups.setdefault(union.find(index), []).append(row)
    folded: list[dict[str, Any]] = []
    created = 0
    updated = 0
    unchanged = 0
    absorbed = 0
    for members in groups.values():
        merged = _merge_group(members, now)
        absorbed += int(merged.pop("absorbed"))
        changed = bool(merged.pop("changed"))
        existing = bool(merged.pop("existing"))
        had_incoming = any(not row["existing"] for row in members)
        existing_updated = next((row.get("updatedAt") or "" for row in members if row["existing"]), "")
        if existing and changed:
            updated += 1
            merged["write"] = True
        elif existing and had_incoming:
            unchanged += 1
            merged["updatedAt"] = existing_updated or now
            merged["write"] = False
        elif existing:
            merged["updatedAt"] = existing_updated or now
            merged["write"] = False
        else:
            created += 1
            merged["write"] = True
        folded.append(merged)
    folded.sort(key=lambda row: (row.get("updatedAt") or "", row.get("createdAt") or ""), reverse=True)
    removed_ids = sorted(
        {row["id"] for row in records if row["existing"]} - {row["id"] for row in folded if row.get("id")}
    )
    return (
        folded,
        {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
            "absorbed": absorbed,
            "incoming": len(incoming),
            "kept": len(kept_incoming),
            "total": len(folded),
            "removed": len(removed_ids),
        },
        removed_ids,
    )


def merge_existing_with_incoming(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
    now: str,
) -> tuple[dict[str, Any], list[str], str]:
    """Ghép một dòng file vào các khách cùng SĐT. Ô đã có giữ, ô trống điền."""
    incoming_row = _as_customer(incoming, False)
    if not existing:
        incoming_row["createdAt"] = now
        incoming_row["updatedAt"] = now
        incoming_row["write"] = True
        incoming_row.pop("existing", None)
        return incoming_row, [], "created"
    members = [_as_customer(row, True) for row in existing] + [incoming_row]
    merged = _merge_group(members, now)
    extra_ids = [str(row.get("id") or "") for row in existing if str(row.get("id") or "") != merged["id"]]
    extra_ids = [item for item in extra_ids if item]
    changed = bool(merged.pop("changed"))
    merged.pop("existing", None)
    merged.pop("absorbed", None)
    if changed:
        merged["write"] = True
        return merged, extra_ids, "updated"
    merged["write"] = False
    if extra_ids:
        merged["write"] = True
        return merged, extra_ids, "updated"
    return merged, extra_ids, "unchanged"


def ingest_customers(
    db_path: Any,
    incoming: Iterator[dict[str, Any]],
    now: str,
    flush_every: int = 400,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Nạp từng dòng vào SQLite. Không tải cả kho vào RAM. Ghi từng lô để không mất dở."""
    created = 0
    updated = 0
    unchanged = 0
    skipped = 0
    absorbed = 0
    incoming_count = 0
    kept = 0
    pending: dict[str, dict[str, Any]] = {}
    pending_phone: dict[str, str] = {}
    pending_deletes: list[str] = []
    preview: list[dict[str, Any]] = []
    flush_every = max(50, int(flush_every))

    def flush(conn: Any) -> None:
        if not pending and not pending_deletes:
            return
        db.write_customers(conn, list(pending.values()), pending_deletes)
        conn.commit()
        pending.clear()
        pending_phone.clear()
        pending_deletes.clear()

    db.init_db(db_path)
    conn = db.connect(db_path)
    try:
        for raw in incoming:
            incoming_count += 1
            row = clean_row(raw)
            if not has_data(row):
                skipped += 1
                continue
            repaired, changed = repair_customer_row(row)
            if changed:
                row = clean_row(repaired)
            if not has_data(row):
                skipped += 1
                continue
            kept += 1
            phone = clean_phone(row.get("phone") or "")
            if phone:
                pending_id = pending_phone.get(phone)
                if pending_id and pending_id in pending:
                    existing = [pending[pending_id]]
                else:
                    existing = db.customers_by_phone(conn, phone)
                merged, extra_ids, kind = merge_existing_with_incoming(existing, row, now)
                for extra_id in extra_ids:
                    pending.pop(extra_id, None)
                    pending_deletes.append(extra_id)
                    absorbed += 1
                pending[merged["id"]] = merged
                pending_phone[phone] = merged["id"]
            else:
                merged, extra_ids, kind = merge_existing_with_incoming([], row, now)
                pending[merged["id"]] = merged
            if kind == "created":
                created += 1
            elif kind == "updated":
                updated += 1
            else:
                unchanged += 1
            if merged.get("write") and len(preview) < 200:
                preview.append(merged)
            if len(pending) >= flush_every:
                flush(conn)
        flush(conn)
        total = int(conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"])
    finally:
        conn.close()
    return (
        {
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
            "absorbed": absorbed,
            "incoming": incoming_count,
            "kept": kept,
            "total": total,
            "removed": absorbed,
        },
        preview,
    )


def public_customer(row: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "phone": str(row.get("phone") or ""),
        "address": str(row.get("address") or ""),
        "adminAddress": str(row.get("adminAddress") or ""),
        "orderContent": str(row.get("orderContent") or ""),
        "codAmount": str(row.get("codAmount") or ""),
        "facebookUid": str(row.get("facebookUid") or ""),
        "tiktokId": str(row.get("tiktokId") or ""),
        "createdAt": str(row.get("createdAt") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


MAX_LOOKUP = 1_000_000
_SPLIT_FIELDS = re.compile(r"[,;\t|]+")


def phones_from_text(text: str, limit: int = MAX_LOOKUP) -> tuple[list[str], dict[str, int]]:
    """Lấy số điện thoại theo thứ tự danh sách, bỏ số trùng và dòng không có số."""
    phones: list[str] = []
    seen: set[str] = set()
    raw = 0
    invalid = 0
    duplicate = 0
    truncated = False
    for line in str(text or "").replace("\ufeff", "").splitlines():
        stripped = collapse(line)
        if not stripped or stripped.startswith("#"):
            continue
        raw += 1
        found: list[str] = []
        whole = clean_phone(stripped)
        if whole:
            found.append(whole)
        for piece in _SPLIT_FIELDS.split(stripped):
            for token in piece.split():
                phone = clean_phone(token)
                if phone and phone not in found:
                    found.append(phone)
        if not found:
            invalid += 1
            continue
        for phone in found:
            if phone in seen:
                duplicate += 1
                continue
            if len(phones) >= limit:
                truncated = True
                break
            seen.add(phone)
            phones.append(phone)
        if truncated:
            break
    return phones, {
        "raw": raw,
        "unique": len(phones),
        "invalid": invalid,
        "duplicate": duplicate,
        "truncated": int(truncated),
    }


def lookup_by_phones(
    stored: list[dict[str, Any]],
    phones: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Đối chiếu list số với kho. Giữ đúng thứ tự số người dùng đưa vào."""
    index: dict[str, list[dict[str, str]]] = {}
    for row in stored:
        public = public_customer(row)
        keys = phone_lookup_keys(row.get("phone") or "")
        if not keys:
            continue
        for key in keys:
            bucket = index.setdefault(key, [])
            if any(item.get("id") == public["id"] for item in bucket):
                continue
            bucket.append(public)
    items: list[dict[str, Any]] = []
    found = 0
    missing = 0
    for phone in phones:
        matches: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for key in phone_lookup_keys(phone) or [phone]:
            for customer in index.get(key) or []:
                customer_id = str(customer.get("id") or "")
                if customer_id in seen_ids:
                    continue
                seen_ids.add(customer_id)
                matches.append(customer)
        if not matches:
            missing += 1
            items.append({"query": phone, "found": False, "customer": None})
            continue
        found += 1
        for customer in matches:
            items.append({"query": phone, "found": True, "customer": customer})
    return items, {
        "queried": len(phones),
        "found": found,
        "missing": missing,
        "hits": found,
    }


_GENDER_KEYS = frozenset(
    {
        "female",
        "male",
        "femelelle",
        "femelele",
        "femelle",
        "femele",
        "gioi tinh",
        "nu",
    }
)
_DATE_LINE = re.compile(r"^\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?$")
_FB_HOST = re.compile(r"(?i)(?:facebook\.com|fb\.com|fb\.me)")
_LETTER_OR_MARK = re.compile(r"[^\W\d_]", re.UNICODE)


def is_gender_value(value: str) -> bool:
    return fold_vietnamese(value) in _GENDER_KEYS


def _is_date_line(value: str) -> bool:
    return bool(_DATE_LINE.fullmatch(collapse(value)))


def _is_uid_digits(value: str) -> bool:
    text = collapse(value)
    if not text or not text.isdigit() or clean_phone(text):
        return False
    return 5 <= len(text) <= 20


def _is_name_line(value: str) -> bool:
    text = collapse(value)
    if not text or is_gender_value(text) or _is_date_line(text) or _FB_HOST.search(text):
        return False
    if clean_phone(text) or _is_uid_digits(text) or any(character.isdigit() for character in text):
        return False
    if has_vietnam_place(text) or has_address_keyword(text):
        return False
    words = fold_vietnamese(text).split()
    return bool(1 <= len(words) <= 7 and _LETTER_OR_MARK.search(text))


def _is_address_line(value: str) -> bool:
    text = collapse(value)
    if not text or is_gender_value(text) or _is_date_line(text) or _FB_HOST.search(text):
        return False
    if clean_phone(text) or _is_uid_digits(text):
        return False
    return has_vietnam_place(text) or has_address_keyword(text)


def _is_facebook_dump(name: str, lines: list[str]) -> bool:
    if is_gender_value(name):
        return True
    return any(is_gender_value(line) or _FB_HOST.search(line) for line in lines)


_DUMP_URL = re.compile(r"https?://\S+|(?:facebook\.com|fb\.com|fb\.me)/\S*", re.I)
_DUMP_GENDER = re.compile(
    r"(?i)\b(?:female|male|femelelle|femelele|femelle|femele)\b"
)
_DUMP_DATE = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?\b")
_DUMP_DIGITS = re.compile(r"\b\d{5,20}\b")


def _dump_lines(address: str) -> list[str]:
    text = str(address or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _DUMP_URL.sub(lambda match: f"\n{match.group(0)}\n", text)
    text = _DUMP_GENDER.sub(lambda match: f"\n{match.group(0)}\n", text)
    text = _DUMP_DATE.sub(lambda match: f"\n{match.group(0)}\n", text)
    text = _DUMP_DIGITS.sub(lambda match: f"\n{match.group(0)}\n", text)
    return split_address_lines(text)


def repair_customer_row(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Đưa tên, SĐT, UID Facebook ra đúng cột; bỏ giới tính/ngày/link khỏi địa chỉ."""
    current = {field: str(row.get(field) or "") for field in FIELDS}
    lines = _dump_lines(current["address"])
    if not _is_facebook_dump(current["name"], lines):
        return dict(row), False
    found_name = ""
    found_phone = clean_phone(current["phone"])
    found_uid = clean_facebook_uid(current["facebookUid"])
    kept_address: list[str] = []
    past_gender = False
    for line in lines:
        if is_gender_value(line):
            past_gender = True
            continue
        if _is_date_line(line):
            continue
        if _FB_HOST.search(line):
            uid = clean_facebook_uid(line)
            if uid and not found_uid:
                found_uid = uid
            continue
        phone = clean_phone(line)
        if phone:
            if not found_phone:
                found_phone = phone
            continue
        if _is_uid_digits(line):
            if not found_uid:
                found_uid = line
            continue
        if not past_gender and _is_name_line(line):
            if not found_name:
                found_name = clean_name(line)
            continue
        if _is_address_line(line) or past_gender:
            kept_address.append(line)
            continue
        if _is_name_line(line):
            continue
        kept_address.append(line)
    name = current["name"]
    if is_gender_value(name) or not collapse(name):
        name = found_name
    if is_gender_value(name):
        name = ""
    fixed = dict(row)
    fixed.update(current)
    fixed["name"] = name
    fixed["phone"] = found_phone
    fixed["address"] = join_address_lines(*kept_address)
    fixed["facebookUid"] = found_uid
    changed = any(str(fixed.get(field) or "") != current[field] for field in FIELDS)
    return fixed, changed


def repair_customer_rows(rows: list[dict[str, Any]]) -> int:
    fixed_count = 0
    for index, row in enumerate(rows):
        fixed, changed = repair_customer_row(row)
        if not changed:
            continue
        fixed["write"] = True
        rows[index] = fixed
        fixed_count += 1
    return fixed_count


def repair_stored_customers(db_path: Any) -> int:
    from control_plane import db

    rows = db.list_customers(db_path)
    fixed_count = repair_customer_rows(rows)
    dirty = [row for row in rows if row.get("write")]
    if dirty:
        db.replace_customers(db_path, dirty, [])
    return fixed_count


def matches_query(row: dict[str, Any], query: str) -> bool:
    needle = collapse(query).casefold()
    if not needle:
        return True
    haystacks = [
        row.get("name") or "",
        row.get("phone") or "",
        row.get("address") or "",
        row.get("adminAddress") or "",
        row.get("orderContent") or "",
        row.get("codAmount") or "",
        row.get("facebookUid") or "",
        row.get("tiktokId") or "",
    ]
    return any(needle in collapse(item).casefold() for item in haystacks)
