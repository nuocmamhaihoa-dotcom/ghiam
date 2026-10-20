from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from worker.commands import GROUPS, SEED, _GLOSS, _read_group, gloss, group_of, seed_commands
from worker.use import _how, _match_skills, _stems

_WRITE = re.compile(
    r"tạo\s+\S+\.py|hello\.py|ghi file|viết file|tạo file|in\s*2\s*\+\s*2|\bsleep\b",
    re.I,
)
_COUNT = re.compile(r"bao nhiêu|đếm|how many", re.I)
_INDEX = re.compile(r"mục lục|muc-luc|những nhóm|các nhóm|dung\.md", re.I)
_SAFE_HEADS = (
    "git status",
    "git diff",
    "git log",
    "git branch",
    "git show",
    "git remote",
    "ls",
    "pwd",
    "docker ps",
    "df",
    "du ",
    "free",
    "uname",
    "ps ",
    "python3 -m pytest",
    "pytest",
)
_CHAINS = (
    (
        ("file đã đổi", "những file đã đổi", "chỗ đã sửa", "đã sửa gì", "xem git", "git status"),
        ("git status", "git diff"),
        "Xem chỗ đã đổi",
    ),
    (
        ("ghi commit", "ghi một mốc", "tạo commit", "commit thay đổi"),
        ("git status", "git add .", 'git commit -m "mo ta"'),
        "Ghi một mốc thay đổi",
    ),
    (
        ("đẩy lên", "git push", "đẩy commit"),
        ("git status", "git add .", 'git commit -m "mo ta"', "git push"),
        "Đẩy thay đổi lên máy khác",
    ),
    (
        ("kéo về", "git pull", "kéo commit"),
        ("git fetch", "git pull"),
        "Kéo commit về",
    ),
    (
        ("kiểm thử", "chạy pytest", "chạy test", "pytest"),
        ("python3 -m pytest",),
        "Chạy bài kiểm thử",
    ),
    (
        ("môi trường python", "tạo venv", "venv"),
        ("python3 -m venv .venv", "python3 -m pip install -r requirements.txt"),
        "Tạo môi trường Python",
    ),
    (
        ("cài gói python", "pip install", "cài pip"),
        ("python3 -m pip install -r requirements.txt", "pip freeze"),
        "Cài gói Python",
    ),
    (
        ("docker ps", "liệt kê container", "xem container", "dùng docker"),
        ("docker ps",),
        "Liệt kê container",
    ),
    (
        ("liệt kê file", "liệt kê thư mục", "xem thư mục", "ls -la"),
        ("ls -la", "pwd"),
        "Liệt kê file",
    ),
)


@dataclass
class Step:
    command: str
    meaning: str
    group: str
    run: bool
    skill: str = ""


@dataclass
class Scene:
    title: str
    steps: list[Step]
    extra: str = ""


def needs_model_tools(question: str) -> bool:
    text = question or ""
    if _WRITE.search(text):
        return True
    low = text.lower()
    return bool(re.search(r"tạo |viết |ghi file|hello|\.py", low))


def is_safe(command: str) -> bool:
    low = " ".join((command or "").strip().lower().split())
    if not low:
        return False
    for head in _SAFE_HEADS:
        if low == head.strip() or low.startswith(head):
            if head == "ls" and not (low == "ls" or low.startswith("ls ") or low.startswith("ls-")):
                continue
            return True
    return False


def _has_term(text: str, term: str) -> bool:
    if not term:
        return False
    if len(term) <= 3:
        return re.search(rf"(^|\s){re.escape(term)}(\s|$)", text) is not None
    return term in text


def _load_groups(root: Path) -> dict[str, list[tuple[str, str, str]]]:
    folder = root / "learn" / "lenh"
    return {name: _read_group(folder / f"{name}.md") for name, _title in GROUPS}


def _ensure_store(root: Path) -> None:
    if not (root / "learn" / "lenh" / "muc-luc.md").is_file():
        seed_commands(root)


def command_counts(root: Path) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    folder = root / "learn" / "lenh"
    for name, title in GROUPS:
        rows.append((name, title, len(_read_group(folder / f"{name}.md"))))
    return rows


def _pick(prefix: str, groups: dict[str, list[tuple[str, str, str]]]) -> tuple[str, str, str]:
    meaning = gloss(prefix)
    group = group_of(prefix) or group_of(prefix + " x")
    low = prefix.lower()
    if group:
        for command, text, _src in groups.get(group) or []:
            if command.lower().startswith(low):
                return command, text or meaning, group
    for item in SEED:
        if item.lower().startswith(low):
            return item, gloss(item) or meaning, group_of(item) or group
    return prefix, meaning, group


def _score_gloss(text: str) -> list[tuple[int, str, str]]:
    scored: list[tuple[int, str, str]] = []
    for prefix, meaning in _GLOSS:
        score = 0
        low_meaning = meaning.lower()
        low_prefix = prefix.strip().lower()
        if low_meaning and low_meaning in text:
            score += 6
        if low_prefix and low_prefix in text:
            score += 5
        for word in re.findall(
            r"[a-zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]{4,}",
            low_meaning,
        ):
            if word in text:
                score += 1
        if score:
            scored.append((score, prefix, meaning))
    scored.sort(key=lambda item: (-item[0], item[2]))
    return scored


def _count_scene(root: Path) -> Scene:
    rows = command_counts(root)
    total = sum(count for _name, _title, count in rows)
    lines = [f"Hiện có {total} câu lệnh đã gom, chia theo nhóm:"]
    for name, title, count in rows:
        lines.append(f"- {title}: {count} — `learn/lenh/{name}.md`")
    lines.append("Nói việc cần làm bằng tiếng Việt, tôi tự tìm lệnh và ghép kịch bản.")
    return Scene(title="Kho câu lệnh", steps=[], extra="\n".join(lines))


def _index_scene(root: Path) -> Scene:
    rows = command_counts(root)
    total = sum(count for _name, _title, count in rows)
    lines = [f"Mục lục {len(rows)} nhóm, {total} lệnh. Mở đúng một file nhóm khi cần."]
    for name, title, count in rows:
        lines.append(f"- `{name}.md` — {title} ({count})")
    return Scene(title="Mục lục lệnh", steps=[], extra="\n".join(lines))


def _steps_for(
    prefixes: list[str],
    groups: dict[str, list[tuple[str, str, str]]],
    stems: list[str],
    run_ok: bool,
) -> list[Step]:
    steps: list[Step] = []
    seen: set[str] = set()
    for prefix in prefixes:
        command, meaning, group = _pick(prefix, groups)
        key = command.lower()
        if not command or key in seen:
            continue
        seen.add(key)
        skills = _match_skills(command, stems)
        steps.append(
            Step(
                command=command,
                meaning=meaning,
                group=group,
                run=run_ok and is_safe(command),
                skill=skills[0] if skills else "",
            )
        )
    return steps


def plan_scene(root: Path, question: str) -> Scene | None:
    text = (question or "").strip()
    if not text or _WRITE.search(text):
        return None
    low = text.lower()
    if len(low) < 8 and not any(meaning.lower() == low for _prefix, meaning in _GLOSS):
        return None
    _ensure_store(root)
    groups = _load_groups(root)
    stems = _stems(root)
    if _COUNT.search(low) and ("lệnh" in low or "command" in low):
        return _count_scene(root)
    if _INDEX.search(low):
        return _index_scene(root)
    for keys, prefixes, title in _CHAINS:
        if any(key in low for key in keys):
            return Scene(title=title, steps=_steps_for(list(prefixes), groups, stems, True))
    scored = _score_gloss(low)
    prefixes: list[str] = []
    seen: set[str] = set()
    for score, prefix, _meaning in scored:
        if score < 6 and not (score >= 5 and prefix.strip().lower() in low):
            continue
        key = prefix.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        prefixes.append(prefix)
        if len(prefixes) >= 6:
            break
    if prefixes:
        title = scored[0][2] if scored else "Kịch bản lệnh"
        return Scene(title=title, steps=_steps_for(prefixes, groups, stems, True))
    group_hits: list[str] = []
    for name, title in GROUPS:
        label = title.split("—")[0].strip().lower()
        if _has_term(low, name) or (len(label) >= 4 and label in low):
            group_hits.append(name)
    if len(group_hits) == 1:
        name = group_hits[0]
        title = dict(GROUPS)[name]
        rows = groups.get(name) or []
        steps: list[Step] = []
        for command, meaning, _src in rows[:5]:
            skills = _match_skills(command, stems)
            steps.append(
                Step(
                    command=command,
                    meaning=meaning,
                    group=name,
                    run=is_safe(command),
                    skill=skills[0] if skills else "",
                )
            )
        if steps:
            return Scene(title=title.split("—")[0].strip(), steps=steps)
    return None


def render_scene(scene: Scene, root: Path | None = None) -> str:
    parts: list[str] = []
    if scene.title:
        parts.append("Kịch bản: " + scene.title)
    if scene.extra:
        parts.append(scene.extra.strip())
    if scene.steps:
        numbered: list[str] = []
        for index, step in enumerate(scene.steps, 1):
            bit = f"{index}. `{step.command}`"
            if step.meaning:
                bit += f" — {step.meaning}"
            if step.group:
                bit += f" — learn/lenh/{step.group}.md"
            if step.skill:
                bit += f" — learn/the/{step.skill}.md"
                how = _how(root, step.skill) if root is not None else ""
                if how:
                    bit += f" — {how}"
            numbered.append(bit)
        parts.append("\n".join(numbered))
        if any(step.run for step in scene.steps):
            parts.append("Tôi chạy bước an toàn.")
    return "\n\n".join(parts).rstrip() + "\n"
