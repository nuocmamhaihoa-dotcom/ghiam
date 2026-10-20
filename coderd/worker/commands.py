from __future__ import annotations

import re
from pathlib import Path

LIMIT = 99_999

GROUPS = (
    ("python", "Python — chạy file, viết hàm, in, mở thư viện"),
    ("git", "Git — xem chỗ đã sửa, ghi commit, đổi nhánh"),
    ("shell", "Shell — liệt kê, tạo thư mục, tìm chữ, sao chép"),
    ("sqlite", "SQLite — mở file .db và câu SQL"),
    ("mang", "Mạng — gọi một địa chỉ HTTP"),
    ("he-thong", "Hệ thống — dịch vụ và tiến trình"),
    ("docker", "Docker — chạy container"),
    ("node", "Node — chạy JS, cài gói npm"),
    ("go", "Go — biên dịch và kiểm thử Go"),
    ("rust", "Rust — cargo và rustc"),
    ("java", "Java — javac, maven, gradle"),
    ("bien-dich", "Biên dịch — gcc, cmake, make, gdb"),
    ("csdl", "CSDL — postgres, mysql, redis"),
)

_PY_MODS = {
    "os",
    "sys",
    "json",
    "pathlib",
    "subprocess",
    "sqlite3",
    "re",
    "typing",
    "asyncio",
    "threading",
    "collections",
    "itertools",
    "functools",
    "io",
    "csv",
    "hashlib",
    "glob",
    "pickle",
    "socket",
    "traceback",
    "dataclasses",
    "enum",
    "copy",
    "math",
    "secrets",
    "gzip",
    "concurrent",
    "multiprocessing",
    "queue",
    "signal",
    "inspect",
    "base64",
    "uuid",
    "random",
    "configparser",
    "urllib",
    "logging",
    "argparse",
    "unittest",
    "tempfile",
    "shutil",
    "datetime",
    "pprint",
    "textwrap",
    "struct",
    "ssl",
    "html",
    "xml",
    "hmac",
    "decimal",
    "fractions",
    "operator",
    "contextlib",
    "abc",
    "pdb",
    "ast",
    "dis",
    "platform",
    "getpass",
    "tomllib",
    "statistics",
    "zipfile",
    "tarfile",
    "fnmatch",
    "linecache",
    "traceback",
}

_TOOLS = {
    "python": {
        "python",
        "python3",
        "pip",
        "pip3",
        "pytest",
        "pydoc",
        "poetry",
        "uv",
        "pipenv",
        "mypy",
        "ruff",
        "black",
        "pylint",
        "flake8",
        "ipython",
        "pyenv",
        "twine",
        "tox",
        "nox",
    },
    "git": {"git", "gh"},
    "sqlite": {"sqlite3"},
    "mang": {"curl", "wget", "ping", "nc", "netcat", "http", "https", "httpie", "jq", "yq"},
    "he-thong": {"systemctl", "journalctl", "ps", "kill", "pkill", "top", "htop", "df", "du", "free", "uname"},
    "docker": {"docker", "docker-compose", "podman"},
    "node": {"node", "nodejs", "npm", "npx", "yarn", "pnpm", "bun"},
    "go": {"go", "gofmt", "gotest"},
    "rust": {"cargo", "rustc", "rustup", "rustfmt"},
    "java": {"java", "javac", "mvn", "gradle", "gradlew"},
    "bien-dich": {"gcc", "g++", "clang", "clang++", "cmake", "meson", "ninja", "make", "gdb", "lldb", "ld", "ar", "objdump", "nm"},
    "csdl": {"psql", "mysql", "mysqldump", "redis-cli", "mongo", "mongosh"},
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
    ("go test", "Chạy kiểm thử Go"),
    ("go build", "Biên dịch gói Go"),
    ("go mod", "Quản lý module Go"),
    ("cargo test", "Chạy kiểm thử Rust"),
    ("cargo build", "Biên dịch crate Rust"),
    ("gcc", "Biên dịch C"),
    ("cmake", "Sinh file build"),
    ("gdb", "Gỡ lỗi chương trình"),
    ("mvn test", "Chạy kiểm thử Maven"),
    ("javac", "Biên dịch Java"),
    ("poetry add", "Thêm gói Poetry"),
    ("uv pip", "Cài gói bằng uv"),
    ("ruff check", "Kiểm tra kiểu và lỗi Python"),
    ("mypy", "Kiểm tra kiểu tĩnh Python"),
    ("jq", "Lọc JSON"),
    ("psql", "Mở PostgreSQL"),
    ("redis-cli", "Gọi Redis"),
    ("npm install", "Cài gói Node"),
    ("npm run", "Chạy một script npm"),
    ("npx", "Chạy một gói npm tạm"),
    ("node", "Chạy JavaScript"),
    ("yarn add", "Thêm gói bằng yarn"),
    ("pnpm add", "Thêm gói bằng pnpm"),
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
    "docker compose up -d",
    "node file.js",
    "npm install",
    "npx --yes goi",
    "curl -fsSL URL",
    "go test ./...",
    "go build -o app .",
    "cargo test",
    "cargo build --release",
    "gcc -O2 -o app main.c",
    "cmake -S . -B build",
    "make -C build",
    "gdb ./app",
    "javac Main.java",
    "mvn test",
    "poetry add goi",
    "uv pip install goi",
    "ruff check .",
    "mypy src",
    "jq .key file.json",
    "psql -d app",
    "redis-cli ping",
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
    token = re.split(r"[(]", first)[0]
    mod = token.split(".")[0].lower()
    if mod in _PY_MODS or token.lower().startswith("path"):
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
        if not command or len(command) > 220 or len(command.split()) > 24:
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


def _flush_groups(root: Path, groups: dict[str, list[tuple[str, str, str]]], limit: int) -> None:
    folder = root / "learn" / "lenh"
    folder.mkdir(parents=True, exist_ok=True)
    for name, title in GROUPS:
        rows = groups.get(name, [])[:limit]
        lines = [f"# {title}", "", "Nói tiếng Việt rồi mở file này khi cần nhóm lệnh này.", ""]
        for command, meaning, origin in rows:
            tail = f" — {meaning}" if meaning else ""
            where = f". Nguồn: {origin}" if origin else ""
            lines.append(f"- `{command}`{tail}{where}")
        (folder / f"{name}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    index = ["# Lệnh lập trình", "", "Người dùng nói tiếng Việt. Mở đúng một file.", ""]
    for name, title in GROUPS:
        index.append(f"- {name}.md: {title}")
    (folder / "muc-luc.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    write_phrases(folder)


def _absorb(groups: dict[str, list[tuple[str, str, str]]], seen: dict[str, set[str]], text: str, source: str) -> int:
    added = 0
    for command in extract_commands(text):
        name = group_of(command)
        if not name:
            continue
        key = command.lower()
        if key in seen[name]:
            continue
        seen[name].add(key)
        groups[name].append((command, gloss(command), source))
        added += 1
    return added


def store_commands(root: Path, text: str, source: str, limit: int = LIMIT) -> int:
    folder = root / "learn" / "lenh"
    folder.mkdir(parents=True, exist_ok=True)
    groups = {name: _read_group(folder / f"{name}.md") for name, _title in GROUPS}
    seen = {name: {command.lower() for command, _g, _s in rows} for name, rows in groups.items()}
    added = _absorb(groups, seen, text, source)
    if added:
        _flush_groups(root, groups, limit)
    elif not (folder / "muc-luc.md").is_file():
        _flush_groups(root, groups, limit)
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


def harvest_saved(root: Path, limit: int = LIMIT) -> int:
    learn = root / "learn"
    if not learn.is_dir():
        learn.mkdir(parents=True, exist_ok=True)
    folder = learn / "lenh"
    folder.mkdir(parents=True, exist_ok=True)
    groups = {name: _read_group(folder / f"{name}.md") for name, _title in GROUPS}
    seen = {name: {command.lower() for command, _gloss, _source in rows} for name, rows in groups.items()}
    added = _absorb(groups, seen, "\n".join(f"`{item}`" for item in SEED), "nen")
    cache = learn / "cache"
    if cache.is_dir():
        for path in sorted(cache.glob("*.txt")):
            added += _absorb(groups, seen, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    for path in sorted(learn.glob("*.md")):
        added += _absorb(groups, seen, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    for folder_name in ("sach", "the"):
        nested = learn / folder_name
        if not nested.is_dir():
            continue
        for path in sorted(nested.glob("*.md")):
            added += _absorb(groups, seen, path.read_text(encoding="utf-8", errors="replace"), path.stem)
    _flush_groups(root, groups, limit)
    return added
