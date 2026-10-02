"""Đọc danh bạ và hồ sơ trên từng khung hình, rồi đề xuất dòng đủ ba cột."""

from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from control_plane import stage_timing
from control_plane.layout_learn import LayoutLearner, Zone, learner_for
from control_plane.people import clean_name, clean_username, fold_name
from control_plane.read_vote import (
    RowMemo,
    five_variants,
    memo_for,
    needs_reread,
    read_rapid,
    row_signature,
    vote_key,
    vote_line,
)
from control_plane.tesseract_keep import read_fast_line_tsv, read_line_tsv, read_tsv, reader_limit

_vote_pool: ThreadPoolExecutor | None = None
_vote_pool_lock = threading.Lock()

_HANDLE = re.compile(r"@[A-Za-z0-9._]{5,30}")
_HANDLE_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@"
# Dưới 30 là nhiễu. 45 bỏ sót chữ mờ trên video iPhone.
_WORD_CONF = 30
# Tên và @ không ghi khi conf còn thấp (sau khi đã đọc lại).
_NAME_CONF = 45.0
_HANDLE_CONF = 70.0
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


def _is_instruction(text: str) -> bool:
    """Câu hướng dẫn trên màn hình, không phải tên người."""
    folded = fold_name(text)
    if "cau hinh" in folded or "khong hop le" in folded:
        return True
    if "bam" in folded.split():
        return True
    return "dừng" in text.casefold()


def _is_name_line(text: str) -> bool:
    stripped = text.strip()
    if _is_instruction(stripped) or _is_label(stripped) or _TIME.match(stripped) or "@" in stripped:
        return False
    if re.fullmatch(r"[\d.\s]+", stripped):
        return False
    words = stripped.split()
    if len(words) > 6:
        return False
    lowered = f" {stripped.casefold()} "
    if " thì " in lowered or "đổi tên" in lowered:
        return False
    letters = [char for char in stripped if char.isalpha()]
    return len(letters) >= 2 and not _valid_handles(stripped)


def _word_core(word: str) -> str:
    """Chữ và số của một từ, bỏ dấu câu dính theo: Follow, thành follow."""
    return "".join(char for char in fold_name(word) if char.isalnum())


def _strip_button(text: str) -> str:
    words = text.split()
    while words and _word_core(words[-1]) in {"follow", "thich"}:
        words.pop()
    return " ".join(words)


def tsv_word_counts(tsv: str) -> tuple[int, int]:
    """Số từ Tesseract in ra (conf ≥ 0) và số từ còn lại trước khi ghép tên."""
    seen = 0
    kept = 0
    for raw in tsv.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if not word:
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        seen += 1
        if conf >= _WORD_CONF:
            kept += 1
    return seen, kept


class _TsvWord(NamedTuple):
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int


def tsv_words(tsv: str, *, min_conf: float = 0.0) -> list[_TsvWord]:
    """Từ Tesseract còn conf. Dùng để khóa @ và bỏ chữ yếu."""
    found: list[_TsvWord] = []
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
        if conf < min_conf or width <= 0 or height <= 0:
            continue
        found.append(_TsvWord(word, conf, left, top, width, height))
    return found


def handle_from_tsv(tsv: str, *, min_conf: float = _HANDLE_CONF) -> tuple[str, float]:
    """Một @ từ TSV dòng đơn. Conf thấp hơn ngưỡng thì không trả."""
    words = tsv_words(tsv, min_conf=0.0)
    if not words:
        return "", 0.0
    blob = "".join(word.text for word in words)
    conf = min(word.conf for word in words)
    found = _HANDLE.findall(blob)
    if found:
        handle = clean_username(found[0])
    elif any(char not in _HANDLE_CHARSET and not char.isspace() for char in blob):
        handle = ""
    else:
        cleaned = "".join(char for char in blob if char in _HANDLE_CHARSET)
        handle = clean_username(cleaned if cleaned.startswith("@") else f"@{cleaned}")
    if not handle or conf < min_conf:
        return "", conf
    return handle, conf


def name_min_conf(tsv: str, name: str) -> float | None:
    """Conf thấp nhất của các từ khớp tên. None khi không thấy trong TSV."""
    cleaned = clean_name(name)
    if not tsv or not cleaned:
        return None
    parts = {fold_name(part) for part in cleaned.split() if part}
    if not parts:
        return None
    confs = [
        word.conf
        for word in tsv_words(tsv, min_conf=0.0)
        if fold_name(word.text) in parts or word.text in cleaned
    ]
    if not confs:
        return None
    return min(confs)


def choose_tsv(memory: str | None, cli: str) -> str:
    """Bản trong bộ nhớ còn từ thì dùng. Bản rỗng thì lấy lệnh tesseract."""
    if memory and tsv_word_counts(memory)[1] > 0:
        return memory
    if cli:
        return cli
    return memory or ""


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
        if conf < _WORD_CONF or width <= 0 or height <= 0:
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
    return _merge_same_row(lines)


def _merge_same_row(lines: list[TextLine]) -> list[TextLine]:
    """Ghép mảnh chữ cùng một hàng, để một tên bị tách không thành hai người."""
    merged: list[TextLine] = []
    for line in lines:
        if not merged:
            merged.append(line)
            continue
        previous = merged[-1]
        overlap = min(previous.bottom, line.bottom) - max(previous.top, line.top)
        same_row = overlap >= int(0.45 * min(previous.height, line.height))
        same_column = abs(previous.left - line.left) <= 240 and line.left >= previous.left - 8
        if not same_row or not same_column:
            merged.append(line)
            continue
        text = _strip_button(f"{previous.text} {line.text}")
        merged[-1] = TextLine(
            text,
            min(previous.left, line.left),
            min(previous.top, line.top),
            max(previous.bottom, line.bottom),
        )
    return merged


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


def _profile_pick(lines: list[TextLine]) -> tuple[TextLine, TextLine, str, str] | None:
    """Dòng tên, dòng @, tên và tài khoản của trang hồ sơ. Dòng tên trùng dòng @ khi cả hai nằm chung một dòng."""
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
                return line, line, name, username
    if not short:
        return None
    anchor = min(short, key=lambda line: line.top)
    above = [
        line
        for line in lines
        if line.bottom <= anchor.top + 6
        and anchor.top - line.bottom <= max(80, int(2.2 * max(line.height, anchor.height)))
        and _is_name_line(line.text)
    ]
    if not above:
        return None
    chosen = max(above, key=lambda line: line.bottom)
    username = clean_username(handle)
    name = clean_name(chosen.text)
    if len(name) < 2 or not username:
        return None
    return chosen, anchor, name, username


def _profile_sighting(lines: list[TextLine]) -> dict[str, str] | None:
    picked = _profile_pick(lines)
    if picked is None:
        return None
    _name_line, _handle_line, name, username = picked
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


def _edit_distance(left: str, right: str, limit: int) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        lowest = limit + 1
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + cost))
            lowest = min(lowest, current[-1])
        if lowest > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _near_contact(left: str, right: str) -> bool:
    """Tên danh bạ dài lệch một ký tự là cùng một dòng bị đọc sai."""
    folded_left = fold_name(left)
    folded_right = fold_name(right)
    if folded_left == folded_right:
        return True
    if min(len(folded_left), len(folded_right)) < 8:
        return False
    return _edit_distance(folded_left, folded_right, 1) <= 1


def _near_username(left: str, right: str) -> bool:
    """Tài khoản cùng độ dài lệch một ký tự. Id khác nhiều số thì giữ riêng."""
    folded_left = left.lstrip("@")
    folded_right = right.lstrip("@")
    if folded_left == folded_right:
        return True
    if len(folded_left) != len(folded_right) or len(folded_left) < 4:
        return False
    return _edit_distance(folded_left, folded_right, 1) <= 1


def _winning_spellings(
    values: list[str],
    near: Callable[[str, str], bool],
    *,
    minimum: int = 3,
    multiple: int = 3,
) -> list[str] | None:
    """Cụm đọc nhiều nhất. Hai cách đọc khác nhau ngang nhau thì không chọn."""
    groups: list[list[str]] = []
    for value in values:
        placed = False
        for group in groups:
            if near(value, group[0]):
                group.append(value)
                placed = True
                break
        if not placed:
            groups.append([value])
    if not groups:
        return None
    groups.sort(key=len, reverse=True)
    if len(groups) == 1:
        return groups[0]
    leader = len(groups[0])
    runner = len(groups[1])
    if leader == runner or leader < minimum or leader < runner * multiple:
        return None
    return groups[0]


def _mode(values: list[str]) -> str:
    return max(values, key=lambda value: (values.count(value), len(value)))


def _accepted_sighting(item: dict[str, str]) -> tuple[str, str] | None:
    """Tên đã lọc và loại lần nhìn. Câu hướng dẫn và tài khoản ngắn không tính."""
    name = clean_name(item.get("name") or "")
    key = fold_name(name)
    if not key or _is_instruction(name):
        return None
    kind = item.get("kind") or ""
    if kind == "contact":
        contact_name = clean_name(item.get("contactName") or "")
        if (
            not contact_name
            or _is_instruction(contact_name)
            or "@" in contact_name
            or "@" in name
            or fold_name(contact_name) == key
        ):
            return None
        return key, "contact"
    if kind == "profile" and clean_username(item.get("username") or ""):
        return key, "profile"
    return None


def reading_counts(sightings: list[dict[str, str]]) -> dict[str, int]:
    """Số người trong danh bạ, số người có tài khoản, số người đủ điều kiện ghi."""
    contacts: set[str] = set()
    accounts: set[str] = set()
    for item in sightings:
        accepted = _accepted_sighting(item)
        if accepted is None:
            continue
        key, kind = accepted
        if kind == "contact":
            contacts.add(key)
        else:
            accounts.add(key)
    return {"contacts": len(contacts), "accounts": len(accounts), "saved": len(propose_rows(sightings))}


def propose_rows(sightings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Chỉ đề xuất khi một tên có một tên danh bạ và một tài khoản đọc thống nhất."""
    contacts: dict[str, list[tuple[str, str]]] = {}
    profiles: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for item in sightings:
        name = clean_name(item.get("name") or "")
        key = fold_name(name)
        if not key or _is_instruction(name):
            continue
        kind = item.get("kind") or ""
        if kind == "contact":
            contact_name = clean_name(item.get("contactName") or "")
            if (
                not contact_name
                or _is_instruction(contact_name)
                or "@" in contact_name
                or "@" in name
                or fold_name(contact_name) == key
            ):
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
        usernames = [username for _name, username in profiles[key]]
        # Tên danh bạ đọc giống nhau 2 lần thì thắng một cách đọc khác. Tài khoản vẫn cần lệch rõ hơn.
        chosen_contacts = _winning_spellings(contact_names, _near_contact, minimum=2, multiple=1)
        chosen_usernames = _winning_spellings(usernames, _near_username, minimum=2, multiple=1)
        if not chosen_contacts or not chosen_usernames:
            continue
        display = _richer_name([name for name, _extra in contacts[key] + profiles[key]])
        rows.append(
            {
                "name": display,
                "contactName": _richer_name(chosen_contacts),
                "username": _mode(chosen_usernames),
            }
        )
    return rows


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


def _prepared_image(path: Path) -> Image.Image | None:
    """Làm nét chữ đúng kích thước gốc. Phóng to dễ đọc nhầm số."""
    try:
        with Image.open(path) as full:
            gray = ImageOps.autocontrast(full.convert("L"))
            width, height = gray.size
            top = int(height * 0.04)
            bottom = int(height * 0.92)
            if bottom - top > 40:
                gray = gray.crop((0, top, width, bottom))
            sharpened = gray.filter(ImageFilter.SHARPEN)
            sharpened.load()
            return sharpened
    except OSError:
        return None


def _prepare_people_image(path: Path, prepared: Path) -> bool:
    image = _prepared_image(path)
    if image is None:
        return False
    try:
        image.save(prepared)
    except OSError:
        return False
    return True


def locate_tesseract(roots: list[Path] | None = None) -> tuple[Path | None, Path | None]:
    """Tìm tesseract và thư mục chữ. Ưu tiên bản cài cạnh phần mềm PC."""
    exe_candidates: list[Path] = []
    forced = os.environ.get("CONTROL_TESSERACT", "").strip().strip('"')
    if forced:
        exe_candidates.append(Path(forced))
    for root in roots or []:
        exe_candidates.append(root / "Tesseract-OCR" / "tesseract.exe")
        exe_candidates.append(root / "Tesseract-OCR" / "tesseract")
    if os.name == "nt":
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name, "")
            if base:
                exe_candidates.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            exe_candidates.append(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    found_on_path = shutil.which("tesseract")
    if found_on_path:
        exe_candidates.append(Path(found_on_path))
    exe = next((path for path in exe_candidates if path.is_file()), None)

    data_candidates: list[Path] = []
    prefix = os.environ.get("TESSDATA_PREFIX", "").strip().strip('"').rstrip("\\/")
    if prefix:
        data_candidates.append(Path(prefix))
    for root in roots or []:
        data_candidates.append(root / "tessdata")
    if exe is not None:
        data_candidates.append(exe.parent / "tessdata")
    data_candidates.extend(
        [
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tessdata"),
        ]
    )
    data = next(
        (
            path
            for path in data_candidates
            if (path / "eng.traineddata").is_file() or (path / "vie.traineddata").is_file()
        ),
        None,
    )
    return exe, data


def prepare_tesseract(roots: list[Path] | None = None) -> str:
    """Trỏ PATH và thư mục chữ tới bản đã cài. Trả về câu báo khi thiếu."""
    exe, data = locate_tesseract(roots)
    if exe is not None:
        os.environ["CONTROL_TESSERACT"] = str(exe)
        folder = str(exe.parent)
        path = os.environ.get("PATH", "")
        parts = path.split(os.pathsep) if path else []
        if folder not in parts:
            os.environ["PATH"] = folder + os.pathsep + path
    if data is not None:
        prefix = str(data)
        if not prefix.endswith(("\\", "/")):
            prefix += os.sep
        os.environ["TESSDATA_PREFIX"] = prefix
    if exe is None:
        return "PC chưa có Tesseract. Máy chủ sẽ đọc lại."
    if data is None:
        return "PC chưa có dữ liệu chữ vie+eng. Máy chủ sẽ đọc lại."
    return ""


def _tesseract_command() -> str:
    forced = os.environ.get("CONTROL_TESSERACT", "").strip().strip('"')
    if forced and Path(forced).is_file():
        return forced
    return "tesseract"


def _standard_prefix() -> str | None:
    prefix = os.environ.get("CONTROL_TESSDATA_STANDARD", "").strip() or None
    if prefix is not None:
        return prefix
    _exe, data = locate_tesseract(None)
    return str(data) if data is not None else None


def _tesseract_cli_run(
    prepared: Path,
    *,
    prefix: str | None = None,
    psm: str = "11",
    langs: tuple[str, ...] = ("vie+eng", "eng"),
    extra: list[str] | None = None,
) -> str:
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    if prefix:
        folder = prefix if prefix.endswith((os.sep, "/")) else prefix + os.sep
        env["TESSDATA_PREFIX"] = folder
    command = _tesseract_command()
    added = list(extra or [])
    for lang in langs:
        argv = [
            command,
            str(prepared),
            "stdout",
            "--dpi",
            "300",
            "-l",
            lang,
            "--oem",
            "1",
            "--psm",
            psm,
            *added,
            "tsv",
        ]
        try:
            result = subprocess.run(
                argv,
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


def _tesseract_cli(prepared: Path) -> str:
    return _tesseract_cli_run(prepared)


def _tesseract_cli_with_prefix(prepared: Path, prefix: str | None) -> str:
    return _tesseract_cli_run(prepared, prefix=prefix)


def prepare_frame_image(path: Path) -> Image.Image | None:
    """Ảnh khung đã làm nét, mở một lần để đọc khung, đọc lại bộ chuẩn, và đối chiếu dùng chung."""
    return _prepared_image(path)


def _read_frame_tsv_impl(path: Path, *, standard: bool, loaded: Image.Image | None = None) -> str:
    image = loaded if loaded is not None else _prepared_image(path)
    if image is None:
        return ""
    if not standard:
        kept = read_tsv(image)
        if kept is not None and tsv_word_counts(kept)[1] > 0:
            return kept
    else:
        kept = None
    prepared = path.with_name(path.stem + "-people.png")
    try:
        image.save(prepared)
    except OSError:
        return kept or ""
    prefix = _standard_prefix() if standard else None
    cli = _tesseract_cli_with_prefix(prepared, prefix)
    return choose_tsv(kept, cli)


def read_frame_tsv(path: Path, loaded: Image.Image | None = None) -> str:
    """Một lần Tesseract cho cả danh bạ và dòng chữ nhìn thấy."""
    return _read_frame_tsv_impl(path, standard=False, loaded=loaded)


def read_frame_tsv_standard(path: Path, loaded: Image.Image | None = None) -> str:
    """Đọc lại bằng bộ chữ chuẩn khi bộ chữ nhanh nghi ngờ."""
    return _read_frame_tsv_impl(path, standard=True, loaded=loaded)


def _crop_box(image: Image.Image, left: int, top: int, width: int, height: int) -> Image.Image:
    pad = max(6, int(max(height, 1) * 0.4))
    right = left + max(1, width)
    bottom = top + max(1, height)
    box = (
        max(0, left - pad),
        max(0, top - pad),
        min(image.width, right + pad),
        min(image.height, bottom + pad),
    )
    return image.crop(box)


def _handle_conf(tsv: str, handle: str) -> float | None:
    token = clean_username(handle).lstrip("@").casefold()
    if not token:
        return None
    confs = [
        word.conf
        for word in tsv_words(tsv, min_conf=0.0)
        if token in word.text.casefold().replace(" ", "")
    ]
    if not confs:
        return None
    return min(confs)


_BUTTON_WORDS = {"follow", "thich", "dafollow", "follower"}
# Từ nhiễu cao hơn 1,6 lần chữ thường là hình nút bị đọc thành chữ. Chữ cùng dòng lệch tâm không quá 0,6 chiều cao.
_BOX_JUNK_HEIGHT = 1.6
_BOX_SAME_LINE = 0.6
# Khoảng trống rộng hơn 1,5 chiều cao chữ là sang phần tử khác, ví dụ nút Follow ở mép phải.
_BOX_GAP = 1.5


def _band_words(line: TextLine, tsv: str) -> list[_TsvWord]:
    return [
        word
        for word in tsv_words(tsv, min_conf=0.0)
        if line.top - 4 <= word.top + (word.height / 2) <= line.bottom + 4 and word.left >= line.left - 12
    ]


def _union_box(words: list[_TsvWord]) -> tuple[int, int, int, int]:
    left = min(word.left for word in words)
    top = min(word.top for word in words)
    right = max(word.left + word.width for word in words)
    bottom = max(word.top + word.height for word in words)
    return left, top, max(1, right - left), max(1, bottom - top)


def _wide_box(line: TextLine, tsv: str, width: int) -> tuple[int, int, int, int]:
    """Hộp chữ của cả dòng. Không có hộp từ thì ước theo chiều cao dòng."""
    words = _band_words(line, tsv)
    if words:
        return _union_box(words)
    height = max(8, line.height)
    guess = max(48, int(len(line.text) * height * 0.55))
    return line.left, line.top, min(guess, max(1, width - line.left)), height


def _is_button_word(text: str) -> bool:
    core = _word_core(text)
    return core in _BUTTON_WORDS or core.startswith(("follow", "ollow"))


def _run_from_left(words: list[_TsvWord], reach: float) -> list[_TsvWord]:
    ordered = sorted(words, key=lambda word: word.left)
    run = [ordered[0]]
    for word in ordered[1:]:
        last = run[-1]
        if word.left - (last.left + last.width) > reach:
            break
        run.append(word)
    return run


def _line_box(line: TextLine, tsv: str, width: int, kind: str = "name") -> tuple[int, int, int, int]:
    """Hộp của đúng chữ cần đối chiếu. Tên bỏ @ và nút Follow, dừng ở khoảng trống rộng. @ bắt đầu từ từ có @."""
    words = _band_words(line, tsv)
    if not words:
        return _wide_box(line, tsv, width)
    middle = statistics.median(word.height for word in words)
    if kind == "handle":
        ordered = sorted(words, key=lambda word: word.left)
        starts = [index for index, word in enumerate(ordered) if "@" in word.text]
        if not starts:
            return _wide_box(line, tsv, width)
        return _union_box(_run_from_left(ordered[starts[0] :], _BOX_GAP * middle))
    words = [word for word in words if "@" not in word.text and not _is_button_word(word.text)]
    if not words:
        return _wide_box(line, tsv, width)
    middle = statistics.median(word.height for word in words)
    center = statistics.median(word.top + word.height / 2 for word in words)
    words = [
        word
        for word in words
        if word.height <= _BOX_JUNK_HEIGHT * middle
        and abs(word.top + word.height / 2 - center) <= _BOX_SAME_LINE * middle
    ]
    if not words:
        return _wide_box(line, tsv, width)
    return _union_box(_run_from_left(words, _BOX_GAP * middle))


def _scaled(crop: Image.Image, scale: int) -> Image.Image:
    if scale == 1:
        return crop
    return crop.resize(
        (max(1, crop.width * scale), max(1, crop.height * scale)),
        Image.Resampling.LANCZOS,
    )


def _text_from_line_tsv(tsv: str, kind: str) -> str:
    if kind == "handle":
        handle, _conf = handle_from_tsv(tsv, min_conf=0.0)
        return handle
    words = tsv_words(tsv, min_conf=0.0)
    return clean_name(" ".join(word.text for word in words))


def _read_saved_crop(dest: Path, kind: str) -> str:
    """Bộ chữ chuẩn, một dòng. @ khóa charset. Tên giữ dấu Việt."""
    prefix = _standard_prefix()
    if kind == "handle":
        extra = ["-c", f"tessedit_char_whitelist={_HANDLE_CHARSET}"]
        tsv = _tesseract_cli_run(dest, prefix=prefix, psm="7", langs=("eng",), extra=extra)
        return _text_from_line_tsv(tsv, kind)
    tsv = _tesseract_cli_run(dest, prefix=prefix, psm="7", langs=("vie+eng", "eng"))
    return _text_from_line_tsv(tsv, kind)


def _read_prepared(picture: Image.Image, dest: Path, kind: str) -> str:
    kept = read_line_tsv(picture, kind=kind)
    if kept is not None:
        return _text_from_line_tsv(kept, kind)
    try:
        picture.save(dest)
    except OSError:
        return ""
    return _read_saved_crop(dest, kind)


def _third_read(crop: Image.Image, dest: Path, kind: str) -> str | None:
    """RapidOCR trên dòng đã cắt. Chưa cài thì Tesseract phóng ba lần, tăng tương phản."""
    enlarged = _scaled(crop, 2)
    rapid = read_rapid(enlarged)
    if rapid is not None:
        if kind == "handle":
            return clean_username(rapid)
        return clean_name(rapid)
    contrasted = ImageOps.autocontrast(crop)
    return _read_prepared(_scaled(contrasted, 3), dest, kind)


def _reread(variant: Image.Image, dest: Path, kind: str) -> str:
    """Một lần đọc lại. @ đọc bằng RapidOCR vì trên trang mẫu nó đúng 14 trên 14, Tesseract hay nhầm q với g.
    Tên đọc bằng bộ chữ chuẩn vì chỉ nó giữ được dấu."""
    if kind == "handle":
        rapid = read_rapid(variant)
        if rapid is not None:
            return clean_username(rapid)
    return _read_prepared(variant, dest, kind)


def _vote_box(
    image: Image.Image,
    box: tuple[int, int, int, int],
    dest: Path,
    kind: str,
    seed: str,
    memo: RowMemo | None = None,
    frame_id: str = "",
    on_settle: Callable[[], None] | None = None,
) -> tuple[str, bool]:
    """Hướng 2 luôn chạy. Hướng 3 và năm lần đọc lại chỉ khi các hướng đã có còn lệch.

    memo có dòng đã chốt ở đủ khung khác nhau thì dùng lại, không đọc nữa. on_settle chạy đúng lúc một dòng vừa chốt hẳn.
    """
    stage_timing.bump("vote.lines")
    mark: tuple[int, int, Image.Image] | None = None
    if memo is not None:
        mark = (box[0], box[2], row_signature(image, box))
        kept = memo.settled(kind, seed, *mark)
        if kept is not None:
            stage_timing.bump("vote.reused")
            return kept, True
    crop = _crop_box(image, *box)
    second = _read_prepared(_scaled(crop, 2), dest, kind)
    if vote_key(seed, kind) and vote_key(seed, kind) == vote_key(second, kind):
        text, agreed = vote_line(seed, second, None, [], kind=kind)
    else:
        stage_timing.bump("vote.third")
        third = _third_read(crop, dest, kind)
        if not needs_reread(seed, second, third, kind):
            text, agreed = vote_line(seed, second, third, [], kind=kind)
        else:
            stage_timing.bump("vote.rereads")
            reruns = [_reread(variant, dest, kind) for variant in five_variants(crop)]
            text, agreed = vote_line(seed, second, third, reruns, kind=kind)
    if memo is not None and mark is not None:
        if agreed and text:
            if memo.agree(kind, seed, *mark, frame_id, text) and on_settle is not None:
                on_settle()
        else:
            memo.fail(kind, seed, *mark)
    return text, agreed


def _list_frame(lines: list[TextLine]) -> bool:
    """Khung danh bạ: có chữ Danh bạ hoặc từ hai cặp tên sát nhau."""
    return _is_contacts(lines) or len(_contact_sightings(lines)) >= 2


def _ink_box(image: Image.Image, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """Hộp bao dòng chữ rõ nhất trong một dải: các hàng có chữ liền nhau, lấy cụm nhiều chữ nhất.

    Một vệt nhỏ của dòng bên cạnh lọt vào mép dải không kéo hộp ra ngoài dòng chữ. None khi dải trống.
    """
    left, top, width, height = rect
    if width < 2 or height < 2:
        return None
    gray = image.crop((left, top, left + width, top + height))
    if gray.mode != "L":
        gray = gray.convert("L")
    background = int(ImageStat.Stat(gray).median[0])
    diff = ImageChops.difference(gray, Image.new("L", gray.size, background))
    mask = diff.point(lambda value: 255 if value > 40 else 0)
    rows = list(mask.resize((1, mask.height), Image.Resampling.BOX).getdata())
    # Hàng có chữ khi có ít nhất khoảng 1% điểm ảnh tối. Khe hở một hai hàng giữa nét chữ vẫn tính là liền.
    inked = [value >= 3 for value in rows]
    runs: list[tuple[int, int, int]] = []
    start = None
    gap = 0
    for index, flag in enumerate(inked + [False, False, False]):
        if flag:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 2:
                stop = index - gap
                runs.append((start, stop, sum(rows[start : stop + 1])))
                start = None
                gap = 0
    if not runs:
        return None
    first, last, _weight = max(runs, key=lambda run: run[2])
    band = mask.crop((0, first, mask.width, last + 1))
    found = band.getbbox()
    if found is None:
        return None
    box_left, _top, box_right, _bottom = found
    box_width = box_right - box_left
    box_height = last + 1 - first
    if box_width < 24 or box_height < 8:
        return None
    return left + box_left, top + first, box_width, box_height


def _band_read(source: Image.Image, zone: Zone, kind: str) -> tuple[str, tuple[int, int, int, int]] | None:
    """Đọc đúng dải đã từng chốt được chữ: cắt sát chữ rồi đọc bằng bộ chữ nhanh một dòng. None khi không ra chữ hợp lệ."""
    height = max(8, zone.bottom - zone.top)
    pad = max(6, height // 2)
    top = max(0, zone.top - pad)
    bottom = min(source.height, zone.bottom + pad)
    ink = _ink_box(source, (0, top, source.width, bottom - top))
    if ink is None:
        return None
    fast = read_fast_line_tsv(_scaled(_crop_box(source, *ink), 2), kind=kind)
    if fast is None:
        return None
    text = _text_from_line_tsv(fast, kind)
    if kind == "handle":
        return (text, ink) if _valid_handles(text) else None
    return (text, ink) if vote_key(text, "name") else None


# Chữ đọc từ dải đã học chưa được ai kiểm. Điểm thấp hơn ngưỡng giữ chữ đọc đơn lẻ (45 cho tên, 70 cho @),
# nên chỉ vào bảng khi bỏ phiếu hai hướng trùng.
_BAND_HANDLE_CONF = 50
_BAND_NAME_CONF = 40


def _tsv_row(block: str, text: str, box: tuple[int, int, int, int], conf: int) -> str:
    left, top, width, height = box
    return f"5\t1\t{block}\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"


def _without_band(tsv: str, top: int, bottom: int) -> str:
    """Bỏ các từ đọc cả khung rơi vào dải đã học. Chúng là chữ rác vì dải này đáng ra có @ hoặc tên."""
    kept: list[str] = []
    for raw in tsv.splitlines():
        parts = raw.split("\t")
        if len(parts) >= 12 and parts[0] == "5":
            try:
                center = int(float(parts[7])) + int(float(parts[9])) / 2
            except ValueError:
                center = None
            if center is not None and top <= center <= bottom:
                continue
        kept.append(raw)
    return "\n".join(kept)


def _rescue_profile(
    source: Image.Image,
    learner: LayoutLearner,
    lines: list[TextLine],
    tsv: str,
    frame: str,
) -> tuple[list[TextLine], str, bool]:
    """Trang hồ sơ: đọc đúng dải @ và dải tên đã học từ các trang đọc thành công, thay cho chữ rác của lần đọc cả khung.

    Đọc cả khung có lúc không thấy @ (trang mẫu mất 5 trong 14 trang), có lúc thấy @ mà tên vỡ thành mảnh.
    Chỉ chạy khi khung không phải danh bạ, tối đa một lần cho mỗi khung. Chữ đọc được vẫn qua bỏ phiếu như mọi dòng.
    """
    if _list_frame(lines):
        return lines, tsv, False
    handles = list(dict.fromkeys(handle for line in lines for handle in _valid_handles(line.text)))
    if len(handles) > 1:
        return lines, tsv, False
    handle_zone = learner.handle_zone()
    name_zone = learner.name_zone()
    if handles:
        # Đã thấy @: chỉ thay phần tên nếu đã biết dải tên.
        if name_zone is None or not learner.first_try(frame):
            return lines, tsv, False
        handle_row = None
    else:
        if handle_zone is None or not learner.first_try(frame):
            return lines, tsv, False
        found = _band_read(source, handle_zone, "handle")
        if found is None:
            return lines, tsv, False
        handle_row = found
    cleaned = tsv
    rows: list[str] = []
    if handle_row is not None:
        handle_text, handle_box = handle_row
        top, bottom = handle_box[1], handle_box[1] + handle_box[3]
        pad = max(6, (bottom - top) // 2)
        cleaned = _without_band(cleaned, top - pad, bottom + pad)
        rows.append(_tsv_row("90", handle_text, handle_box, _BAND_HANDLE_CONF))
    named = _band_read(source, name_zone, "name") if name_zone is not None else None
    if named is not None:
        name_text, name_box = named
        pad = max(6, name_box[3] // 2)
        cleaned = _without_band(cleaned, name_box[1] - pad, name_box[1] + name_box[3] + pad)
        rows.append(_tsv_row("91", name_text, name_box, _BAND_NAME_CONF))
    if not rows:
        return lines, tsv, False
    merged = "\n".join(part for part in (cleaned, *rows) if part)
    return lines_from_tsv(merged), merged, True


def _open_frame_image(path: Path, *, prepared: bool) -> Image.Image | None:
    if prepared:
        return _prepared_image(path)
    try:
        with Image.open(path) as full:
            copied = full.convert("RGB")
            copied.load()
            return copied
    except OSError:
        return None


def _vote_executor() -> ThreadPoolExecutor:
    global _vote_pool
    with _vote_pool_lock:
        if _vote_pool is None:
            # Số lõi lúc rảnh có thể cao hơn số bộ đọc lúc đang dùng máy. Bộ đọc Tesseract tự giới hạn số lượt chạy cùng lúc.
            size = max(1, reader_limit(), os.cpu_count() or 1)
            _vote_pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="vote")
        return _vote_pool


def _apply_line_votes(
    line: TextLine,
    source: Image.Image,
    tsv: str,
    dest: Path,
    memo: RowMemo | None = None,
    frame_id: str = "",
    learner: LayoutLearner | None = None,
    list_frame: bool = False,
    learn: bool = True,
) -> tuple[TextLine, str | None, str | None]:
    """Đối chiếu một dòng. Trả dòng đã sửa, tên đã trùng, @ đã trùng."""
    text = line.text
    agreed_handle: str | None = None
    agreed_name: str | None = None
    if _valid_handles(text) or text.strip().startswith("@"):
        handle_box = _line_box(line, tsv, source.width, "handle")
        voted, handle_agreed = _vote_box(source, handle_box, dest, "handle", text, memo, frame_id)
        if handle_agreed and voted and learner is not None and learn:
            learner.learn_handle(handle_box[1], handle_box[1] + handle_box[3])
        sure = _handle_conf(tsv, voted or text)
        if voted and (handle_agreed or (sure is not None and sure >= _HANDLE_CONF)):
            if handle_agreed:
                agreed_handle = voted
            for old in _valid_handles(text):
                text = text.replace(old, voted)
            if not _valid_handles(text):
                text = f"{text} {voted}".strip()
        else:
            text = re.sub(r"@\S+", "", text)
    name_source = re.sub(r"@\S+", " ", text)
    if _is_name_line(name_source):
        name_box = _line_box(line, tsv, source.width, "name")
        if learner is not None and list_frame and not learner.fits_name_box(name_box, source.height):
            # Ô cao quá, thấp quá hoặc chạm mép ảnh: trong video này chưa lần nào chốt đúng ở dạng đó.
            learner.note("skipped")
            stage_timing.bump("vote.skipped")
            voted_name, name_agreed = "", False
        else:
            settled = (lambda height=name_box[3]: learner.learn_height(height)) if learner is not None and list_frame else None
            voted_name, name_agreed = _vote_box(source, name_box, dest, "name", name_source, memo, frame_id, settled)
        handle_part = " ".join(_valid_handles(text))
        if voted_name and name_agreed:
            agreed_name = voted_name
            text = f"{voted_name} {handle_part}".strip()
        elif voted_name and not name_agreed:
            text = f"{clean_name(name_source)} {handle_part}".strip()
        else:
            text = handle_part
    cleaned = " ".join(text.split())
    return TextLine(cleaned, line.left, line.top, line.bottom), agreed_name, agreed_handle


def _vote_one_line(
    index: int,
    line: TextLine,
    source: Image.Image,
    tsv: str,
    stem: Path,
    memo: RowMemo | None = None,
    frame_id: str = "",
    learner: LayoutLearner | None = None,
    list_frame: bool = False,
    learn: bool = True,
) -> tuple[int, TextLine, str | None, str | None]:
    dest = stem.with_name(f"{stem.stem}-vote-{index}.png")
    try:
        updated, agreed_name, agreed_handle = _apply_line_votes(
            line, source, tsv, dest, memo, frame_id, learner, list_frame, learn
        )
    finally:
        dest.unlink(missing_ok=True)
    return index, updated, agreed_name, agreed_handle


def memo_scope(path: Path) -> str:
    """Khóa bộ nhớ dòng là thư mục khung của video. Vùng đọc dày boost-N dùng chung với video."""
    folder = path.parent
    if folder.name.startswith("boost-"):
        folder = folder.parent
    return str(folder)


def _rewrite_with_votes(
    path: Path,
    source: Image.Image,
    lines: list[TextLine],
    tsv: str,
    memo: RowMemo | None = None,
    frame_id: str = "",
    learner: LayoutLearner | None = None,
    list_frame: bool = False,
    learn: bool = True,
) -> tuple[list[TextLine], set[str], list[str]]:
    """Mỗi dòng tên và @ đối chiếu riêng. Chuỗi đã trùng thì ghi, lệch hết thì bỏ."""
    agreed_names: set[str] = set()
    agreed_handles: list[str] = []
    if not lines:
        return [], agreed_names, agreed_handles
    updated: list[TextLine] = [TextLine("", 0, 0, 0)] * len(lines)

    def take(index: int, row: TextLine, agreed_name: str | None, agreed_handle: str | None) -> None:
        updated[index] = row
        if agreed_name:
            agreed_names.add(agreed_name)
        if agreed_handle and agreed_handle not in agreed_handles:
            agreed_handles.append(agreed_handle)

    if len(lines) == 1:
        index, row, agreed_name, agreed_handle = _vote_one_line(
            0, lines[0], source, tsv, path, memo, frame_id, learner, list_frame, learn
        )
        take(index, row, agreed_name, agreed_handle)
        return updated, agreed_names, agreed_handles

    futures = [
        _vote_executor().submit(
            _vote_one_line, index, line, source, tsv, path, memo, frame_id, learner, list_frame, learn
        )
        for index, line in enumerate(lines)
    ]
    for future in futures:
        index, row, agreed_name, agreed_handle = future.result()
        take(index, row, agreed_name, agreed_handle)
    return updated, agreed_names, agreed_handles


def tighten_frame_reading(
    path: Path,
    lines: list[TextLine],
    sightings: list[dict[str, str]],
    *,
    tsv: str = "",
    prepared: bool = True,
    loaded: Image.Image | None = None,
) -> tuple[list[TextLine], list[dict[str, str]]]:
    """Đối chiếu tên và @. Hai hoặc ba hướng trùng thì ghi. Lệch hết thì đọc lại năm lần.

    loaded là ảnh prepare_frame_image đã mở sẵn, đỡ mở lại file khung.
    """
    del sightings
    source = loaded if loaded is not None else _open_frame_image(path, prepared=prepared)
    agreed_names: set[str] = set()
    agreed_handles: list[str] = []
    scope = memo_scope(path)
    learner = learner_for(scope)
    frame_id = f"{path.parent.name}/{path.name}"
    rescued = False
    if source is None:
        updated = list(lines)
    else:
        lines, tsv, rescued = _rescue_profile(source, learner, lines, tsv, frame_id)
        updated, agreed_names, agreed_handles = _rewrite_with_votes(
            path, source, lines, tsv, memo_for(scope), frame_id, learner, _list_frame(lines), not rescued
        )

    found = sightings_from_lines(updated)
    kept: list[dict[str, str]] = []
    for item in found:
        kind = item.get("kind") or ""
        name = clean_name(item.get("name") or "")
        contact = clean_name(item.get("contactName") or "")
        username = clean_username(item.get("username") or "")
        name_conf = name_min_conf(tsv, name)
        contact_conf = name_min_conf(tsv, contact)
        if name not in agreed_names and name_conf is not None and name_conf < _NAME_CONF:
            continue
        if contact not in agreed_names and contact_conf is not None and contact_conf < _NAME_CONF:
            continue
        if kind == "profile":
            if agreed_handles:
                if username not in agreed_handles:
                    username = ""
            if not name or not username:
                continue
            kept.append({"kind": "profile", "name": name, "contactName": "", "username": username})
            continue
        if kind == "contact" and name and contact:
            kept.append({"kind": "contact", "name": name, "contactName": contact, "username": ""})
    if agreed_handles and not any(item.get("kind") == "profile" for item in kept):
        names = [line for line in updated if _is_name_line(line.text)]
        if names:
            name = clean_name(names[-1].text)
            name_conf = name_min_conf(tsv, name)
            if name and (name in agreed_names or name_conf is None or name_conf >= _NAME_CONF):
                kept.append(
                    {"kind": "profile", "name": name, "contactName": "", "username": agreed_handles[0]}
                )
    if source is not None and agreed_handles and any(item.get("kind") == "profile" for item in kept):
        if rescued:
            learner.note("rescued")
            stage_timing.bump("zone.rescued")
        else:
            _learn_profile_zones(learner, updated, tsv, source.width, agreed_names, agreed_handles)
    return updated, kept


def _learn_profile_zones(
    learner: LayoutLearner,
    lines: list[TextLine],
    tsv: str,
    width: int,
    agreed_names: set[str],
    agreed_handles: list[str],
) -> None:
    """Trang hồ sơ đã đọc chắc cả tên lẫn @: nhớ dải ngang của tên để sau này đọc đúng chỗ."""
    picked = _profile_pick(lines)
    if picked is None:
        return
    name_line, handle_line, name, username = picked
    if username not in agreed_handles or name not in agreed_names or name_line is handle_line:
        return
    _left, top, _width, height = _line_box(name_line, tsv, width, "name")
    learner.learn_name_zone(top, top + height)


def sightings_from_image(path: Path) -> list[dict[str, str]]:
    """Đọc vị trí chữ trên một khung hình."""
    tsv = read_frame_tsv(path)
    if not tsv:
        return []
    lines = lines_from_tsv(tsv)
    sightings = sightings_from_lines(lines)
    _lines, found = tighten_frame_reading(path, lines, sightings, tsv=tsv)
    return found
