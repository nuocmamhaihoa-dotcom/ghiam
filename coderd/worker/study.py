from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from app import db
from worker.connect import load_env, load_env_file

SKIP = {"không có", "khong co", "không có gì mới", "khong co gi moi"}
LIMIT = 40


def keep_lesson(item: str) -> bool:
    if len(item) < 12 or item.lower() in SKIP:
        return False
    low = item.lower()
    if low.startswith("xin chào") or "tôi có thể" in low or low.endswith("?"):
        return False
    return sum(ch.isalpha() for ch in item) >= 6


def parse_lessons(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip().lstrip("•").strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        elif stripped.startswith("-"):
            stripped = stripped[1:].strip()
        else:
            continue
        if not keep_lesson(stripped) or stripped.lower() in seen:
            continue
        seen.add(stripped.lower())
        found.append(stripped[:240])
        if len(found) == 3:
            break
    return found


def merge_lessons(existing: str, fresh: list[str], limit: int = LIMIT) -> str:
    kept: list[str] = []
    seen: set[str] = set()
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            item = stripped[2:].strip()
        else:
            continue
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            kept.append(item)
    for item in fresh:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            kept.append(item)
    kept = kept[-limit:]
    if not kept:
        return ""
    return "\n".join(f"- {item}" for item in kept) + "\n"


_PY = "https://raw.githubusercontent.com/python/cpython/3.12/Doc"
_GIT = "https://raw.githubusercontent.com/git/git/master/Documentation"
OPEN_SOURCES = (
    ("python-nhap-mon", f"{_PY}/tutorial/introduction.rst"),
    ("python-dieu-khien", f"{_PY}/tutorial/controlflow.rst"),
    ("python-du-lieu", f"{_PY}/tutorial/datastructures.rst"),
    ("python-module", f"{_PY}/tutorial/modules.rst"),
    ("python-file", f"{_PY}/tutorial/inputoutput.rst"),
    ("python-loi", f"{_PY}/tutorial/errors.rst"),
    ("python-lop", f"{_PY}/tutorial/classes.rst"),
    ("python-stdlib", f"{_PY}/tutorial/stdlib.rst"),
    ("python-venv", f"{_PY}/tutorial/venv.rst"),
    ("python-json", f"{_PY}/library/json.rst"),
    ("python-pathlib", f"{_PY}/library/pathlib.rst"),
    ("python-sqlite", f"{_PY}/library/sqlite3.rst"),
    ("python-urllib", f"{_PY}/library/urllib.request.rst"),
    ("python-subprocess", f"{_PY}/library/subprocess.rst"),
    ("python-argparse", f"{_PY}/library/argparse.rst"),
    ("python-unittest", f"{_PY}/library/unittest.rst"),
    ("git-init", f"{_GIT}/git-init.adoc"),
    ("git-clone", f"{_GIT}/git-clone.adoc"),
    ("git-status", f"{_GIT}/git-status.adoc"),
    ("git-add", f"{_GIT}/git-add.adoc"),
    ("git-commit", f"{_GIT}/git-commit.adoc"),
    ("git-diff", f"{_GIT}/git-diff.adoc"),
    ("git-log", f"{_GIT}/git-log.adoc"),
    ("git-show", f"{_GIT}/git-show.adoc"),
    ("git-branch", f"{_GIT}/git-branch.adoc"),
    ("git-restore", f"{_GIT}/git-restore.adoc"),
    ("git-merge", f"{_GIT}/git-merge.adoc"),
    ("git-fetch", f"{_GIT}/git-fetch.adoc"),
    ("git-pull", f"{_GIT}/git-pull.adoc"),
    ("git-push", f"{_GIT}/git-push.adoc"),
    ("git-remote", f"{_GIT}/git-remote.adoc"),
)
SKIP_FILES = {"da-hoc.md", "kinh-nghiem.md", "muc-luc.md"}
CACHE_TTL = 24 * 60 * 60
OPEN_NOTE_LIMIT = 8


def _clean_markup(line: str) -> str:
    line = re.sub(r":[A-Za-z0-9-]+:`!?([^`]+)`", r"\1", line)
    line = re.sub(r"linkgit:([A-Za-z0-9-]+)\[\d+\]", r"\1", line)
    line = re.sub(r"<([^>]+)>_?", r"\1", line)
    line = re.sub(r"\b([A-Za-z][A-Za-z0-9-]*)_\b", r"\1", line)
    line = line.replace("`", "")
    return re.sub(r"\s+", " ", line).strip()


def _pieces(line: str) -> list[str]:
    if line.startswith("- "):
        return [line]
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]
    if not parts:
        parts = [line]
    fitted: list[str] = []
    for part in parts:
        if len(part) <= 240:
            fitted.append(part)
        else:
            fitted.append(part[:240].rsplit(" ", 1)[0])
    return fitted


def _paragraphs(text: str) -> list[str]:
    blocks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append(_clean_markup(" ".join(buf)))
            buf.clear()

    for raw in text.splitlines():
        if raw.startswith("   ") or raw.startswith("\t") or not raw.strip():
            flush()
            continue
        line = raw.strip()
        if line.startswith("..") or line.startswith("|") or line.startswith(":") or line.startswith("#"):
            flush()
            continue
        if set(line) <= set("*-= ") or line.endswith("::"):
            flush()
            continue
        if line.startswith("- ") or line.startswith("* "):
            flush()
            blocks.append(_clean_markup(line))
            continue
        buf.append(line)
    flush()
    return [item for item in blocks if item]


def extract_notes(text: str, limit: int = 8) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for line in _paragraphs(text):
        for piece in _pieces(line):
            if piece.startswith("- "):
                item = piece[2:].strip()
            elif 40 <= len(piece) <= 280 and not piece.startswith("["):
                item = piece
            else:
                continue
            if not keep_lesson(item) or item.lower() in seen:
                continue
            seen.add(item.lower())
            notes.append(item[:240])
            if len(notes) >= limit:
                return notes
    return notes


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "coderd-study"})
    with urllib.request.urlopen(request, timeout=12) as response:
        data = response.read(100_000)
    return data.decode("utf-8", "replace")


def load_source(root: Path, name: str, url: str, fetch) -> str:
    cache = root / "learn" / "cache" / f"{name}.txt"
    if cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        cached = cache.read_text(encoding="utf-8")
        if cached.strip():
            return cached
    text = fetch(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def _bullets(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            found.append(item)
    return found


def remember(existing: str, highlights: list[str], limit: int = LIMIT) -> str:
    old = _bullets(existing)
    keys = {item.lower() for item in highlights}
    chat = [item for item in old if item.lower() not in keys][-20:]
    picked: list[str] = []
    seen = {item.lower() for item in chat}
    room = max(limit - len(chat), 0)
    for item in highlights:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) >= room:
            break
    kept = picked + chat
    if not kept:
        return ""
    return "\n".join(f"- {item}" for item in kept[:limit]) + "\n"


def ingest_sources(root: Path, fetch=fetch_text) -> int:
    learn = root / "learn"
    learn.mkdir(parents=True, exist_ok=True)
    sections: list[tuple[str, str, list[str]]] = []
    if learn.is_dir():
        for path in sorted(learn.glob("*.md")):
            if path.name in SKIP_FILES:
                continue
            notes = extract_notes(path.read_text(encoding="utf-8"), limit=30)
            if notes:
                sections.append((path.name, "", notes))
    for name, url in OPEN_SOURCES:
        try:
            text = load_source(root, name, url, fetch)
            notes = extract_notes(text, limit=OPEN_NOTE_LIMIT)
        except Exception as exc:
            print(f"không tải {name}: {exc}")
            continue
        if notes:
            sections.append((name, url, notes))
    lines = ["# Kho đã học", ""]
    sach = learn / "sach"
    sach.mkdir(parents=True, exist_ok=True)
    highlights: list[str] = []
    total = 0
    for name, url, notes in sections:
        lines.append(f"## {name}")
        if url:
            lines.append(f"nguồn: {url}")
        topic = [f"# {name}", ""]
        if url:
            topic.append(f"nguồn: {url}")
            topic.append("")
        for note in notes:
            lines.append(f"- {note}")
            topic.append(f"- {note}")
            total += 1
        lines.append("")
        (sach / f"{name}.md").write_text("\n".join(topic).rstrip() + "\n", encoding="utf-8")
        highlights.append(notes[0])
    (learn / "da-hoc.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path = lesson_file(root)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(remember(existing, highlights), encoding="utf-8")
    return total


def lesson_file(root: Path) -> Path:
    path = root / "learn" / "kinh-nghiem.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def study_once(database: Path, root: Path, ask) -> int:
    db.init_db(database)
    pending = db.unstudied_messages(database)
    if not pending:
        return 0
    path = lesson_file(root)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    learned = 0
    for item in pending:
        prompt = (
            "Rút tối đa 3 bài học từ hội thoại. Mỗi bài một dòng, bắt đầu bằng '- '. "
            "Nếu không có gì đáng nhớ, trả lời đúng một dòng '- không có'.\n"
            f"Người dùng: {item['body'][:800]}\n"
            f"Trả lời: {item['answer'][:800]}"
        )
        try:
            reply = str(ask(prompt) or "")
        except Exception as exc:
            print(f"chưa học được {item['id']}: {exc}")
            return learned
        fresh = parse_lessons(reply)
        if fresh:
            existing = merge_lessons(existing, fresh)
            path.write_text(existing, encoding="utf-8")
        db.mark_studied(database, item["id"])
        learned += 1
    return learned


def ask_model(prompt: str) -> str:
    base = os.environ.get("MODEL_BASE_URL", "").rstrip("/")
    model = os.environ.get("MODEL_NAME", "")
    if not base or not model:
        raise RuntimeError("chưa cấu hình MODEL_BASE_URL")
    key = os.environ.get("MODEL_API_KEY", "")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 180,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = json.load(response)
    return str(data["choices"][0]["message"].get("content") or "")


def main() -> None:
    load_env()
    load_env_file(Path("/opt/coderd/coderd.env"))
    database = Path(os.environ.get("CODERD_DB") or "/opt/coderd/var/coderd.db")
    root = Path(os.environ.get("WORKSPACE_ROOT") or "/opt/coderd/var/workspace")
    try:
        ingested = ingest_sources(root)
        count = study_once(database, root, ask_model)
    except Exception as exc:
        print(f"vòng học dừng: {exc}")
        return
    print(f"nguồn {ingested}")
    print(f"học {count}")


if __name__ == "__main__":
    main()
