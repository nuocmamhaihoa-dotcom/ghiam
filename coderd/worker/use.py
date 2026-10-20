from __future__ import annotations

import re
from pathlib import Path

from worker.commands import GROUPS, _GLOSS, _read_group, group_of


def _stems(root: Path) -> list[str]:
    folder = root / "learn" / "the"
    if not folder.is_dir():
        return []
    return [path.stem for path in sorted(folder.glob("*.md"))]


def _match_skills(command: str, stems: list[str]) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", command.lower())
    if not tokens:
        return []
    slug = "-".join(tokens[:3])
    tool = tokens[0]
    wanted = {slug, f"tldr-{slug}", f"tldr-{tool}", tool}
    if tool in {"python", "python3"}:
        wanted.update({"tldr-python", "tldr-python3", "python-nhap-mon", "python-file"})
    if tool == "pip":
        wanted.update({"tldr-pip", "tldr-pip3"})
    hits: list[str] = []
    seen: set[str] = set()
    for stem in stems:
        if stem in seen:
            continue
        if stem in wanted or stem == slug or stem.startswith(slug + "-"):
            seen.add(stem)
            hits.append(stem)
    for stem in stems:
        if stem in seen:
            continue
        if stem == f"tldr-{tool}":
            seen.add(stem)
            hits.append(stem)
        if len(hits) >= 4:
            break
    return hits[:4]


def _how(root: Path, stem: str) -> str:
    path = root / "learn" / "the" / f"{stem}.md"
    if not path.is_file():
        return ""
    section = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip() == "## Cách làm":
            section = True
            continue
        if section and line.startswith("## "):
            break
        if section and line.startswith("- "):
            item = line[2:].strip()
            if item.startswith(">") or len(item) < 8:
                continue
            return item[:160]
    return ""


def _group_count(root: Path, name: str) -> int:
    return len(_read_group(root / "learn" / "lenh" / f"{name}.md"))


def link_learned(root: Path) -> Path:
    learn = root / "learn"
    learn.mkdir(parents=True, exist_ok=True)
    stems = _stems(root)
    lines = [
        "# Dùng kiến thức đã học",
        "",
        "Người dùng nói tiếng Việt. Đổi thành lệnh, mở đúng một file nhóm, khi cần mở thêm một file kỹ năng.",
        "",
        "## Việc → lệnh → file",
        "",
    ]
    seen: set[str] = set()
    for prefix, meaning in _GLOSS:
        if meaning in seen:
            continue
        seen.add(meaning)
        group = group_of(prefix) or group_of(prefix + " x")
        skills = _match_skills(prefix, stems)
        group_bit = f"learn/lenh/{group}.md" if group else "learn/lenh/"
        skill_bit = ""
        if skills:
            skill_bit = " ; " + ", ".join(f"learn/the/{name}.md" for name in skills[:2])
        lines.append(f"- {meaning}: `{prefix.strip()}` → {group_bit}{skill_bit}")
    lines += ["", "## Nhóm công cụ", ""]
    for name, title in GROUPS:
        count = _group_count(root, name)
        matching = [
            stem
            for stem in stems
            if stem == name
            or stem.startswith(name + "-")
            or stem.startswith("tldr-" + name)
            or (name == "python" and (stem.startswith("py-") or stem.startswith("python")))
            or (name == "mang" and stem in {"tldr-curl", "tldr-wget", "tldr-httpie", "tldr-nc", "curl-manual"})
            or (name == "he-thong" and stem.startswith("tldr-system"))
            or (name == "shell" and stem in {"tldr-ls", "tldr-mkdir", "tldr-rm", "tldr-grep", "tldr-find", "tldr-chmod", "tldr-ssh", "tldr-tar", "tldr-rsync", "tldr-sed", "tldr-awk", "tldr-make"})
            or (name == "node" and stem in {"tldr-node", "tldr-npm", "tldr-npx", "tldr-yarn", "tldr-pnpm"})
            or (name == "go" and stem.startswith("tldr-go"))
            or (name == "rust" and (stem.startswith("tldr-cargo") or stem.startswith("tldr-rust")))
            or (name == "java" and stem in {"tldr-java", "tldr-javac", "tldr-mvn", "tldr-gradle"})
            or (name == "bien-dich" and stem in {"tldr-gcc", "tldr-g++", "tldr-clang", "tldr-cmake", "tldr-make", "tldr-gdb"})
            or (name == "csdl" and stem in {"tldr-psql", "tldr-redis-cli", "tldr-mysql", "tldr-mongosh"})
            or (name == "php" and (stem.startswith("tldr-php") or stem.startswith("tldr-composer")))
            or (name == "ruby" and stem in {"tldr-ruby", "tldr-gem", "tldr-bundle", "tldr-rails", "tldr-rake"})
            or (name == "ngon-ngu" and stem in {"tldr-perl", "tldr-lua", "tldr-dart", "tldr-flutter", "tldr-dotnet", "tldr-zig", "tldr-elixir", "tldr-mix", "tldr-julia", "tldr-scala", "tldr-sbt", "tldr-swift"})
            or (name == "devops" and (stem.startswith("tldr-kube") or stem.startswith("tldr-helm") or stem.startswith("tldr-terraform") or stem.startswith("tldr-ansible") or stem in {"tldr-minikube", "tldr-kind", "tldr-kustomize", "tldr-istioctl", "tldr-vagrant", "tldr-packer", "tldr-pulumi"}))
            or (name == "web" and stem in {"tldr-nginx", "tldr-caddy", "tldr-apache2", "tldr-httpd", "tldr-traefik"})
            or (name == "bao-mat" and stem in {"tldr-openssl", "tldr-gpg", "tldr-ssh-keygen", "tldr-certbot"})
            or (name == "dam-may" and stem in {"tldr-aws", "tldr-gcloud", "tldr-az", "tldr-wrangler", "tldr-heroku", "tldr-vercel", "tldr-doctl"})
        ]
        matching.sort(key=lambda stem: (0 if stem.startswith(name) or stem.startswith("tldr-" + name) else 1, stem))
        shown = ", ".join(matching[:8]) if matching else "chưa có kỹ năng"
        lines.append(f"- {title}: `learn/lenh/{name}.md` ({count} lệnh). Kỹ năng: {shown}")
    lines += [
        "",
        "## Thứ tự mở file",
        "",
        "1. `learn/dung.md` (file này)",
        "2. đúng một file trong `learn/lenh/`",
        "3. đúng một file trong `learn/the/` mục Cách làm",
        "4. `learn/sach/` chỉ khi Cách làm chưa đủ",
        "",
    ]
    path = learn / "dung.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def use_context(root: Path, question: str, limit: int = 6) -> str:
    text = (question or "").lower()
    if len(text.strip()) < 2:
        return ""
    stems = _stems(root)
    scored: list[tuple[int, str, str]] = []
    for prefix, meaning in _GLOSS:
        score = 0
        low_meaning = meaning.lower()
        low_prefix = prefix.strip().lower()
        if low_meaning and low_meaning in text:
            score += 6
        if low_prefix and low_prefix in text:
            score += 5
        for word in re.findall(r"[a-zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]{4,}", low_meaning):
            if word in text:
                score += 1
        if score:
            scored.append((score, prefix, meaning))
    scored.sort(key=lambda item: (-item[0], item[2]))
    lines: list[str] = []
    seen: set[str] = set()
    for _score, prefix, meaning in scored:
        key = meaning
        if key in seen:
            continue
        seen.add(key)
        group = group_of(prefix) or group_of(prefix + " x")
        skills = _match_skills(prefix, stems)
        bit = f"- {meaning}: `{prefix.strip()}`"
        if group:
            bit += f" — learn/lenh/{group}.md"
        if skills:
            bit += f" — learn/the/{skills[0]}.md"
            how = _how(root, skills[0])
            if how:
                bit += f" — {how}"
        lines.append(bit)
        if len(lines) >= limit:
            break
    if lines:
        return "\n".join(lines)
    for name, title in GROUPS:
        label = title.split("—")[0].strip().lower()
        if name in text or label in text:
            return f"- {title}: mở learn/lenh/{name}.md rồi một file learn/the/ cùng nhóm."
    return ""


def vietnamese_lessons(text: str, limit: int = 6) -> str:
    found: list[str] = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", line.lower()) is None:
            continue
        found.append(line.strip())
        if len(found) >= limit:
            break
    return "\n".join(found)
