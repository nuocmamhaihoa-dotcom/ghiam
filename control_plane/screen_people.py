"""Đọc danh bạ và hồ sơ trên từng khung hình, rồi đề xuất dòng đủ ba cột."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageOps

from control_plane.people import clean_name, clean_username, fold_name

_HANDLE = re.compile(r"@[A-Za-z0-9._]{3,30}")
_TIME = re.compile(r"\d{1,2}:\d{2}")
_LABELS = {
    "danh ba",
    "follow",
    "da follow",
    "follower",
    "thich",
    "tin nhan",
    "tu cac lien he cua ban",
    "chua co video nao",
}
_LABEL_COMPACT = {
    "danhba",
    "follow",
    "dafollow",
    "follower",
    "thich",
    "tinnhan",
    "tucaclienhecuaban",
    "chuacovideonao",
}


class TextLine(NamedTuple):
    text: str
    left: int
    top: int
    bottom: int

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def _mark_count(name: str) -> int:
    return sum(1 for char in name if fold_name(char) != char.casefold())


def _richer_name(names: list[str]) -> str:
    return max(names, key=lambda name: (_mark_count(name), len(name)))


def _valid_handles(text: str) -> list[str]:
    return [handle for handle in _HANDLE.findall(text) if any(char.isalpha() for char in handle)]


def _is_label(text: str) -> bool:
    folded = fold_name(text)
    compact = folded.replace(" ", "")
    if folded in _LABELS or compact in _LABEL_COMPACT:
        return True
    return compact.startswith("videodo") and "dangtai" in compact


def _is_name_line(text: str) -> bool:
    stripped = text.strip()
    if _is_label(stripped) or _TIME.match(stripped):
        return False
    if re.fullmatch(r"[\d.\s]+", stripped):
        return False
    letters = [char for char in stripped if char.isalpha()]
    return len(letters) >= 2 and not _valid_handles(stripped)


def _strip_button(text: str) -> str:
    words = text.split()
    while words and fold_name(words[-1]) in {"follow", "thich"}:
        words.pop()
    return " ".join(words)


def lines_from_tsv(tsv: str) -> list[TextLine]:
    """Ghép các từ Tesseract cùng một dòng thành một dòng chữ có vị trí."""
    groups: dict[tuple[str, str, str, str], list[dict[str, int | str]]] = {}
    for raw in tsv.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if not word:
            continue
        try:
            conf = float(parts[10])
            left = int(float(parts[6]))
            top = int(float(parts[7]))
            width = int(float(parts[8]))
            height = int(float(parts[9]))
        except ValueError:
            continue
        if conf < 45 or width <= 0 or height <= 0:
            continue
        key = (parts[1], parts[2], parts[3], parts[4])
        groups.setdefault(key, []).append(
            {"text": word, "left": left, "top": top, "right": left + width, "bottom": top + height}
        )
    lines: list[TextLine] = []
    for words in groups.values():
        words.sort(key=lambda item: int(item["left"]))
        text = _strip_button(" ".join(str(item["text"]) for item in words))
        if len(text) < 2:
            continue
        left = min(int(item["left"]) for item in words)
        top = min(int(item["top"]) for item in words)
        bottom = max(int(item["bottom"]) for item in words)
        lines.append(TextLine(text, left, top, bottom))
    lines.sort(key=lambda line: (line.top, line.left))
    return lines


def _is_contacts(lines: list[TextLine]) -> bool:
    return any("danhba" in fold_name(line.text).replace(" ", "") for line in lines)


def _tight_pair(upper: TextLine, lower: TextLine) -> bool:
    """Hai dòng của một người nằm sát nhau, cùng một cột."""
    if abs(upper.left - lower.left) > 80:
        return False
    gap = lower.top - upper.bottom
    overlap_limit = int(min(upper.height, lower.height) * 0.55)
    if gap < -overlap_limit:
        return False
    return gap <= int(upper.height * 0.95)


def _contact_sightings(lines: list[TextLine]) -> list[dict[str, str]]:
    names = [line for line in lines if _is_name_line(line.text)]
    found: list[dict[str, str]] = []
    index = 0
    while index < len(names) - 1:
        upper = names[index]
        lower = names[index + 1]
        if _tight_pair(upper, lower):
            contact_name = clean_name(upper.text)
            name = clean_name(lower.text)
            if contact_name and name and fold_name(contact_name) != fold_name(name):
                found.append({"kind": "contact", "name": name, "contactName": contact_name, "username": ""})
            index += 2
            continue
        index += 1
    return found


def _profile_sighting(lines: list[TextLine]) -> dict[str, str] | None:
    handles = list(dict.fromkeys(handle for line in lines for handle in _valid_handles(line.text)))
    if len(handles) != 1:
        return None
    handle = handles[0]
    short = [line for line in lines if handle in line.text and len(line.text.split()) <= 4]
    for line in short:
        if _is_name_line(line.text.replace(handle, " ")):
            name = clean_name(line.text.replace(handle, " "))
            username = clean_username(handle)
            if len(name) >= 2 and username:
                return {"kind": "profile", "name": name, "contactName": "", "username": username}
    if not short:
        return None
    anchor = min(short, key=lambda line: line.top)
    above = [
        line
        for line in lines
        if line.bottom <= anchor.top + 6 and anchor.top - line.bottom <= 80 and _is_name_line(line.text)
    ]
    if not above:
        return None
    chosen = max(above, key=lambda line: line.bottom)
    username = clean_username(handle)
    name = clean_name(chosen.text)
    if len(name) < 2 or not username:
        return None
    return {"kind": "profile", "name": name, "contactName": "", "username": username}


def sightings_from_lines(lines: list[TextLine]) -> list[dict[str, str]]:
    """Danh bạ cho cặp tên. Hồ sơ cho một tên và đúng một @."""
    pairs = _contact_sightings(lines)
    if _is_contacts(lines) or len(pairs) >= 2:
        return pairs
    profile = _profile_sighting(lines)
    if profile is not None:
        return [profile]
    return pairs


def propose_rows(sightings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Chỉ đề xuất khi một tên có đúng một tên danh bạ và đúng một tài khoản."""
    contacts: dict[str, list[tuple[str, str]]] = {}
    profiles: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for item in sightings:
        name = clean_name(item.get("name") or "")
        key = fold_name(name)
        if not key:
            continue
        kind = item.get("kind") or ""
        if kind == "contact":
            contact_name = clean_name(item.get("contactName") or "")
            if not contact_name or fold_name(contact_name) == key:
                continue
            if key not in contacts and key not in profiles:
                order.append(key)
            contacts.setdefault(key, []).append((name, contact_name))
        elif kind == "profile":
            username = clean_username(item.get("username") or "")
            if not username:
                continue
            if key not in contacts and key not in profiles:
                order.append(key)
            profiles.setdefault(key, []).append((name, username))
    rows: list[dict[str, str]] = []
    for key in order:
        if key not in contacts or key not in profiles:
            continue
        contact_names = [contact_name for _name, contact_name in contacts[key]]
        usernames = {username for _name, username in profiles[key]}
        if len({fold_name(contact_name) for contact_name in contact_names}) != 1 or len(usernames) != 1:
            continue
        display = _richer_name([name for name, _extra in contacts[key] + profiles[key]])
        rows.append(
            {
                "name": display,
                "contactName": _richer_name(contact_names),
                "username": next(iter(usernames)),
            }
        )
    return rows[:200]


def captions_from_sightings(sightings: list[dict[str, str]]) -> list[str]:
    """Một dòng chữ cho mỗi người nhìn thấy trên khung hình."""
    lines: list[str] = []
    for item in sightings:
        kind = item.get("kind") or ""
        if kind == "contact":
            contact_name = clean_name(item.get("contactName") or "")
            name = clean_name(item.get("name") or "")
            if contact_name and name:
                lines.append(f"Danh bạ · {contact_name} · {name}")
        elif kind == "profile":
            name = clean_name(item.get("name") or "")
            username = clean_username(item.get("username") or "")
            if name and username:
                lines.append(f"{name} · {username}")
    return lines


def read_frame_tsv(path: Path) -> str:
    """Một lần Tesseract cho cả danh bạ và dòng chữ nhìn thấy."""
    prepared = path.with_name(path.stem + "-people.png")
    try:
        with Image.open(path) as full:
            ImageOps.autocontrast(full.convert("L")).save(prepared)
    except OSError:
        return ""
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    for lang in ("vie+eng", "eng"):
        try:
            result = subprocess.run(
                ["tesseract", str(prepared), "stdout", "-l", lang, "--psm", "11", "tsv"],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return ""


def sightings_from_image(path: Path) -> list[dict[str, str]]:
    """Đọc vị trí chữ trên một khung hình."""
    tsv = read_frame_tsv(path)
    if not tsv:
        return []
    return sightings_from_lines(lines_from_tsv(tsv))
