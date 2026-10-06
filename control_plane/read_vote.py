"""Đối chiếu một dòng tên hoặc @ bằng ba hướng, rồi năm lần đọc lại khi lệch.

Hướng 1 là chữ Tesseract nhanh đã có. Hướng 2 là bộ chữ chuẩn, cắt dòng, phóng đôi.
Hướng 3 là RapidOCR chỉ nhận dạng đúng dòng đó, không dò chữ lại. Chưa cài RapidOCR thì hướng 3 là một lần Tesseract khác.
Hai hướng trùng thì không gọi hướng 3. Cả ba khác nhau thì đọc lại năm kiểu ảnh.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from control_plane.people import clean_name, clean_username, fold_name, mark_count
from control_plane.syllables import restore_name

try:
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    np = None  # type: ignore[assignment]
    RapidOCR = None  # type: ignore[misc, assignment]

_engine: object | None = None
_engine_failed = False
_engine_lock = threading.Lock()

# Một dòng danh bạ cuộn qua màn hình hiện ở nhiều khung. Ba khung khác nhau cùng chốt một chữ thì các khung sau dùng lại.
SETTLE_VOTES = 3
SIGNATURE_SIZE = (48, 8)
# Trung bình lệch điểm ảnh giữa hai lần chụp cùng một dòng nằm dưới 30. Hai dòng khác nhau từ 60 trở lên.
SIGNATURE_LIMIT = 30.0
_LEFT_SLACK = 8
_WIDTH_SLACK = 10
_ROWS_PER_SEED = 6
# Video dài vài chục phút có hàng chục nghìn cách đọc khác nhau. Quá mức này thì bỏ cách đọc cũ nhất.
_MAX_SEEDS = 60_000
_MEMO_SCOPES = 8


def rapid_ready() -> bool:
    """RapidOCR nhập được. Lần khởi tạo lỗi thì coi như chưa có."""
    return RapidOCR is not None and np is not None and not _engine_failed


@dataclass
class _Row:
    left: int
    width: int
    signature: Image.Image
    frames: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    settled: str = ""


def row_signature(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Ảnh xám rất nhỏ của đúng hộp chữ, để nhận ra cùng một dòng ở khung khác."""
    left, top, width, height = box
    gray = image if image.mode == "L" else image.convert("L")
    crop = gray.crop((left, top, left + max(1, width), top + max(1, height)))
    return crop.resize(SIGNATURE_SIZE, Image.Resampling.BOX)


def _alike(first: Image.Image, second: Image.Image) -> bool:
    if first.size != second.size:
        return False
    diff = ImageChops.difference(first, second)
    return ImageStat.Stat(diff).mean[0] <= SIGNATURE_LIMIT


class RowMemo:
    """Dòng đã chốt trong một video. Nhận ra dòng bằng chữ đọc nhanh y hệt, vị trí cột, và ảnh nhỏ gần giống.

    Một dòng chỉ được dùng lại sau khi đủ SETTLE_VOTES khung khác nhau cùng chốt một chữ y hệt.
    Một lần lệch hoặc không chốt được thì đếm lại từ đầu, dòng đó tiếp tục bỏ phiếu ở mọi khung.
    """

    def __init__(self, settle: int = SETTLE_VOTES) -> None:
        self._settle = max(1, int(settle))
        self._rows: dict[tuple[str, str], list[_Row]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(kind: str, seed: str) -> tuple[str, str] | None:
        text = " ".join(str(seed or "").split())
        return (kind, text) if text else None

    def _find(self, key: tuple[str, str], left: int, width: int, signature: Image.Image) -> _Row | None:
        for row in self._rows.get(key, []):
            if abs(row.left - left) <= _LEFT_SLACK and abs(row.width - width) <= _WIDTH_SLACK and _alike(row.signature, signature):
                return row
        return None

    def settled(self, kind: str, seed: str, left: int, width: int, signature: Image.Image) -> str | None:
        key = self._key(kind, seed)
        if key is None:
            return None
        with self._lock:
            row = self._find(key, left, width, signature)
            return row.settled if row is not None and row.settled else None

    def _row(self, key: tuple[str, str], left: int, width: int, signature: Image.Image) -> _Row:
        row = self._find(key, left, width, signature)
        if row is not None:
            return row
        rows = self._rows.get(key)
        if rows is None:
            if len(self._rows) >= _MAX_SEEDS:
                self._rows.pop(next(iter(self._rows)))
            rows = self._rows[key] = []
        if len(rows) >= _ROWS_PER_SEED:
            rows.pop(0)
        row = _Row(left, width, signature)
        rows.append(row)
        return row

    def agree(self, kind: str, seed: str, left: int, width: int, signature: Image.Image, frame: str, text: str) -> bool:
        """Ghi một lần đối chiếu đã trùng ở khung này. Trả True đúng lúc dòng vừa được chốt hẳn."""
        key = self._key(kind, seed)
        if key is None or not text:
            return False
        with self._lock:
            row = self._row(key, left, width, signature)
            if row.settled or frame in row.frames:
                return False
            if row.texts and row.texts[-1] != text:
                row.frames.clear()
                row.texts.clear()
            row.frames.append(frame)
            row.texts.append(text)
            if len(row.texts) >= self._settle:
                row.settled = text
                return True
        return False

    def fail(self, kind: str, seed: str, left: int, width: int, signature: Image.Image) -> None:
        """Lần đối chiếu không chốt được: dòng này chưa ổn, đếm lại từ đầu."""
        key = self._key(kind, seed)
        if key is None:
            return
        with self._lock:
            row = self._find(key, left, width, signature)
            if row is not None and not row.settled:
                row.frames.clear()
                row.texts.clear()


_memos: OrderedDict[str, RowMemo] = OrderedDict()
_memos_lock = threading.Lock()


def memo_for(scope: str) -> RowMemo:
    """Bộ nhớ dòng của một video. Giữ vài video gần nhất."""
    with _memos_lock:
        found = _memos.get(scope)
        if found is None:
            found = RowMemo()
            _memos[scope] = found
            while len(_memos) > _MEMO_SCOPES:
                _memos.popitem(last=False)
        else:
            _memos.move_to_end(scope)
        return found


def forget_scope(scope: str) -> None:
    with _memos_lock:
        _memos.pop(scope, None)


def clear_memos() -> None:
    with _memos_lock:
        _memos.clear()


def vote_key(text: str, kind: str) -> str:
    """Khóa so trùng. Tên gom dấu. @ không phân biệt hoa thường."""
    if kind == "handle":
        handle = clean_username(text)
        return handle.casefold()
    cleaned = clean_name(text)
    letters = [char for char in cleaned if char.isalpha()]
    if len(letters) < 2:
        return ""
    return fold_name(cleaned)


def _groups(reads: list[str], kind: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for text in reads:
        key = vote_key(text, kind)
        if not key:
            continue
        found.setdefault(key, []).append(text)
    return found


def _restore(text: str, _texts: list[str]) -> str:
    """Chuyển dấu thanh đã đọc về đúng nguyên âm. Không điền dấu khi chữ đọc được không có dấu."""
    cleaned = clean_name(text)
    if not cleaned:
        return ""
    return restore_name(cleaned, allow_unique=False)


def _show(texts: list[str], kind: str) -> str:
    """Chữ được ghi từ một nhóm cách đọc đã trùng chữ gốc. Tên: nhiều phiếu nhất, hòa thì nhiều dấu hơn, rồi thứ tự."""
    if kind == "handle":
        for text in texts:
            handle = clean_username(text)
            if handle:
                return handle
        return ""
    names = [clean_name(text) for text in texts if vote_key(text, "name")]
    if not names:
        return ""
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for position, name in enumerate(names):
        counts[name] = counts.get(name, 0) + 1
        first.setdefault(name, position)
    return max(counts, key=lambda name: (counts[name], mark_count(name), -first[name]))


def _richest(texts: list[str]) -> str:
    names = [clean_name(text) for text in texts if vote_key(text, "name")]
    if not names:
        return ""
    return max(names, key=lambda name: (mark_count(name), -names.index(name)))


def needs_reread(seed: str, second: str, third: str | None, kind: str) -> bool:
    """Hai hướng đã trùng thì thôi. Một hướng có chữ, các hướng kia trống, thì thôi."""
    reads = [seed, second] if third is None else [seed, second, third]
    groups = _groups(reads, kind)
    if any(len(items) >= 2 for items in groups.values()):
        return False
    nonempty = [text for text in reads if vote_key(text, kind)]
    return len(nonempty) >= 2


def vote_line(seed: str, second: str, third: str | None, reruns: list[str], *, kind: str) -> tuple[str, bool]:
    """Trả chữ được ghi và cờ đã có ít nhất hai hướng trùng.

    Cờ sai khi chỉ còn một hướng đọc được: giữ chữ hướng đó, bộ lọc conf cũ vẫn áp dụng.
    Năm lần đọc lại hòa, hoặc không chuỗi nào hơn một phiếu, thì trả chuỗi rỗng.
    """
    reads = [seed, second] if third is None else [seed, second, third]
    groups = _groups(reads, kind)
    if groups:
        winner = max(groups.values(), key=len)
        if len(winner) >= 2:
            if kind == "name":
                # Các hướng đã trùng chữ gốc, chỉ có thể khác dấu. Cách viết lấy từ bộ chữ chuẩn đọc riêng ô dòng (hướng 2):
                # trên danh sách mẫu nó đúng 77% khi lệch dấu với bộ nhanh (bộ nhanh 12%), và không tự thêm dấu vào tên không dấu.
                # RapidOCR chỉ trả chữ gốc nên không được lấn phiếu của hướng 2.
                # Sau đó dấu thanh đặt sai nguyên âm được chuyển về đúng chỗ. Không điền dấu khi chữ đọc được không có dấu.
                if second in winner and vote_key(second, "name"):
                    return _restore(second, winner), True
                return _restore(_richest(winner), winner), True
            return _show(winner, kind), True
    nonempty = [text for text in reads if vote_key(text, kind)]
    if len(nonempty) < 2:
        shown = _show([seed], kind)
        if kind == "name" and shown:
            shown = restore_name(shown, allow_unique=False)
        return shown, False
    rerun_groups = _groups(reruns, kind)
    if not rerun_groups:
        return "", False
    ranked = sorted(rerun_groups.values(), key=len, reverse=True)
    if len(ranked[0]) < 2:
        return "", False
    if len(ranked) > 1 and len(ranked[1]) == len(ranked[0]):
        return "", False
    shown = _show(ranked[0], kind)
    if kind == "name" and shown:
        shown = _restore(shown, ranked[0])
    return shown, True


def five_variants(crop: Image.Image) -> list[Image.Image]:
    """Năm kiểu ảnh cho lần đọc lại. Cùng một ảnh đưa vào Tesseract thì ra cùng một chuỗi."""
    width = max(1, crop.width)
    height = max(1, crop.height)
    return [
        crop.copy(),
        crop.resize((width * 2, height * 2), Image.Resampling.LANCZOS),
        ImageOps.autocontrast(crop),
        crop.filter(ImageFilter.SHARPEN),
        crop.resize((width * 3, height * 3), Image.Resampling.LANCZOS),
    ]


def _rapid_text(out: object) -> str:
    """Chữ từ kết quả RapidOCR. Chỉ nhận dạng cho [chữ, điểm]. Có dò chữ cho [khung, chữ, điểm]."""
    payload = out[0] if isinstance(out, tuple) else out
    if payload is None:
        return ""
    txts = getattr(payload, "txts", None)
    if txts:
        return " ".join(str(item) for item in txts if item)
    if not isinstance(payload, list):
        return ""
    lines: list[str] = []
    for item in payload:
        if isinstance(item, str):
            lines.append(item)
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        if isinstance(item[0], str):
            lines.append(item[0])
        elif isinstance(item[1], str):
            lines.append(item[1])
    return " ".join(line for line in lines if line)


def _make_engine() -> object:
    """Mỗi phiên một luồng: nhiều dòng đọc cùng lúc không giành nhau lõi."""
    try:
        return RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    except TypeError:
        return RapidOCR()


def read_rapid(image: Image.Image) -> str | None:
    """Nhận dạng một dòng đã cắt. None khi máy chưa có RapidOCR. Chuỗi rỗng khi không thấy chữ."""
    global _engine, _engine_failed
    if RapidOCR is None or np is None or _engine_failed:
        return None
    with _engine_lock:
        if _engine is None:
            try:
                _engine = _make_engine()
            except (OSError, RuntimeError, ValueError):
                _engine_failed = True
                return None
        engine = _engine
    if engine is None:
        return None
    try:
        pixels = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
    except (OSError, ValueError):
        return ""
    try:
        out = engine(pixels, use_det=False, use_cls=False, use_rec=True)  # type: ignore[operator]
    except TypeError:
        try:
            out = engine(pixels)  # type: ignore[operator]
        except (OSError, RuntimeError, ValueError):
            return ""
    except (OSError, RuntimeError, ValueError):
        return ""
    return _rapid_text(out)
