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
        count = study_once(database, root, ask_model)
    except Exception as exc:
        print(f"vòng học dừng: {exc}")
        return
    print(f"học {count}")


if __name__ == "__main__":
    main()
