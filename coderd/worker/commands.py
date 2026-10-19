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
    ("docker", "Docker — chạy container"),
)

_TOOLS = {
    "python": {"python", "python3", "pip", "pip3", "pytest", "pydoc"},
    "git": {"git"},
    "sqlite": {"sqlite3"},
    "mang": {"curl", "wget", "ping", "nc", "netcat"},
    "he-thong": {"systemctl", "journalctl", "ps", "kill", "pkill", "top", "htop", "df", "du", "free", "uname"},
    "docker": {"docker", "docker-compose", "podman"},
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
        "rsync",
        "tee",
        "xargs",
        "cut",
        "tr",
        "uniq",
        "ln",
        "which",
        "env",
        "date",
        "sleep",
        "less",
        "diff",
        "patch",
        "make",
        "bash",
        "sh",
        "export",
        "source",
        "alias",
        "history",
        "man",
        "true",
        "false",
        "test",
        "printf",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "stat",
        "file",
        "gzip",
        "gunzip",
        "zip",
        "unzip",
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
    ("git rebase", "Đặt lại commit lên một nhánh khác"),
    ("git cherry-pick", "Lấy một commit sang nhánh hiện tại"),
    ("git revert", "Tạo commit đảo ngược"),
    ("git reset", "Lùi con trỏ commit"),
    ("git stash", "Cất tạm phần sửa chưa commit"),
    ("git tag", "Gắn mốc phiên bản"),
    ("git rm", "Xóa file khỏi git"),
    ("git mv", "Đổi tên file trong git"),
    ("git blame", "Xem ai sửa từng dòng"),
    ("git grep", "Tìm chữ trong kho"),
    ("git clean", "Xóa file chưa theo dõi"),
    ("git bisect", "Tìm commit làm hỏng"),
    ("git reflog", "Xem lịch sử con trỏ HEAD"),
    ("git remote", "Xem kho ở máy khác"),
    ("git config", "Đọc hoặc ghi cấu hình git"),
    ("git show", "Xem một commit"),
    ("git init", "Tạo kho git mới"),
    ("git restore", "Bỏ phần sửa chưa ghi commit"),
    ("git worktree", "Mở thêm một bản làm việc"),
    ("git submodule", "Làm việc với kho con"),
    ("python3 -m venv", "Tạo môi trường Python riêng"),
    ("python3 -m pip", "Cài gói bằng pip của Python 3"),
    ("python3 -m pytest", "Chạy bài kiểm thử"),
    ("python3 -m http.server", "Mở máy chủ HTTP tạm"),
    ("python3", "Chạy Python 3"),
    ("python", "Chạy Python"),
    ("pip install", "Cài một gói Python"),
    ("pip uninstall", "Gỡ một gói Python"),
    ("pip freeze", "Liệt kê gói đã cài"),
    ("pytest", "Chạy bài kiểm thử"),
    ("sqlite3", "Mở một file SQLite"),
    ("ls", "Liệt kê file trong thư mục"),
    ("mkdir", "Tạo thư mục"),
    ("rm", "Xóa file"),
    ("cp", "Sao chép file"),
    ("mv", "Đổi tên hoặc chuyển file"),
    ("cat", "In nội dung file"),
    ("grep", "Tìm một chữ trong file"),
    ("find", "Tìm file theo tên"),
    ("chmod", "Đổi quyền của file"),
    ("chown", "Đổi chủ file"),
    ("tar", "Nén hoặc giải nén tar"),
    ("rsync", "Đồng bộ file giữa hai chỗ"),
    ("ssh", "Đăng nhập máy khác"),
    ("scp", "Chép file qua SSH"),
    ("curl", "Gọi một địa chỉ mạng"),
    ("wget", "Tải một địa chỉ mạng"),
    ("ping", "Thử máy kia còn sống"),
    ("systemctl", "Bật, tắt hoặc xem dịch vụ"),
    ("journalctl", "Xem nhật ký dịch vụ"),
    ("ps", "Liệt kê tiến trình"),
    ("kill", "Dừng một tiến trình"),
    ("df", "Xem dung lượng đĩa"),
    ("du", "Xem dung lượng thư mục"),
    ("free", "Xem RAM còn trống"),
    ("uname", "Xem tên hệ điều hành"),
    ("docker run", "Chạy một container"),
    ("docker ps", "Liệt kê container"),
    ("docker build", "Dựng image"),
    ("docker compose", "Chạy nhiều container"),
    ("print(", "In một giá trị"),
    ("def ", "Khai báo một hàm"),
    ("class ", "Khai báo một lớp"),
    ("import ", "Lấy một thư viện vào chương trình"),
    ("from ", "Lấy tên từ một thư viện"),
    ("path(", "Làm việc với đường dẫn file"),
    ("sqlite3.connect", "Mở một file cơ sở dữ liệu"),
    ("select ", "Lấy dòng từ bảng"),
    ("insert ", "Thêm một dòng vào bảng"),
    ("update ", "Sửa dòng trong bảng"),
    ("delete ", "Xóa dòng trong bảng"),
    ("create table", "Tạo một bảng"),
)

SEED = (
    "git status",
    "git add .",
    "git commit -m \"mo ta\"",
    "git diff",
    "git log --oneline",
    "git clone URL",
    "git push",
    "git pull",
    "git fetch",
    "git branch",
    "git switch ten-nhanh",
    "git checkout -b ten-nhanh",
    "git merge ten-nhanh",
    "git restore file",
    "git init",
    "git remote -v",
    "git show",
    "git stash",
    "git stash pop",
    "git rebase main",
    "git config --list",
    "python3 file.py",
    "python3 -m venv .venv",
    "python3 -m pip install -r requirements.txt",
    "python3 -m pytest",
    "python3 -m http.server 8090",
    "pip install goi",
    "pytest",
    "ls -la",
    "pwd",
    "mkdir -p thu-muc",
    "rm file",
    "cp nguon dich",
    "mv cu moi",
    "cat file",
    "grep chu file",
    "find . -name '*.py'",
    "chmod 755 file",
    "tar -czf goi.tgz thu-muc",
    "rsync -az nguon/ dich/",
    "ssh user@may",
    "curl -I http://127.0.0.1:8090/health",
    "wget -O file URL",
    "sqlite3 file.db '.tables'",
    "SELECT * FROM t",
    "INSERT INTO t VALUES (1)",
    "CREATE TABLE t (id INTEGER)",
    "systemctl status coderd",
    "journalctl -u coderd -n 50",
    "ps aux",
    "df -h",
    "free -h",
    "docker ps",
    "print(1)",
    "def ten():",
    "import os",
    "from pathlib import Path",
    "sqlite3.connect('file.db')",
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
    if first in {"sudo", "nohup", "time", "env"} and len(text.split()) > 1:
        return group_of(" ".join(text.split()[1:]))
    for name, tools in _TOOLS.items():
        if first in tools:
            return name
    low = text.lower()
    if low.startswith(("select ", "insert ", "update ", "delete ", "create table", "pragma ")):
        return "sqlite"
    if low.startswith(("print(", "def ", "class ", "import ", "from ", "sqlite3.connect", "path(")):
        return "python"
    if first == "docker-compose" or low.startswith("docker compose"):
        return "docker"
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
        command = command.strip("`")
        if command.startswith("# ") and group_of(command[2:]):
            command = command[2:].strip()
        parts = command.split()
        if parts and re.match(r"^git-[a-z0-9-]+$", parts[0]):
            command = "git " + parts[0][4:]
            parts = command.split()
        if " - " in command:
            head = command.split(" - ", 1)[0].strip()
            if group_of(head):
                command = head
        if not command or len(command) > 180 or len(command.split()) > 18:
            return
        if command.endswith("\\"):
            return
        if not group_of(command):
            return
        first = command.split()[0].lower()
        if first in {"this", "with", "from", "imports", "using", "see", "the", "for", "and"}:
            if not command.lower().startswith("from "):
                return
        key = command.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(command)

    for match in re.findall(r"`([^`\n]{2,180})`", text):
        add(match)
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- `") or stripped.startswith("* `"):
            add(stripped)
        elif raw.startswith("    ") or raw.startswith("\t") or stripped.startswith(("$", ">>>", "...")):
            add(raw)
        elif group_of(raw) or stripped.startswith("git-"):
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


def store_commands(root: Path, text: str, source: str, limit: int = 800) -> int:
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
    write_phrases(folder)
    return added


def write_phrases(folder: Path) -> None:
    lines = [
        "# Nói tiếng Việt",
        "",
        "Người dùng nói tiếng Việt. Đổi thành lệnh, rồi mở đúng nhóm.",
        "",
    ]
    for prefix, meaning in _GLOSS:
        lines.append(f"- {meaning}: `{prefix.strip()}`")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tieng-viet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_commands(root: Path) -> int:
    return store_commands(root, "\n".join(f"`{item}`" for item in SEED), "nen")


def harvest_saved(root: Path) -> int:
    learn = root / "learn"
    if not learn.is_dir():
        return 0
    total = seed_commands(root)
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
