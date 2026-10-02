"""Quy luật học từ các lần đọc thành công trong một video có cùng một định dạng.

Một trang hồ sơ luôn để @ và tên ở cùng một dải ngang. Dòng tên danh bạ đã chốt luôn có chiều cao trong một khoảng.
Mỗi video tự học các số này từ những lần đọc đã chốt, rồi dùng ngay trong video đó để:
- cứu trang hồ sơ mà đọc cả khung không thấy @, bằng cách đọc đúng dải đã học;
- bỏ phiếu những ô dòng sai hình dạng (quá cao, quá thấp, chạm mép ảnh), vì chưa lần nào chốt đúng ở đó.
"""

from __future__ import annotations

import statistics
import threading
from collections import OrderedDict
from typing import NamedTuple

_MIN_ZONE_SAMPLES = 3
# Các lần chốt cùng một dải không lệch quá ngần này điểm ảnh.
_ZONE_SLACK = 10
_MIN_HEIGHT_SAMPLES = 20
_SAMPLE_WINDOW = 60
_HEIGHT_WINDOW = 400
_SCOPES = 8


class Zone(NamedTuple):
    top: int
    bottom: int


def _consistent_zone(samples: list[tuple[int, int]]) -> Zone | None:
    if len(samples) < _MIN_ZONE_SAMPLES:
        return None
    middle = statistics.median(top for top, _bottom in samples)
    close = [(top, bottom) for top, bottom in samples if abs(top - middle) <= _ZONE_SLACK]
    if len(close) < _MIN_ZONE_SAMPLES:
        return None
    return Zone(
        int(statistics.median(top for top, _bottom in close)),
        int(statistics.median(bottom for _top, bottom in close)),
    )


class LayoutLearner:
    """Số đo học được của một video. An toàn khi nhiều luồng đọc khung cùng ghi."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handle: list[tuple[int, int]] = []
        self._name: list[tuple[int, int]] = []
        self._heights: list[int] = []
        self._tried: set[str] = set()
        self.counts = {"settled": 0, "skipped": 0, "rescued": 0}

    @staticmethod
    def _push(samples: list, item: object, window: int) -> None:
        samples.append(item)
        if len(samples) > window:
            del samples[0]

    def learn_handle(self, top: int, bottom: int) -> None:
        with self._lock:
            self._push(self._handle, (int(top), int(bottom)), _SAMPLE_WINDOW)

    def learn_name_zone(self, top: int, bottom: int) -> None:
        with self._lock:
            self._push(self._name, (int(top), int(bottom)), _SAMPLE_WINDOW)

    def learn_height(self, height: int) -> None:
        with self._lock:
            self._push(self._heights, int(height), _HEIGHT_WINDOW)
            self.counts["settled"] += 1

    def handle_zone(self) -> Zone | None:
        with self._lock:
            return _consistent_zone(list(self._handle))

    def name_zone(self) -> Zone | None:
        with self._lock:
            return _consistent_zone(list(self._name))

    def height_band(self) -> tuple[int, int] | None:
        """Khoảng chiều cao của dòng tên đã chốt, nới ra để không loại dòng đúng. None khi chưa đủ mẫu."""
        with self._lock:
            if len(self._heights) < _MIN_HEIGHT_SAMPLES:
                return None
            ordered = sorted(self._heights)
        low = ordered[len(ordered) // 10]
        high = ordered[(len(ordered) * 9) // 10]
        return max(6, int(low * 0.72)), int(high * 1.6) + 1

    def fits_name_box(self, box: tuple[int, int, int, int], image_height: int) -> bool:
        """Ô dòng tên có hình dạng của một dòng đọc được. Chạm mép ảnh hoặc sai chiều cao thì chưa lần nào chốt đúng."""
        _left, top, _width, height = box
        if top <= 0 or top + height >= image_height - 1:
            return False
        band = self.height_band()
        return band is None or band[0] <= height <= band[1]

    def note(self, name: str, count: int = 1) -> None:
        with self._lock:
            self.counts[name] = self.counts.get(name, 0) + count

    def first_try(self, frame: str) -> bool:
        """True ở lần đầu hỏi về khung này. Một khung chỉ được cứu một lần."""
        with self._lock:
            if frame in self._tried:
                return False
            self._tried.add(frame)
            return True

    def describe(self) -> str:
        """Một câu nói những gì video này đã dạy. Rỗng khi chưa học được gì."""
        parts: list[str] = []
        handle = self.handle_zone()
        name = self.name_zone()
        band = self.height_band()
        if handle is not None:
            parts.append(f"@ nằm ở dải y {handle.top} đến {handle.bottom}")
        if name is not None:
            parts.append(f"tên trên trang hồ sơ ở dải y {name.top} đến {name.bottom}")
        if band is not None:
            parts.append(f"dòng tên danh bạ chốt được cao {band[0]} đến {band[1]} điểm ảnh")
        with self._lock:
            settled = self.counts.get("settled", 0)
            skipped = self.counts.get("skipped", 0)
            rescued = self.counts.get("rescued", 0)
        if settled:
            parts.append(f"{settled} dòng đã chốt")
        if skipped:
            parts.append(f"bỏ {skipped} ô dòng sai hình dạng")
        if rescued:
            parts.append(f"cứu {rescued} trang hồ sơ")
        if not parts:
            return ""
        return "Quy luật học được: " + "; ".join(parts) + "."


_learners: OrderedDict[str, LayoutLearner] = OrderedDict()
_learners_lock = threading.Lock()


def learner_for(scope: str) -> LayoutLearner:
    with _learners_lock:
        found = _learners.get(scope)
        if found is None:
            found = LayoutLearner()
            _learners[scope] = found
            while len(_learners) > _SCOPES:
                _learners.popitem(last=False)
        else:
            _learners.move_to_end(scope)
        return found


def forget_scope(scope: str) -> None:
    with _learners_lock:
        _learners.pop(scope, None)


def clear_learners() -> None:
    with _learners_lock:
        _learners.clear()
