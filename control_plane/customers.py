"""Kho khách: chuẩn hoá, nhận diện trùng, bổ sung ô trống, không ghi đè ô đã có."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

FIELDS = ("name", "phone", "address", "facebookUid", "tiktokId")

_SCI = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
_DIGITS = re.compile(r"^\d{5,20}$")
_PHONE_BREAK = set(" -().\t")


def collapse(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def expand_scientific(value: str) -> str:
    text = collapse(value)
    if not text or not _SCI.fullmatch(text):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer() and abs(number) < 1e16:
        return str(int(number))
    return text


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
    if digits.startswith("84") and len(digits) == 11:
        return "0" + digits[2:]
    if leading_plus and digits.startswith("84") and len(digits) >= 10:
        return "0" + digits[2:]
    return ("+" + digits) if leading_plus and not digits.startswith("84") else digits


def clean_address(value: Any) -> str:
    return collapse(value)[:300]


def address_key(value: str) -> str:
    return clean_address(value).casefold()


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
    "facebookUid": clean_facebook_uid,
    "tiktokId": clean_tiktok_id,
}

_KEYS = {
    "name": name_key,
    "phone": clean_phone,
    "address": address_key,
    "facebookUid": facebook_key,
    "tiktokId": tiktok_key,
}


def clean_row(raw: dict[str, Any] | None) -> dict[str, str]:
    source = raw or {}
    return {field: _CLEANERS[field](source.get(field) or "") for field in FIELDS}


def has_data(row: dict[str, str]) -> bool:
    return any(row.get(field) for field in FIELDS)


def identity_keys(row: dict[str, str]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for field in FIELDS:
        value = row.get(field) or ""
        if not value:
            continue
        keys.append((field, _KEYS[field](value)))
    return keys


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
    """Ghép khách mới vào kho. Ô đã có giữ nguyên, ô trống được điền thêm."""
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
        if existing and changed:
            updated += 1
        elif existing and had_incoming:
            unchanged += 1
            merged["updatedAt"] = members[0].get("updatedAt") or now
        elif existing:
            merged["updatedAt"] = members[0].get("updatedAt") or now
        else:
            created += 1
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


def public_customer(row: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "phone": str(row.get("phone") or ""),
        "address": str(row.get("address") or ""),
        "facebookUid": str(row.get("facebookUid") or ""),
        "tiktokId": str(row.get("tiktokId") or ""),
        "createdAt": str(row.get("createdAt") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def matches_query(row: dict[str, Any], query: str) -> bool:
    needle = collapse(query).casefold()
    if not needle:
        return True
    haystacks = [
        row.get("name") or "",
        row.get("phone") or "",
        row.get("address") or "",
        row.get("facebookUid") or "",
        row.get("tiktokId") or "",
    ]
    return any(needle in collapse(item).casefold() for item in haystacks)
