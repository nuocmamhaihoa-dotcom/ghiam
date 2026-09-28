"""Ghép một người từ danh bạ và từ hồ sơ khi cùng một tên đã được nhìn thấy."""

from __future__ import annotations

import re

_HANDLE = re.compile(r"^@?[A-Za-z0-9._]{2,30}$")


def name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def clean_name(value: str) -> str:
    return " ".join(str(value or "").split())[:80]


def clean_username(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text or not _HANDLE.match(text.lstrip("@")):
        return ""
    if not text.startswith("@"):
        text = "@" + text
    return text[:40]


def fold_sightings(stored: list[dict[str, str]], items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Điền vào dòng đã có. Không ghi đè tên danh bạ hoặc username đã lưu."""
    by_key: dict[str, dict[str, str]] = {}
    for row in stored:
        key = row.get("nameKey") or name_key(row.get("name") or "")
        if not key:
            continue
        by_key[key] = {
            "nameKey": key,
            "name": clean_name(row.get("name") or ""),
            "contactName": clean_name(row.get("contactName") or ""),
            "username": clean_username(row.get("username") or ""),
        }
    for item in items[:40]:
        kind = item.get("kind") or ""
        name = clean_name(item.get("name") or "")
        key = name_key(name)
        if not key or kind not in ("contact", "profile"):
            continue
        current = by_key.get(key) or {"nameKey": key, "name": name, "contactName": "", "username": ""}
        if not current["name"]:
            current["name"] = name
        if kind == "contact":
            contact_name = clean_name(item.get("contactName") or "")
            if contact_name and not current["contactName"]:
                current["contactName"] = contact_name
        else:
            username = clean_username(item.get("username") or "")
            if username and not current["username"]:
                current["username"] = username
        by_key[key] = current
    return list(by_key.values())


def complete_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("contactName") and row.get("username")]
