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
    ("php", "PHP — chạy file, Composer, PHPUnit"),
    ("ruby", "Ruby — gem, bundler, Rails"),
    ("ngon-ngu", "Ngôn ngữ khác — Perl, Lua, Dart, .NET, Zig"),
    ("devops", "DevOps — kubectl, terraform, ansible, helm"),
    ("web", "Web — nginx, caddy, apache"),
    ("bao-mat", "Bảo mật — openssl, gpg, ssh-keygen"),
    ("dam-may", "Đám mây — aws, gcloud, az, wrangler"),
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
    "ipaddress",
    "email",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "http",
    "xmlrpc",
    "wsgiref",
    "ctypes",
    "mmap",
    "array",
    "select",
    "selectors",
    "zoneinfo",
    "graphlib",
    "numbers",
    "string",
    "unicodedata",
    "locale",
    "gettext",
    "cmd",
    "shlex",
    "fileinput",
    "netrc",
    "mailbox",
    "mimetypes",
    "quopri",
    "uu",
    "binascii",
    "codecs",
    "encodings",
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
        "conda",
        "jupyter",
        "pdm",
        "hatch",
        "pyright",
        "sphinx-build",
        "pipx",
        "coverage",
        "isort",
        "bandit",
        "pip-compile",
    },
    "git": {"git", "gh"},
    "sqlite": {"sqlite3"},
    "mang": {
        "curl",
        "wget",
        "ping",
        "nc",
        "netcat",
        "http",
        "https",
        "httpie",
        "jq",
        "yq",
        "dig",
        "nslookup",
        "ss",
        "ip",
        "traceroute",
        "nmap",
        "socat",
        "ncat",
        "netstat",
        "whois",
        "mtr",
        "iperf3",
        "nmcli",
        "resolvectl",
        "tcpdump",
    },
    "he-thong": {
        "systemctl",
        "journalctl",
        "ps",
        "kill",
        "pkill",
        "top",
        "htop",
        "df",
        "du",
        "free",
        "uname",
        "lsof",
        "strace",
        "perf",
        "dmesg",
        "vmstat",
        "iostat",
        "apt",
        "apt-get",
        "apt-cache",
        "dpkg",
        "snap",
        "yum",
        "dnf",
        "pacman",
        "apk",
        "brew",
        "nix",
        "nix-shell",
        "nix-env",
        "crontab",
        "tmux",
        "screen",
        "lscpu",
        "lsblk",
        "mount",
        "umount",
        "sysctl",
        "hostnamectl",
        "useradd",
        "usermod",
        "passwd",
        "ufw",
        "iptables",
        "nft",
        "lscpu",
    },
    "docker": {"docker", "docker-compose", "podman", "nerdctl", "buildah", "skopeo", "ctr", "buildx", "dive", "crane", "compose"},
    "node": {
        "node",
        "nodejs",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bun",
        "deno",
        "vite",
        "webpack",
        "eslint",
        "prettier",
        "tsc",
        "nuxi",
        "nest",
        "next",
        "prisma",
        "pm2",
        "nodemon",
        "tsx",
        "ts-node",
        "corepack",
        "gulp",
    },
    "go": {"go", "gofmt", "gotest"},
    "rust": {"cargo", "rustc", "rustup", "rustfmt", "clippy-driver"},
    "java": {"java", "javac", "mvn", "gradle", "gradlew", "ant", "kotlin", "kotlinc", "groovy"},
    "bien-dich": {
        "gcc",
        "g++",
        "clang",
        "clang++",
        "cmake",
        "meson",
        "ninja",
        "make",
        "gdb",
        "lldb",
        "ld",
        "ar",
        "objdump",
        "nm",
        "bazel",
        "valgrind",
        "pkg-config",
        "autoconf",
        "automake",
        "libtool",
        "ldd",
        "strip",
        "readelf",
        "ccache",
        "ranlib",
        "addr2line",
        "objcopy",
        "nasm",
        "yasm",
    },
    "csdl": {
        "psql",
        "mysql",
        "mysqldump",
        "redis-cli",
        "mongo",
        "mongosh",
        "duckdb",
        "clickhouse-client",
        "cqlsh",
        "sqlite",
        "createdb",
        "dropdb",
        "pg_dump",
        "pg_restore",
        "mysqladmin",
        "redis-server",
    },
    "php": {"php", "composer", "phpunit", "phpstan", "psalm", "laravel", "artisan", "pecl"},
    "ruby": {"ruby", "gem", "bundle", "bundler", "rails", "rake", "irb", "rspec", "puma"},
    "ngon-ngu": {
        "perl",
        "cpan",
        "cpanm",
        "lua",
        "luajit",
        "luarocks",
        "dart",
        "flutter",
        "elixir",
        "mix",
        "erl",
        "erlc",
        "ghci",
        "stack",
        "cabal",
        "scala",
        "sbt",
        "clojure",
        "lein",
        "julia",
        "rscript",
        "zig",
        "nim",
        "nimble",
        "swift",
        "swiftc",
        "dotnet",
        "csc",
        "fsharpc",
        "crystal",
        "shards",
        "dmd",
        "dub",
    },
    "devops": {
        "kubectl",
        "kubeadm",
        "helm",
        "kustomize",
        "minikube",
        "kind",
        "k3s",
        "k3d",
        "skaffold",
        "kompose",
        "istioctl",
        "terraform",
        "terragrunt",
        "ansible",
        "ansible-playbook",
        "ansible-galaxy",
        "ansible-vault",
        "ansible-lint",
        "vagrant",
        "packer",
        "pulumi",
        "nomad",
        "consul",
        "vault",
        "helmfile",
        "argocd",
        "flux",
        "oc",
        "openshift",
    },
    "web": {"nginx", "caddy", "apache2", "apache2ctl", "apachectl", "httpd", "traefik", "haproxy", "envoy"},
    "bao-mat": {
        "openssl",
        "gpg",
        "gpg2",
        "ssh-keygen",
        "ssh-copy-id",
        "ssh-agent",
        "ssh-add",
        "certbot",
        "keytool",
        "age",
        "sops",
        "cfssl",
        "mkcert",
        "ssh-keyscan",
        "gpg-agent",
    },
    "dam-may": {
        "aws",
        "gcloud",
        "az",
        "doctl",
        "flyctl",
        "heroku",
        "vercel",
        "netlify",
        "wrangler",
        "sam",
        "serverless",
        "firebase",
        "supabase",
    },
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
        "seq",
        "yes",
        "paste",
        "join",
        "comm",
        "split",
        "nl",
        "od",
        "hexdump",
        "xxd",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "timeout",
        "watch",
        "parallel",
        "tree",
        "rg",
        "ripgrep",
        "fd",
        "fzf",
        "bat",
        "eza",
        "exa",
        "tldr",
        "mktemp",
        "rmdir",
        "column",
        "strings",
        "iconv",
        "vim",
        "nvim",
        "nano",
        "emacs",
        "cmp",
        "tac",
        "rev",
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
    ("php artisan", "Chạy lệnh Laravel"),
    ("php -S", "Mở máy chủ PHP tạm"),
    ("phpunit", "Chạy kiểm thử PHP"),
    ("composer install", "Cài gói PHP"),
    ("composer require", "Thêm gói PHP"),
    ("ruby", "Chạy Ruby"),
    ("bundle install", "Cài gem theo Gemfile"),
    ("bundle exec", "Chạy lệnh trong bundle"),
    ("rails s", "Chạy máy chủ Rails"),
    ("rails generate", "Sinh mã Rails"),
    ("gem install", "Cài một gem"),
    ("perl", "Chạy Perl"),
    ("lua", "Chạy Lua"),
    ("dart run", "Chạy chương trình Dart"),
    ("flutter run", "Chạy ứng dụng Flutter"),
    ("dotnet build", "Biên dịch .NET"),
    ("zig build", "Biên dịch Zig"),
    ("kubectl get", "Liệt kê tài nguyên Kubernetes"),
    ("kubectl apply", "Áp dụng file Kubernetes"),
    ("kubectl logs", "Xem nhật ký pod"),
    ("helm install", "Cài chart Helm"),
    ("terraform init", "Khởi tạo Terraform"),
    ("terraform plan", "Xem thay đổi Terraform"),
    ("terraform apply", "Áp dụng Terraform"),
    ("ansible-playbook", "Chạy playbook Ansible"),
    ("ansible-galaxy", "Cài role Ansible"),
    ("nginx -t", "Kiểm tra cấu hình nginx"),
    ("caddy run", "Chạy Caddy"),
    ("openssl req", "Tạo chứng chỉ hoặc CSR"),
    ("openssl x509", "Xem chứng chỉ X.509"),
    ("gpg --encrypt", "Mã hóa file bằng GPG"),
    ("ssh-keygen", "Tạo khóa SSH"),
    ("certbot", "Xin chứng chỉ Let's Encrypt"),
    ("aws s3", "Làm việc với S3"),
    ("gcloud", "Gọi Google Cloud"),
    ("az", "Gọi Azure CLI"),
    ("wrangler deploy", "Đẩy Cloudflare Worker"),
    ("deno run", "Chạy TypeScript bằng Deno"),
    ("vite", "Chạy Vite"),
    ("tsc", "Biên dịch TypeScript"),
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
    "php -S 127.0.0.1:8080",
    "php artisan migrate",
    "composer install",
    "phpunit",
    "ruby file.rb",
    "bundle install",
    "rails s -p 3000",
    "gem install goi",
    "perl file.pl",
    "lua file.lua",
    "dart run",
    "dotnet build",
    "zig build",
    "kubectl get pods",
    "kubectl apply -f file.yaml",
    "helm install ten chart",
    "terraform init",
    "terraform plan",
    "terraform apply",
    "ansible-playbook play.yml",
    "nginx -t",
    "caddy run",
    "openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes",
    "ssh-keygen -t ed25519",
    "gpg --encrypt file",
    "aws s3 ls",
    "gcloud config list",
    "az account show",
    "wrangler deploy",
    "deno run file.ts",
    "vite",
    "tsc --noEmit",
    "print(1)",
    "def ten():",
    "import os",
    "from pathlib import Path",
    "sqlite3.connect('file.db')",
)


def tool_names() -> frozenset[str]:
    names: set[str] = set()
    for tools in _TOOLS.values():
        names.update(tools)
    return frozenset(names)


_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")


def _placeholder_choices(inner: str) -> list[str]:
    text = inner.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        return parts[:8] or [text]
    return [text] if text else [""]


def expand_placeholders(command: str, limit: int = 12) -> list[str]:
    if "{{" not in command:
        return [command]
    variants = [command]
    round_no = 0
    while round_no < 8 and any("{{" in item for item in variants):
        round_no += 1
        nxt: list[str] = []
        seen: set[str] = set()
        for item in variants:
            match = _PLACEHOLDER.search(item)
            if not match:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    nxt.append(item)
                continue
            for choice in _placeholder_choices(match.group(1)):
                built = (item[: match.start()] + choice + item[match.end() :]).strip()
                built = " ".join(built.split())
                key = built.lower()
                if not built or key in seen:
                    continue
                seen.add(key)
                nxt.append(built)
                if len(nxt) >= limit:
                    break
            if len(nxt) >= limit:
                break
        variants = nxt or variants
    clean = [item for item in variants if "{{" not in item]
    if clean:
        return clean
    stripped = _PLACEHOLDER.sub(lambda match: _placeholder_choices(match.group(1))[0], command)
    return [" ".join(stripped.split())]


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

    def keep(command: str) -> None:
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
        for item in expand_placeholders(command):
            keep(item)

    for match in re.findall(r"`([^`\n]{2,180})`", text):
        add(match)
    for block in re.findall(r"```(?:[a-zA-Z0-9+-]*)\n(.*?)```", text, re.S):
        for line in block.splitlines():
            add(line)
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
