from __future__ import annotations

import re
from pathlib import Path

GROUPS = (
    ("python", "Python — chạy file, viết hàm, in, mở thư viện"),
    ("git", "Git — xem chỗ đã sửa, ghi commit, đổi nhánh"),
    ("shell", "Shell — liệt kê, tạo thư mục, tìm chữ, sao chép"),
    ("sqlite", "SQLite — mở file .db và câu SQL"),
    ("mang", "Mạng — gọi một địa chỉ HTTP"),
    ("he-thong", "Hệ thống — dịch vụ và tiến trình"),
)

_TOOLS = {
    "python": {"python", "python3", "pip", "pip3", "pytest"},
    "git": {"git"},
    "sqlite": {"sqlite3"},
    "mang": {"curl", "wget"},
    "he-thong": {"systemctl", "journalctl"},
    "shell": {
        "ls",
        "cd",
        "pwd",
        "mkdir",
        "rm",
        "cp",
        "mv",
        "cat",
        "grep",
        "sed",
        "awk",
        "chmod",
        "chown",
        "find",
        "echo",
        "touch",
        "head",
        "tail",
        "sort",
        "wc",
        "tar",
        "ssh",
        "scp",
    },
}

_GLOSS = (
    ("git status", "Xem những file đã đổi"),
    ("git add", "Chọn file để ghi vào commit"),
    ("git commit", "Ghi một mốc thay đổi"),
    ("git diff", "Xem nội dung vừa sửa"),
    ("git log", "Xem các mốc đã ghi"),
    ("git clone", "Tải một kho về máy"),
    ("git push", "Đẩy commit lên máy khác"),
    ("git pull", "Kéo commit từ máy khác về"),
    ("git fetch", "Tải tin commit mới, chưa trộn"),
    ("git branch", "Xem hoặc tạo nhánh"),
    ("git switch", "Đổi sang nhánh khác"),
    ("git checkout", "Đổi nhánh hoặc lấy lại file"),
    ("git merge", "Trộn một nhánh vào nhánh hiện tại"),
    ("git init", "Tạo kho git mới"),
    ("git restore", "Bỏ phần sửa chưa ghi commit"),
    ("git show", "Xem một commit"),
    ("git remote", "Xem kho ở máy khác"),
    ("python3", "Chạy Python 3"),
    ("python", "Chạy Python"),
    ("pip install", "Cài một gói Python"),
    ("pytest", "Chạy bài kiểm thử"),
    ("sqlite3", "Mở một file SQLite"),
    ("ls", "Liệt kê file trong thư mục"),
    ("mkdir", "Tạo thư mục"),
    ("rm", "Xóa file"),
    ("cp", "Sao chép file"),
    ("mv", "Đổi tên hoặc chuyển file"),
    ("cat", "In nội dung file"),
    ("grep", "Tìm một chữ trong file"),
    ("chmod", "Đổi quyền của file"),
    ("curl", "Gọi một địa chỉ mạng"),
    ("wget", "Tải một địa chỉ mạng"),
    ("systemctl", "Bật, tắt hoặc xem dịch vụ"),
    ("journalctl", "Xem nhật ký dịch vụ"),
    ("print(", "In một giá trị"),
    ("def ", "Khai báo một hàm"),
    ("class ", "Khai báo một lớp"),
    ("import ", "Lấy một thư viện vào chương trình"),
    ("path(", "Làm việc với đường dẫn file"),
    ("sqlite3.connect", "Mở một file cơ sở dữ liệu"),
    ("select ", "Lấy dòng từ bảng"),
    ("insert ", "Thêm một dòng vào bảng"),
    ("create table", "Tạo một bảng"),
)


def gloss(command: str) -> str:
    low = command.lower()
    for prefix, text in _GLOSS:
        if low.startswith(prefix):
            return text
    return ""


def group_of(command: str) -> str:
    text = command.strip()
    if not text:
        return ""
    first = text.split()[0].rsplit("/", 1)[-1].lower()
    for name, tools in _TOOLS.items():
        if first in tools:
            return name
    low = text.lower()
    if low.startswith(("select ", "insert ", "update ", "delete ", "create table")):
        return "sqlite"
    if low.startswith(("print(", "def ", "class ", "import ", "from ", "sqlite3.connect", "path(")):
        return "python"
    return ""


def extract_commands(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        command = " ".join(raw.strip().split())
        if command.startswith("$ "):
            command = command[2:].strip()
        if command.startswith(">>> ") or command.startswith("... "):
            command = command[4:].strip()
        if not command or len(command) > 180 or len(command.split()) > 18:
            return
        if not group_of(command):
            return
        key = command.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(command)

    for match in re.findall(r"`([^`\n]{2,180})`", text):
        add(match)
    for raw in text.splitlines():
        if raw.startswith("    ") or raw.startswith("\t") or raw.strip().startswith(("`", "$", ">>>", "...")):
            add(raw)
        elif group_of(raw):
            add(raw)
    return found


def _read_group(path: Path) -> list[tuple[str, str, str]]:
    if not path.is_file():
        return []
    rows: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- `"):
            continue
        command = line.split("`", 2)[1] if line.count("`") >= 2 else ""
        source = ""
        if "Nguồn:" in line:
            source = line.split("Nguồn:", 1)[1].strip()
        if command and group_of(command):
            rows.append((command, gloss(command), source))
    return rows


def store_commands(root: Path, text: str, source: str, limit: int = 500) -> int:
    fresh = extract_commands(text)
    if not fresh:
        return 0
    folder = root / "learn" / "lenh"
    folder.mkdir(parents=True, exist_ok=True)
    added = 0
    for name, title in GROUPS:
        path = folder / f"{name}.md"
        rows = _read_group(path)
        seen = {command.lower() for command, _gloss, _source in rows}
        for command in fresh:
            if group_of(command) != name or command.lower() in seen:
                continue
            seen.add(command.lower())
            rows.append((command, gloss(command), source))
            added += 1
        rows = rows[:limit]
        lines = [f"# {title}", "", "Nói tiếng Việt rồi mở file này khi cần nhóm lệnh này.", ""]
        for command, meaning, origin in rows:
            tail = f" — {meaning}" if meaning else ""
            where = f". Nguồn: {origin}" if origin else ""
            lines.append(f"- `{command}`{tail}{where}")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    index = ["# Lệnh lập trình", "", "Người dùng nói tiếng Việt. Mở đúng một file.", ""]
    for name, title in GROUPS:
        index.append(f"- {name}.md: {title}")
    (folder / "muc-luc.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return added


def harvest_saved(root: Path) -> int:
    learn = root / "learn"
    if not learn.is_dir():
        return 0
    total = 0
    cache = learn / "cache"
    if cache.is_dir():
        for path in sorted(cache.glob("*.txt")):
            total += store_commands(root, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    for path in sorted(learn.glob("*.md")):
        total += store_commands(root, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    for folder_name in ("sach", "the"):
        folder = learn / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            total += store_commands(root, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    return total
