from __future__ import annotations

import json
import os
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


OPEN_SOURCES = (
    ("python-nhap-mon", "https://raw.githubusercontent.com/python/cpython/3.12/Doc/tutorial/introduction.rst"),
    ("python-dieu-khien", "https://raw.githubusercontent.com/python/cpython/3.12/Doc/tutorial/controlflow.rst"),
    ("git-init", "https://raw.githubusercontent.com/git/git/master/Documentation/git-init.adoc"),
)
SKIP_FILES = {"da-hoc.md", "kinh-nghiem.md", "muc-luc.md"}


def extract_notes(text: str, limit: int = 8) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        if raw.startswith("   ") or raw.startswith("\t"):
            continue
        line = raw.strip()
        if not line or line.startswith("..") or line.startswith("|") or line.startswith(":"):
            continue
        if set(line) <= set("*-= ") or line.endswith("::"):
            continue
        if line.startswith("- "):
            item = line[2:].strip()
        elif 40 <= len(line) <= 220 and not line.startswith("["):
            item = line
        else:
            continue
        if not keep_lesson(item) or item.lower() in seen:
            continue
        seen.add(item.lower())
        notes.append(item[:240])
        if len(notes) >= limit:
            break
    return notes


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "coderd-study"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(100_000)
    return data.decode("utf-8", "replace")


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
            notes = extract_notes(fetch(url), limit=6)
        except Exception as exc:
            print(f"không tải {name}: {exc}")
            continue
        if notes:
            sections.append((name, url, notes))
    lines = ["# Kho đã học", ""]
    flat: list[str] = []
    for name, url, notes in sections:
        lines.append(f"## {name}")
        if url:
            lines.append(f"nguồn: {url}")
        for note in notes:
            lines.append(f"- {note}")
            flat.append(note)
        lines.append("")
    (learn / "da-hoc.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path = lesson_file(root)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(merge_lessons(existing, flat), encoding="utf-8")
    return len(flat)


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
