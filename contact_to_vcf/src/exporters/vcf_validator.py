"""vCard 3.0 text escaping, folding, and structural checks."""

from __future__ import annotations

from pathlib import Path


class VcfStructureError(Exception):
    """A written VCF file does not match the required contact structure."""


def escape_vcard_text(value: str) -> str:
    """Escape a single vCard value so it cannot break out of its line."""
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\n", "\\n")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    return value


def fold_line(line: str) -> str:
    """Fold a logical line at 75 octets, without splitting a UTF-8 character."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    limit = 75
    while encoded:
        take = min(limit, len(encoded))
        chunk = encoded[:take]
        while chunk:
            try:
                text = chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        else:
            raise VcfStructureError("Không thể gấp dòng vCard")
        parts.append(text)
        encoded = encoded[len(chunk) :]
        limit = 74
    return "\r\n ".join(parts)


def format_card(name: str, phone: str) -> str:
    """Write one contact in the same vCard 3.0 shape as a working phone import.

    The working sample is five CRLF lines, no blank line, and the phone kept as
    ``+84`` plus nine digits. A 10-digit ``09`` number is a different file and
    the phone does not import it.
    """
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        fold_line("FN:" + escape_vcard_text(name)),
        fold_line("TEL;TYPE=CELL:" + escape_vcard_text(phone)),
        "END:VCARD",
    ]
    return "\r\n".join(lines) + "\r\n"


def count_markers(path: Path) -> tuple[int, int]:
    begin = 0
    end = 0
    with path.open("rb") as handle:
        for line in handle:
            stripped = line.rstrip(b"\r\n")
            if stripped == b"BEGIN:VCARD":
                begin += 1
            elif stripped == b"END:VCARD":
                end += 1
    return begin, end


def truncate_to_contacts(path: Path, keep: int) -> None:
    """Keep the first ``keep`` complete contacts and drop a torn tail."""
    if keep < 0:
        raise VcfStructureError("Số contact cần giữ không hợp lệ")
    if keep == 0:
        path.unlink(missing_ok=True)
        return
    end_position: int | None = None
    seen = 0
    with path.open("rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                break
            if line.rstrip(b"\r\n") == b"END:VCARD":
                seen += 1
                if seen == keep:
                    end_position = handle.tell()
                    break
    if end_position is None:
        raise VcfStructureError(
            f"{path.name} chỉ có {seen} contact, tiến trình yêu cầu {keep}"
        )
    with path.open("r+b") as handle:
        handle.truncate(end_position)


def validate_vcf(path: Path, expected: int) -> None:
    """Reject a file whose cards are incomplete or whose count is wrong."""
    begin, end = count_markers(path)
    if begin != expected or end != expected or begin != end:
        raise VcfStructureError(
            f"{path.name}: BEGIN={begin}, END={end}, kỳ vọng {expected} contact"
        )
    text = path.read_text(encoding="utf-8")
    cards = _cards(text, path.name)
    if len(cards) != expected:
        raise VcfStructureError(
            f"{path.name}: đọc được {len(cards)} contact, kỳ vọng {expected}"
        )
    for index, card in enumerate(cards, start=1):
        if card[0] != "BEGIN:VCARD" or card[-1] != "END:VCARD":
            raise VcfStructureError(f"{path.name}: contact {index} thiếu BEGIN hoặc END")
        fields = {_field_name(line) for line in card}
        if "VERSION" not in fields or "FN" not in fields or "TEL" not in fields:
            raise VcfStructureError(
                f"{path.name}: contact {index} thiếu VERSION, FN hoặc TEL"
            )
        version = next(line for line in card if _field_name(line) == "VERSION")
        if version != "VERSION:3.0":
            raise VcfStructureError(f"{path.name}: contact {index} không phải vCard 3.0")
        for line in card:
            if "\n" in line or "\r" in line:
                raise VcfStructureError(
                    f"{path.name}: contact {index} còn ký tự xuống dòng thô"
                )


def _cards(text: str, name: str) -> list[list[str]]:
    logical = _unfold(text)
    cards: list[list[str]] = []
    current: list[str] | None = None
    for line in logical:
        if line == "BEGIN:VCARD":
            if current is not None:
                raise VcfStructureError(f"{name}: thiếu END:VCARD")
            current = [line]
            continue
        if line == "END:VCARD":
            if current is None:
                raise VcfStructureError(f"{name}: END:VCARD không có BEGIN:VCARD")
            current.append(line)
            cards.append(current)
            current = None
            continue
        if line == "":
            if current is not None:
                raise VcfStructureError(f"{name}: dòng trống nằm trong contact")
            continue
        if current is None:
            raise VcfStructureError(f"{name}: dữ liệu nằm ngoài contact")
        current.append(line)
    if current is not None:
        raise VcfStructureError(f"{name}: thiếu END:VCARD")
    return cards


def _unfold(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    logical: list[str] = []
    for line in normalized.split("\n"):
        if logical and line.startswith((" ", "\t")):
            logical[-1] += line[1:]
        else:
            logical.append(line)
    while logical and logical[-1] == "":
        logical.pop()
    return logical


def _field_name(line: str) -> str:
    head = line.split(":", 1)[0]
    return head.split(";")[0].upper()
