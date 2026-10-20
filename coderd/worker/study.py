from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

from app import db
from worker.commands import harvest_saved, seed_commands, store_commands
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
    ("git-danh-sach", "https://raw.githubusercontent.com/git/git/master/command-list.txt"),
    ("python-os", f"{_PY}/library/os.rst"),
    ("python-shutil", f"{_PY}/library/shutil.rst"),
    ("python-tempfile", f"{_PY}/library/tempfile.rst"),
    ("python-re", f"{_PY}/library/re.rst"),
    ("python-datetime", f"{_PY}/library/datetime.rst"),
    ("python-logging", f"{_PY}/library/logging.rst"),
    ("python-typing", f"{_PY}/library/typing.rst"),
    ("python-http", f"{_PY}/library/http.client.rst"),
    ("python-sys", f"{_PY}/library/sys.rst"),
    ("python-io", f"{_PY}/library/io.rst"),
    ("python-collections", f"{_PY}/library/collections.rst"),
    ("python-itertools", f"{_PY}/library/itertools.rst"),
    ("python-functools", f"{_PY}/library/functools.rst"),
    ("python-asyncio", f"{_PY}/library/asyncio.rst"),
    ("python-threading", f"{_PY}/library/threading.rst"),
    ("python-csv", f"{_PY}/library/csv.rst"),
    ("python-hashlib", f"{_PY}/library/hashlib.rst"),
    ("python-glob", f"{_PY}/library/glob.rst"),
    ("python-pickle", f"{_PY}/library/pickle.rst"),
    ("python-socket", f"{_PY}/library/socket.rst"),
    ("python-http-server", f"{_PY}/library/http.server.rst"),
    ("python-traceback", f"{_PY}/library/traceback.rst"),
    ("python-dataclasses", f"{_PY}/library/dataclasses.rst"),
    ("python-enum", f"{_PY}/library/enum.rst"),
    ("python-copy", f"{_PY}/library/copy.rst"),
    ("python-math", f"{_PY}/library/math.rst"),
    ("python-time", f"{_PY}/library/time.rst"),
    ("python-secrets", f"{_PY}/library/secrets.rst"),
    ("python-gzip", f"{_PY}/library/gzip.rst"),
    ("python-concurrent", f"{_PY}/library/concurrent.futures.rst"),
    ("python-multiprocessing", f"{_PY}/library/multiprocessing.rst"),
    ("python-queue", f"{_PY}/library/queue.rst"),
    ("python-signal", f"{_PY}/library/signal.rst"),
    ("python-inspect", f"{_PY}/library/inspect.rst"),
    ("python-base64", f"{_PY}/library/base64.rst"),
    ("python-uuid", f"{_PY}/library/uuid.rst"),
    ("python-random", f"{_PY}/library/random.rst"),
    ("python-configparser", f"{_PY}/library/configparser.rst"),
    ("python-urllib-parse", f"{_PY}/library/urllib.parse.rst"),
    ("git-reset", f"{_GIT}/git-reset.adoc"),
    ("git-stash", f"{_GIT}/git-stash.adoc"),
    ("git-tag", f"{_GIT}/git-tag.adoc"),
    ("git-rebase", f"{_GIT}/git-rebase.adoc"),
    ("git-config", f"{_GIT}/git-config.adoc"),
    ("git-rm", f"{_GIT}/git-rm.adoc"),
    ("git-mv", f"{_GIT}/git-mv.adoc"),
    ("git-blame", f"{_GIT}/git-blame.adoc"),
    ("git-grep", f"{_GIT}/git-grep.adoc"),
    ("git-switch", f"{_GIT}/git-switch.adoc"),
    ("git-clean", f"{_GIT}/git-clean.adoc"),
    ("git-bisect", f"{_GIT}/git-bisect.adoc"),
    ("git-reflog", f"{_GIT}/git-reflog.adoc"),
    ("git-cherry-pick", f"{_GIT}/git-cherry-pick.adoc"),
    ("git-revert", f"{_GIT}/git-revert.adoc"),
    ("git-worktree", f"{_GIT}/git-worktree.adoc"),
    ("git-submodule", f"{_GIT}/git-submodule.adoc"),
    ("tldr-git", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/git.md"),
    ("tldr-python", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/python.md"),
    ("tldr-python3", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/python3.md"),
    ("tldr-pip", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/pip.md"),
    ("tldr-curl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/curl.md"),
    ("tldr-wget", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/wget.md"),
    ("tldr-sqlite3", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/sqlite3.md"),
    ("tldr-systemctl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/linux/systemctl.md"),
    ("tldr-journalctl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/linux/journalctl.md"),
    ("tldr-ls", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ls.md"),
    ("tldr-mkdir", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/mkdir.md"),
    ("tldr-rm", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/rm.md"),
    ("tldr-grep", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/grep.md"),
    ("tldr-find", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/find.md"),
    ("tldr-chmod", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/chmod.md"),
    ("tldr-ssh", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ssh.md"),
    ("tldr-tar", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/tar.md"),
    ("tldr-rsync", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/rsync.md"),
    ("tldr-docker", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/docker.md"),
    ("tldr-sed", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/sed.md"),
    ("tldr-awk", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/awk.md"),
    ("tldr-make", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/make.md"),
    ("python-cmdline", f"{_PY}/using/cmdline.rst"),
    ("python-howto-urllib", f"{_PY}/howto/urllib2.rst"),
    ("python-howto-socket", f"{_PY}/howto/sockets.rst"),
    ("python-howto-logging", f"{_PY}/howto/logging.rst"),
    ("python-howto-regex", f"{_PY}/howto/regex.rst"),
    ("python-howto-argparse", f"{_PY}/howto/argparse.rst"),
    ("python-pdb", f"{_PY}/library/pdb.rst"),
    ("python-venv-lib", f"{_PY}/library/venv.rst"),
    ("python-ensurepip", f"{_PY}/library/ensurepip.rst"),
    ("curl-manual", "https://raw.githubusercontent.com/curl/curl/master/docs/MANUAL.md"),
    ("tldr-node", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/node.md"),
    ("tldr-npm", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/npm.md"),
    ("tldr-npx", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/npx.md"),
    ("tldr-yarn", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/yarn.md"),
    ("tldr-pnpm", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/pnpm.md"),
    ("tldr-httpie", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/http.md"),
    ("tldr-nc", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/nc.md"),
    ("tldr-docker-compose", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/docker-compose.md"),
    ("tldr-pytest", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/pytest.md"),
    ("tldr-pip3", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/pip3.md"),
    ("tldr-go", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/go.md"),
    ("tldr-gofmt", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gofmt.md"),
    ("tldr-cargo", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/cargo.md"),
    ("tldr-rustc", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/rustc.md"),
    ("tldr-gcc", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gcc.md"),
    ("tldr-g++", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/g++.md"),
    ("tldr-clang", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/clang.md"),
    ("tldr-cmake", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/cmake.md"),
    ("tldr-gdb", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gdb.md"),
    ("tldr-java", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/java.md"),
    ("tldr-javac", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/javac.md"),
    ("tldr-mvn", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/mvn.md"),
    ("tldr-gradle", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gradle.md"),
    ("tldr-poetry", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/poetry.md"),
    ("tldr-jq", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/jq.md"),
    ("tldr-psql", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/psql.md"),
    ("tldr-redis-cli", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/redis-cli.md"),
    ("tldr-gh", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gh.md"),
    ("python-ast", f"{_PY}/library/ast.rst"),
    ("python-pdb-lib", f"{_PY}/library/pdb.rst"),
    ("python-contextlib", f"{_PY}/library/contextlib.rst"),
    ("python-ssl", f"{_PY}/library/ssl.rst"),
    ("python-xml", f"{_PY}/library/xml.etree.elementtree.rst"),
    ("python-http-cookie", f"{_PY}/library/http.cookiejar.rst"),
    ("python-ipaddress", f"{_PY}/library/ipaddress.rst"),
    ("python-email", f"{_PY}/library/email.rst"),
    ("python-ctypes", f"{_PY}/library/ctypes.rst"),
    ("python-mmap", f"{_PY}/library/mmap.rst"),
    ("tldr-php", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/php.md"),
    ("tldr-composer", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/composer.md"),
    ("tldr-phpunit", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/phpunit.md"),
    ("tldr-ruby", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ruby.md"),
    ("tldr-gem", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gem.md"),
    ("tldr-bundle", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/bundle.md"),
    ("tldr-rails", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/rails.md"),
    ("tldr-rake", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/rake.md"),
    ("tldr-perl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/perl.md"),
    ("tldr-lua", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/lua.md"),
    ("tldr-dart", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/dart.md"),
    ("tldr-flutter", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/flutter.md"),
    ("tldr-dotnet", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/dotnet.md"),
    ("tldr-zig", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/zig.md"),
    ("tldr-elixir", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/elixir.md"),
    ("tldr-mix", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/mix.md"),
    ("tldr-julia", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/julia.md"),
    ("tldr-scala", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/scala.md"),
    ("tldr-sbt", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/sbt.md"),
    ("tldr-swift", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/swift.md"),
    ("tldr-kubectl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/kubectl.md"),
    ("tldr-helm", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/helm.md"),
    ("tldr-minikube", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/minikube.md"),
    ("tldr-terraform", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/terraform.md"),
    ("tldr-ansible", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ansible.md"),
    ("tldr-ansible-playbook", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ansible-playbook.md"),
    ("tldr-ansible-galaxy", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ansible-galaxy.md"),
    ("tldr-vagrant", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/vagrant.md"),
    ("tldr-packer", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/packer.md"),
    ("tldr-pulumi", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/pulumi.md"),
    ("tldr-nginx", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/nginx.md"),
    ("tldr-caddy", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/caddy.md"),
    ("tldr-traefik", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/traefik.md"),
    ("tldr-openssl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/openssl.md"),
    ("tldr-gpg", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gpg.md"),
    ("tldr-ssh-keygen", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/ssh-keygen.md"),
    ("tldr-certbot", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/linux/certbot.md"),
    ("tldr-aws", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/aws.md"),
    ("tldr-gcloud", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/gcloud.md"),
    ("tldr-az", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/az.md"),
    ("tldr-wrangler", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/wrangler.md"),
    ("tldr-heroku", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/heroku.md"),
    ("tldr-vercel", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/vercel.md"),
    ("tldr-doctl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/doctl.md"),
    ("tldr-deno", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/deno.md"),
    ("tldr-vite", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/vite.md"),
    ("tldr-tsc", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/tsc.md"),
    ("tldr-eslint", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/eslint.md"),
    ("tldr-mysql", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/mysql.md"),
    ("tldr-mongosh", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/mongosh.md"),
    ("tldr-conda", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/conda.md"),
    ("tldr-bazel", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/bazel.md"),
    ("tldr-valgrind", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/valgrind.md"),
    ("tldr-kind", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/kind.md"),
    ("tldr-kustomize", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/common/kustomize.md"),
    ("tldr-apache2ctl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/linux/apache2ctl.md"),
    ("tldr-apachectl", "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/linux/apachectl.md"),
)
SKIP_FILES = {
    "da-hoc.md",
    "kinh-nghiem.md",
    "muc-luc.md",
    "nhat-ky.md",
    "chi-muc.md",
    "on-tap.md",
    "suy-ngam.md",
    "dong-chay.md",
    "dung.md",
}
CACHE_TTL = 24 * 60 * 60
OPEN_NOTE_LIMIT = 8
CHUNK = 8
_LISTINGS = (
    ("https://api.github.com/repos/python/cpython/contents/Doc/tutorial?ref=3.12", "py-tut"),
    ("https://api.github.com/repos/python/cpython/contents/Doc/library?ref=3.12", "py-lib"),
    ("https://api.github.com/repos/python/cpython/contents/Doc/howto?ref=3.12", "py-howto"),
    ("https://api.github.com/repos/python/cpython/contents/Doc/faq?ref=3.12", "py-faq"),
    ("https://api.github.com/repos/git/git/contents/Documentation?ref=master", "git-doc"),
    ("https://api.github.com/repos/tldr-pages/tldr/contents/pages/common?ref=main", "tldr"),
    ("https://api.github.com/repos/tldr-pages/tldr/contents/pages/linux?ref=main", "tldr-linux"),
    ("https://api.github.com/repos/python/cpython/contents/Doc/using?ref=3.12", "py-using"),
)
_DOC_SUFFIX = (".rst", ".adoc", ".md")


def _clean_markup(line: str) -> str:
    line = re.sub(r":pep:`(\d+)`", r"PEP \1", line)
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
    cap = 2_000_000 if "api.github.com" in url else 100_000
    request = urllib.request.Request(url, headers={"User-Agent": "coderd-study"})
    with urllib.request.urlopen(request, timeout=12) as response:
        data = response.read(cap)
    return data.decode("utf-8", "replace")


def load_source(root: Path, name: str, url: str, fetch) -> str:
    if url.startswith("file:"):
        path = Path(unquote(urlparse(url).path))
        return path.read_text(encoding="utf-8")
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


def _topic_path(root: Path, name: str) -> Path:
    return root / "learn" / "sach" / f"{name}.md"


def _topic_url(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("nguồn: "):
            return line.split(": ", 1)[1].strip()
    return ""


def write_topic(root: Path, name: str, url: str, notes: list[str]) -> None:
    path = _topic_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {name}", ""]
    if url:
        lines += [f"nguồn: {url}", ""]
    lines += [f"- {note}" for note in notes]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def keep_deeper(root: Path, name: str, notes: list[str]) -> list[str]:
    path = _topic_path(root, name)
    if not path.is_file():
        return notes
    old = _bullets(path.read_text(encoding="utf-8"))
    if len(old) > len(notes):
        return old
    return notes


def render_store(root: Path) -> int:
    learn = root / "learn"
    learn.mkdir(parents=True, exist_ok=True)
    sections: list[tuple[str, str, list[str]]] = []
    for path in sorted(learn.glob("*.md")):
        if path.name in SKIP_FILES:
            continue
        notes = extract_notes(path.read_text(encoding="utf-8"), limit=30)
        if notes:
            sections.append((path.name, "", notes))
    sach = learn / "sach"
    if sach.is_dir():
        for path in sorted(sach.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            notes = _bullets(text)
            if notes:
                sections.append((path.stem, _topic_url(text), notes))
    lines = ["# Kho đã học", ""]
    total = 0
    highlights: list[str] = []
    for name, url, notes in sections:
        lines.append(f"## {name}")
        if url:
            lines.append(f"nguồn: {url}")
        for note in notes:
            lines.append(f"- {note}")
            total += 1
        lines.append("")
        if notes:
            highlights.append(notes[0])
    (learn / "da-hoc.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path = lesson_file(root)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(remember(existing, highlights), encoding="utf-8")
    return total


def ingest_sources(root: Path, fetch=fetch_text) -> int:
    learn = root / "learn"
    learn.mkdir(parents=True, exist_ok=True)
    (learn / "sach").mkdir(parents=True, exist_ok=True)
    for name, url in OPEN_SOURCES:
        try:
            text = load_source(root, name, url, fetch)
            notes = keep_deeper(root, name, extract_notes(text, limit=OPEN_NOTE_LIMIT))
        except Exception as exc:
            print(f"không tải {name}: {exc}")
            continue
        if notes:
            url_now = url
            existing = _topic_path(root, name)
            if existing.is_file() and len(_bullets(existing.read_text(encoding="utf-8"))) == len(notes):
                url_now = _topic_url(existing.read_text(encoding="utf-8")) or url
            write_topic(root, name, url_now, notes)
    return render_store(root)


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


def _safe_name(prefix: str, filename: str) -> str:
    stem = Path(filename).stem.lower().replace(".", "-")
    cleaned = re.sub(r"[^a-z0-9-]+", "-", f"{prefix}-{stem}").strip("-")
    return cleaned[:80] or "nguon"


def discover_catalog(root: Path, fetch) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: str, url: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        found.append((name, url))

    for name, url in OPEN_SOURCES:
        add(name, url)
    cache = root / "learn" / "cache" / "catalog.json"
    cached: list[tuple[str, str]] = []
    if cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                cached = [(str(name), str(url)) for name, url in data]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            cached = []
    if cached:
        for name, url in cached:
            add(name, url)
        if any("tldr-pages/tldr" in url for _name, url in cached):
            return found
    for api, prefix in _LISTINGS:
        try:
            payload = json.loads(fetch(api))
        except Exception as exc:
            print(f"không liệt kê {prefix}: {exc}")
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("name") or "")
            if not filename.endswith(_DOC_SUFFIX):
                continue
            add(_safe_name(prefix, filename), str(item.get("download_url") or ""))
    if found:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return found


_DEEP = frozenset(
    {
        "python",
        "git",
        "go",
        "gofmt",
        "cargo",
        "rust",
        "rustc",
        "rustup",
        "gcc",
        "g++",
        "clang",
        "cmake",
        "make",
        "gdb",
        "lldb",
        "java",
        "javac",
        "mvn",
        "maven",
        "gradle",
        "docker",
        "podman",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "node",
        "bun",
        "deno",
        "vite",
        "webpack",
        "eslint",
        "prettier",
        "tsc",
        "pip",
        "poetry",
        "sqlite",
        "curl",
        "wget",
        "ssh",
        "jq",
        "yq",
        "psql",
        "postgres",
        "mysql",
        "redis",
        "mongo",
        "mongosh",
        "duckdb",
        "http",
        "pytest",
        "ruff",
        "mypy",
        "black",
        "uv",
        "conda",
        "jupyter",
        "php",
        "composer",
        "phpunit",
        "ruby",
        "gem",
        "bundle",
        "rails",
        "rake",
        "perl",
        "lua",
        "luajit",
        "dart",
        "flutter",
        "dotnet",
        "zig",
        "nim",
        "elixir",
        "mix",
        "julia",
        "scala",
        "sbt",
        "swift",
        "haskell",
        "cabal",
        "clojure",
        "ninja",
        "meson",
        "bazel",
        "valgrind",
        "gh",
        "kubectl",
        "kubeadm",
        "helm",
        "kustomize",
        "minikube",
        "kind",
        "skaffold",
        "istioctl",
        "terraform",
        "terragrunt",
        "ansible",
        "vagrant",
        "packer",
        "pulumi",
        "nginx",
        "caddy",
        "apache2",
        "apache2ctl",
        "apachectl",
        "httpd",
        "traefik",
        "haproxy",
        "openssl",
        "gpg",
        "certbot",
        "keytool",
        "aws",
        "gcloud",
        "az",
        "wrangler",
        "heroku",
        "vercel",
        "netlify",
        "doctl",
        "flyctl",
        "subprocess",
        "pathlib",
        "argparse",
        "unittest",
        "asyncio",
        "typing",
        "ctypes",
        "ipaddress",
    }
)
_DEEP_PREFIX = ("py-", "python", "git-doc", "git-")


def _deep_name(name: str) -> bool:
    low = name.lower()
    if low.startswith(_DEEP_PREFIX):
        return True
    tokens = {part for part in re.split(r"[^a-z0-9+]+", low) if part}
    return bool(tokens & _DEEP)


def prefetch_catalog(root: Path, fetch=fetch_text, limit: int = 2500, deep_only: bool = True) -> int:
    catalog = discover_catalog(root, fetch)
    catalog = sorted(catalog, key=lambda item: (0 if _deep_name(item[0]) else 1, item[0]))
    loaded = 0
    for name, url in catalog:
        if url.startswith("file:"):
            continue
        if deep_only and not _deep_name(name):
            continue
        cache = root / "learn" / "cache" / f"{name}.txt"
        if cache.is_file() and cache.stat().st_size > 40:
            continue
        try:
            load_source(root, name, url, fetch)
            loaded += 1
            print(f"tải {name}", flush=True)
        except Exception as exc:
            print(f"không tải {name}: {exc}", flush=True)
        if loaded >= limit:
            break
    return loaded


def load_progress(root: Path) -> dict:
    path = root / "learn" / "tien-do.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("offsets", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"cursor": 0, "offsets": {}}


def save_progress(root: Path, state: dict) -> None:
    (root / "learn").mkdir(parents=True, exist_ok=True)
    (root / "learn" / "tien-do.json").write_text(json.dumps(state), encoding="utf-8")


def absorb_next(root: Path, catalog: list[tuple[str, str]], state: dict, fetch) -> tuple[str, list[str]]:
    if not catalog:
        return "", []
    offsets = state.setdefault("offsets", {})
    count = len(catalog)
    cursor = int(state.get("cursor") or 0) % count
    for step in range(count):
        index = (cursor + step) % count
        name, url = catalog[index]
        if int(offsets.get(name) or 0) < 0:
            continue
        try:
            text = load_source(root, name, url, fetch)
        except Exception as exc:
            print(f"không tải {name}: {exc}")
            offsets[name] = -2
            continue
        offset = int(offsets.get(name) or 0)
        fresh = extract_notes(text, limit=offset + CHUNK)[offset:offset + CHUNK]
        if not fresh:
            offsets[name] = -1
            continue
        path = _topic_path(root, name)
        current = _bullets(path.read_text(encoding="utf-8")) if path.is_file() else []
        seen = {item.lower() for item in current}
        for item in fresh:
            if item.lower() not in seen:
                seen.add(item.lower())
                current.append(item)
        write_topic(root, name, url, current)
        offsets[name] = offset + len(fresh)
        state["cursor"] = (index + 1) % count
        return name, fresh
    state["cursor"] = 0
    return "", []


def _pass_done(state: dict, catalog: list[tuple[str, str]]) -> bool:
    if not catalog:
        return False
    offsets = state.get("offsets") or {}
    return all(int(offsets.get(name) or 0) < 0 for name, _url in catalog)


def _rewind(state: dict) -> None:
    offsets = state.setdefault("offsets", {})
    for name, value in list(offsets.items()):
        if int(value) == -1:
            offsets[name] = 0
    state["cursor"] = 0
    state["passes"] = int(state.get("passes") or 1) + 1


def next_saved(root: Path, state: dict) -> tuple[str, list[str]]:
    sach = root / "learn" / "sach"
    if not sach.is_dir():
        return "", []
    files = sorted(sach.glob("*.md"))
    if not files:
        return "", []
    index = int(state.get("recap") or 0) % len(files)
    state["recap"] = index + 1
    notes = _bullets(files[index].read_text(encoding="utf-8"))
    return files[index].stem, notes[-CHUNK:]


def distill_notes(ask, name: str, notes: list[str]) -> list[str]:
    if not notes:
        return []
    prompt = (
        "Viết tối đa 3 bài học tiếng Việt, mỗi bài một dòng bắt đầu bằng '- '. "
        "Chỉ dùng ý có trong ghi chú. Giữ lại tên lệnh hoặc hàm. "
        "Nếu không có gì mới, trả lời '- không có'.\n"
        f"Chủ đề: {name}\n" + "\n".join(f"- {note}" for note in notes[:CHUNK])
    )
    return [item for item in parse_lessons(str(ask(prompt) or "")) if grounded(item, notes)]


_GENERIC = {
    "python",
    "library",
    "module",
    "source",
    "description",
    "example",
    "import",
    "using",
    "used",
    "code",
    "class",
    "function",
    "package",
    "section",
    "tools",
    "provides",
    "useful",
    "different",
    "between",
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "your",
    "into",
    "than",
    "then",
    "them",
}


def _terms(text: str) -> set[str]:
    return {
        word.lower()
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text)
        if word.lower() not in _GENERIC
    }


def _claims(text: str) -> set[str]:
    found: set[str] = set()
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text):
        low = word.lower()
        if low in _GENERIC:
            continue
        technical = "_" in word or any(ch.isdigit() for ch in word) or any(ch.isupper() for ch in word[1:])
        if technical or len(low) >= 6:
            found.add(low)
    return found


def grounded(lesson: str, notes: list[str]) -> bool:
    if not keep_lesson(lesson):
        return False
    claimed = _claims(lesson)
    if claimed:
        return claimed <= _terms(" ".join(notes))
    return re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", lesson.lower()) is not None


def extract_examples(text: str, limit: int = 3) -> list[str]:
    found: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not 1 <= len(buf) <= 6:
            buf.clear()
            return
        snippet = " ".join(buf)
        if snippet.startswith(":") or "doctest" in snippet or ":class:" in snippet or ":synopsis:" in snippet:
            buf.clear()
            return
        if 8 <= len(snippet) <= 180 and any(mark in snippet for mark in "=("):
            found.append(snippet[:180])
        buf.clear()

    for raw in text.splitlines():
        if raw.startswith("    ") or raw.startswith("\t"):
            buf.append(raw.strip())
            continue
        flush()
        if len(found) >= limit:
            break
    flush()
    return found[:limit]


def _append_capped(path: Path, line: str, limit: int = 40) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    old.append(line)
    path.write_text("\n".join(old[-limit:]) + "\n", encoding="utf-8")


def write_skill(root: Path, name: str, url: str, notes: list[str], lessons: list[str], examples: list[str]) -> None:
    if not name or not notes:
        return
    path = root / "learn" / "the" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = notes[-12:]
    steps = lessons[:3] or facts[:1]
    lines = [f"# {name}", ""]
    if url:
        lines += [f"nguồn: {url}", ""]
    lines.append("## Sự kiện")
    lines += [f"- {note}" for note in facts]
    lines += ["", "## Cách làm"]
    lines += [f"- {step}" for step in steps]
    if examples:
        lines += ["", "## Ví dụ"]
        lines += [f"- {item}" for item in examples[:3]]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def rebuild_index(root: Path) -> dict[str, set[str]]:
    folder = root / "learn" / "the"
    index: dict[str, set[str]] = {}
    clusters: dict[str, list[str]] = {}
    lines = ["# Chỉ mục kỹ năng", "", "Đọc learn/dung.md rồi đúng một file trong learn/the/.", ""]
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            terms = sorted(_terms(path.read_text(encoding="utf-8")))[:8]
            index[path.stem] = set(terms)
            key = path.stem.split("-")[0]
            clusters.setdefault(key, []).append(path.stem)
    for key, names in clusters.items():
        lines.append(f"- {key}: {', '.join(names[:12])}")
    (root / "learn").mkdir(parents=True, exist_ok=True)
    (root / "learn" / "chi-muc.md").write_text("\n".join(lines[:250]).rstrip() + "\n", encoding="utf-8")
    try:
        from worker.use import link_learned

        link_learned(root)
    except Exception as exc:
        print(f"chưa liên kết: {exc}")
    return index


def reflect(root: Path, name: str, notes: list[str], index: dict[str, set[str]]) -> str:
    mine = _terms(" ".join(notes))
    best = ""
    words: list[str] = []
    score = 1
    for other, terms in index.items():
        if other == name:
            continue
        overlap = sorted(mine & terms)
        if len(overlap) > score:
            score = len(overlap)
            best = other
            words = overlap[:3]
    if not best:
        return ""
    line = f"- {name} gắn với {best} qua {', '.join(words)}"
    _append_capped(root / "learn" / "suy-ngam.md", line)
    return line


def review_once(root: Path, state: dict, ask) -> str:
    folder = root / "learn" / "the"
    if not folder.is_dir():
        return ""
    files = sorted(folder.glob("*.md"))
    if not files:
        return ""
    counts = state.setdefault("reviewed", {})
    files.sort(key=lambda path: (int(counts.get(path.stem) or 0), path.stem))
    path = files[0]
    notes = _bullets(path.read_text(encoding="utf-8"))
    counts[path.stem] = int(counts.get(path.stem) or 0) + 1
    if not notes:
        return ""
    answer = notes[0]
    try:
        lessons = distill_notes(ask, path.stem, notes[:1])
    except Exception as exc:
        print(f"chưa ôn được {path.stem}: {exc}")
        lessons = []
    if lessons:
        answer = lessons[0]
    _append_capped(root / "learn" / "on-tap.md", f"- {path.stem}: {answer}")
    return answer


def local_sources(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    memory = root / ".coderd" / "memory.md"
    if memory.is_file():
        found.append(("ghi-nho", memory.as_uri()))
    learn = root / "learn"
    if learn.is_dir():
        for path in sorted(learn.glob("*.md")):
            if path.name in SKIP_FILES:
                continue
            found.append((f"noi-bo-{path.stem}"[:80], path.as_uri()))
    return found


def extend_catalog(root: Path, catalog: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = {name for name, _url in catalog}
    extra = [item for item in local_sources(root) if item[0] not in seen]
    return list(catalog) + extra


def _source_url(catalog: list[tuple[str, str]], root: Path, name: str) -> str:
    for item, url in catalog:
        if item == name:
            return url
    path = _topic_path(root, name)
    if path.is_file():
        return _topic_url(path.read_text(encoding="utf-8"))
    return ""


def study_hours(root: Path, database: Path, ask, hours: float, fetch=fetch_text, clock=time.time, catalog=None) -> int:
    (root / "learn").mkdir(parents=True, exist_ok=True)
    state = load_progress(root)
    begun = float(clock())
    if "active_seconds" not in state:
        started = float(state.get("started") or begun)
        state["active_seconds"] = max(0.0, begun - started)
    need = float(state.get("need_seconds") or 0)
    if need <= 0:
        need = float(hours) * 3600
    state["need_seconds"] = need
    stored = float(state.get("deadline") or 0)
    active = float(state.get("active_seconds") or 0)
    if stored > begun:
        deadline = stored
    elif state.get("started") and active < need:
        deadline = begun + (need - active)
    else:
        deadline = begun + need
        state["active_seconds"] = 0.0
        state["started"] = begun
    state["deadline"] = deadline
    state["started"] = state.get("started") or begun
    if catalog is None:
        catalog = discover_catalog(root, fetch)
    catalog = extend_catalog(root, catalog)
    try:
        seed_commands(root)
        harvest_saved(root)
    except Exception as exc:
        print(f"gom lệnh: {exc}")
    rounds = 0
    added = 0
    last = begun
    while True:
        now = float(clock())
        state["active_seconds"] = float(state.get("active_seconds") or 0) + max(0.0, now - last)
        last = now
        if now >= deadline and float(state["active_seconds"]) >= need:
            save_progress(root, state)
            break
        rounds += 1
        fresh: list[str] = []
        name = ""
        try:
            name, fresh = absorb_next(root, catalog, state, fetch)
        except Exception as exc:
            print(f"nạp: {exc}")
        if not fresh and _pass_done(state, catalog):
            _rewind(state)
            try:
                name, fresh = absorb_next(root, catalog, state, fetch)
            except Exception as exc:
                print(f"nạp lại: {exc}")
        if fresh:
            added += len(fresh)
            try:
                render_store(root)
            except Exception as exc:
                print(f"ghi kho: {exc}")
        elif catalog:
            name, fresh = next_saved(root, state)
        lessons: list[str] = []
        if fresh:
            try:
                lessons = distill_notes(ask, name, fresh)
            except Exception as exc:
                print(f"chưa rút được {name}: {exc}")
        remember_lines = lessons or (fresh[:1] if fresh else [])
        if remember_lines:
            path = lesson_file(root)
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(merge_lessons(existing, remember_lines), encoding="utf-8")
        examples: list[str] = []
        cache = root / "learn" / "cache" / f"{name}.txt"
        if name and cache.is_file():
            try:
                cached = cache.read_text(encoding="utf-8")
                examples = extract_examples(cached)
                store_commands(root, cached, name)
            except OSError:
                examples = []
        elif name and fresh:
            store_commands(root, "\n".join(fresh), name)
        if name and fresh:
            write_skill(root, name, _source_url(catalog, root, name), fresh, lessons, examples)
        index = rebuild_index(root)
        if name and fresh:
            reflect(root, name, fresh, index)
        try:
            review_once(root, state, ask)
        except Exception as exc:
            print(f"ôn: {exc}")
        try:
            study_once(database, root, ask)
        except Exception as exc:
            print(f"chat: {exc}")
        save_progress(root, state)
        spent = float(state.get("active_seconds") or 0) / 3600
        line = f"vòng {rounds} {name or 'trống'} +{len(fresh)} bài {len(lessons)} đã học {spent:.2f}/{need / 3600:.2f} giờ"
        (root / "learn" / "nhat-ky.md").write_text(
            "# Nhật ký học\n\n" + f"- hạn {int(deadline)}\n- {line}\n- bài mới {added}\n",
            encoding="utf-8",
        )
        _append_capped(root / "learn" / "dong-chay.md", f"- {line}", limit=200)
        print(line, flush=True)
    print(f"hết giờ sau {rounds} vòng, bài mới {added}", flush=True)
    return rounds


def begin_hours(root: Path, hours: float, clock=time.time) -> dict:
    (root / "learn").mkdir(parents=True, exist_ok=True)
    state = load_progress(root)
    now = float(clock())
    need = float(hours) * 3600
    offsets = state.setdefault("offsets", {})
    for name in list(offsets):
        offsets[name] = 0
    state["cursor"] = 0
    state["active_seconds"] = 0.0
    state["need_seconds"] = need
    state["started"] = now
    state["deadline"] = now + need
    save_progress(root, state)
    return state


def hours_done(root: Path, hours: float, slack: float = 30.0) -> bool:
    state = load_progress(root)
    need = float(state.get("need_seconds") or 0)
    if need <= 0:
        need = float(hours) * 3600
    active = float(state.get("active_seconds") or 0)
    return active + slack >= need


def acquire_lock(root: Path):
    learn = root / "learn"
    learn.mkdir(parents=True, exist_ok=True)
    handle = (learn / "study.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def main() -> None:
    load_env()
    load_env_file(Path("/opt/coderd/coderd.env"))
    database = Path(os.environ.get("CODERD_DB") or "/opt/coderd/var/coderd.db")
    root = Path(os.environ.get("WORKSPACE_ROOT") or "/opt/coderd/var/workspace")
    hours = 0.0
    if len(sys.argv) >= 3 and sys.argv[1] == "--hours":
        hours = float(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == "--harvest":
        from worker.use import link_learned

        added = harvest_saved(root)
        link_learned(root)
        print(f"gom {added}")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--fill":
        from worker.use import link_learned

        loaded = prefetch_catalog(root)
        added = harvest_saved(root)
        link_learned(root)
        print(f"tải {loaded} gom {added}")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--link":
        from worker.use import link_learned

        path = link_learned(root)
        print(path)
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--begin-hours":
        begin_hours(root, float(sys.argv[2]))
        print("bắt đầu phiên")
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--check-hours":
        hours = float(sys.argv[2])
        if hours_done(root, hours):
            print("đủ giờ")
            return
        print("chưa đủ giờ")
        sys.exit(1)
    lock = acquire_lock(root)
    if lock is None:
        print("đang có vòng học khác")
        if hours > 0:
            sys.exit(75)
        return
    try:
        if hours > 0:
            try:
                ingest_sources(root)
            except Exception as exc:
                print(f"nạp nguồn: {exc}")
            study_hours(root, database, ask_model, hours)
            if not hours_done(root, hours):
                sys.exit(1)
            return
        try:
            ingested = ingest_sources(root)
            count = study_once(database, root, ask_model)
        except Exception as extra:
            print(f"vòng học dừng: {extra}")
            return
        print(f"nguồn {ingested}")
        print(f"học {count}")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
