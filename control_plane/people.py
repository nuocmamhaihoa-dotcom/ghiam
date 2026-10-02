"""Ghép một người từ danh bạ và từ hồ sơ khi cùng một tên đã được nhìn thấy."""

from __future__ import annotations

import re
import unicodedata

_HANDLE = re.compile(r"^@?[A-Za-z0-9._]{5,30}$")
_HANDLE_FIND = re.compile(r"@[A-Za-z0-9._]{5,30}")
_HANDLE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._")


def name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def fold_name(name: str) -> str:
    """So khớp khi OCR bỏ dấu. Khóa lưu trong bảng vẫn giữ dấu."""
    normalized = unicodedata.normalize("NFD", name)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.casefold().replace("đ", "d").split())


def mark_count(name: str) -> int:
    """Số chữ có dấu hoặc đ trong tên. OCR bỏ dấu nhiều hơn nhiều so với thêm dấu, nên nhiều dấu hơn thường là đúng hơn."""
    return sum(1 for char in unicodedata.normalize("NFC", str(name or "")) if fold_name(char) != char.casefold())


def _name_char_ok(char: str) -> bool:
    if char.isspace() or char in "-'":
        return True
    if char.isalpha() or char.isdigit():
        return True
    return unicodedata.category(char) == "Mn"


def clean_name(value: str) -> str:
    """Chỉ giữ chữ (kể cả dấu Việt), số, khoảng, gạch. Ký tự lạ bỏ."""
    kept = "".join(char if _name_char_ok(char) else " " for char in str(value or ""))
    return " ".join(kept.split())[:80]


def clean_username(value: str) -> str:
    """Chỉ nhận @ và [A-Za-z0-9._]. Ký tự lạ giữa handle thì bỏ cả tài khoản."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    found = _HANDLE_FIND.search(text)
    if found:
        return found.group(0)[:40]
    core = text.lstrip("@")
    if not core or any(char not in _HANDLE_CHARS for char in core):
        return ""
    if not _HANDLE.match(core):
        return ""
    return ("@" + core)[:40]


def sighting_adds(row: dict[str, str] | None, item: dict[str, str]) -> bool:
    """Chỉ nhận lần nhìn thấy khi còn một cột trống. Cột đã có thì không lưu thêm."""
    kind = item.get("kind") or ""
    if kind == "contact":
        contact_name = clean_name(item.get("contactName") or "")
        if not contact_name:
            return False
        return not (row and row.get("contactName"))
    if kind == "profile":
        username = clean_username(item.get("username") or "")
        if not username:
            return False
        return not (row and row.get("username"))
    return False


_ROW_LIMIT = 200
_SIGHTING_LIMIT = 400


def complete_sightings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mỗi dòng đủ ba cột thành một lần danh bạ và một lần hồ sơ."""
    items: list[dict[str, str]] = []
    for row in rows[:_ROW_LIMIT]:
        name = clean_name(row.get("name") or "")
        contact_name = clean_name(row.get("contactName") or "")
        username = clean_username(row.get("username") or "")
        if len(name) < 2 or not contact_name or not username:
            continue
        if fold_name(contact_name) == fold_name(name):
            continue
        items.append({"kind": "contact", "name": name, "contactName": contact_name, "username": ""})
        items.append({"kind": "profile", "name": name, "contactName": "", "username": username})
    return items


def apply_novel(
    stored: list[dict[str, str]], items: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int]:
    """Ghép lần lượt những mục còn mới. Mục đã có trong dòng thì bỏ qua."""
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
    added = 0
    for item in items[:_SIGHTING_LIMIT]:
        key = name_key(clean_name(item.get("name") or ""))
        if not sighting_adds(by_key.get(key), item):
            continue
        updated = fold_sightings(list(by_key.values()), [item])
        by_key = {row["nameKey"]: row for row in updated}
        added += 1
    return list(by_key.values()), added


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
    for item in items[:_SIGHTING_LIMIT]:
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


_PROFILE_LABELS = ("TikTok", "Facebook", "Instagram", "Zalo", "Danh bạ", "Đã follow")


def profile_from_line(line: str) -> dict[str, str] | None:
    """A profile screen names one person and one @account."""
    handles = re.findall(r"@[A-Za-z0-9._]{5,30}", line)
    if len(handles) != 1:
        return None
    name = line
    for handle in handles:
        name = name.replace(handle, " ")
    for label in _PROFILE_LABELS:
        name = name.replace(label, " ")
    name = clean_name(name.replace("·", " "))
    if len(name) < 2:
        return None
    return {"kind": "profile", "name": name, "contactName": "", "username": handles[0]}
