"""Write a short import order so each VCF can be opened in sequence."""

from __future__ import annotations

from pathlib import Path

from exporters.vcf_validator import count_markers


def write_import_guide(output_dir: Path) -> Path | None:
    """List VCF files from first to last, with the first and last contact name."""
    files = _vcf_files(output_dir)
    if not files:
        return None
    lines = [
        "Thứ tự nhập vào Danh bạ",
        "Mở từng file từ trên xuống. Trên iPhone: ứng dụng Tệp, chọn file, Chia sẻ, Thêm vào Danh bạ.",
        "Mỗi file là một nhóm liên hệ. Nhập hết file này rồi mới sang file kế tiếp.",
        "",
    ]
    for index, path in enumerate(files, start=1):
        count = count_markers(path)[0]
        first, last = _name_span(path)
        relative = path.relative_to(output_dir).as_posix()
        lines.append(f"{index}. {relative} — {count} liên hệ — {first} → {last}")
    target = output_dir / "thu_tu_nhap.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _vcf_files(output_dir: Path) -> list[Path]:
    found = list(output_dir.glob("contacts_*.vcf"))
    found.extend(output_dir.glob("batch_*/contacts_*.vcf"))
    return sorted(found, key=lambda path: path.name)


def _name_span(path: Path) -> tuple[str, str]:
    first = ""
    last = ""
    logical = ""
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if line.startswith((" ", "\t")) and logical:
                logical += line[1:]
                continue
            if logical.startswith("FN:"):
                name = _unescape(logical[3:])
                if first == "":
                    first = name
                last = name
            logical = line
    if logical.startswith("FN:"):
        name = _unescape(logical[3:])
        if first == "":
            first = name
        last = name
    return first or "—", last or "—"


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )
