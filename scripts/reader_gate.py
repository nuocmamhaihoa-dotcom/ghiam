"""Cổng chống lùi: một thay đổi bộ đọc chỉ được lên khi không làm mất dòng đủ 3 ô nào.

Chạy trên máy nào cũng được (máy thử, PC rảnh, VPS lúc hàng chờ trống):

    # Đọc tập chuẩn bằng bộ đọc hiện tại, rồi bằng bản cần thử.
    python scripts/reader_gate.py read --videos tap-chuan/ --out doc-goc/
    python scripts/reader_gate.py read --videos tap-chuan/ --out doc-thu/ --transform fps2
    # So hai lần đọc. Mã thoát 1 nếu bản thử làm mất dòng đủ 3 ô hoặc tổng số dòng đủ giảm.
    python scripts/reader_gate.py compare doc-goc/ doc-thu/

Dòng đủ 3 ô là dòng có số điện thoại, tên và @id. Mất là số có dòng đủ ở lần đọc gốc mà
lần đọc thử không còn dòng đủ với đúng tên và @id đó. Đổi tên hoặc @id cũng tính là mất.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control_plane.video_shrink import DEFAULT_CRF, DEFAULT_PRESET, shrink  # noqa: E402
from doc_video.pipeline import read_video  # noqa: E402
from doc_video.video import IMAGE_SUFFIXES, VIDEO_SUFFIXES  # noqa: E402


def _cell(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def read_set(videos: Path, out: Path, transform: str = "", crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET) -> int:
    """Đọc từng video trong thư mục, ghi bảng ra out/<tên>.json. Video đã có kết quả thì bỏ qua."""
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in videos.iterdir() if path.suffix.lower() in VIDEO_SUFFIXES | IMAGE_SUFFIXES)
    for path in files:
        target = out / f"{path.stem}.json"
        if target.is_file():
            continue
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="gate-") as tmp:
            source = path
            note: dict[str, object] = {}
            if transform == "fps2":
                source = Path(tmp) / f"{path.stem}.mp4"
                if not shrink(path, source, crf=crf, preset=preset, threads=0, nice=0):
                    print(f"{path.name}: không thu nhỏ được", flush=True)
                    return 2
                note = {"bytes_before": path.stat().st_size, "bytes_after": source.stat().st_size, "crf": crf, "preset": preset}
            result, frames = read_video([source])
        payload = {
            "video": path.name,
            "transform": transform,
            "seconds": round(time.monotonic() - started, 1),
            "frames": len(frames),
            "rows": [[row.phone, row.name, row.handle] for row in result.rows],
            "review": [[item.phone, item.list_name, item.profile_name, item.handle, item.reason] for item in result.review],
            "unopened": [[phone, name] for phone, name in result.unopened],
            **note,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"{path.name}: {len(result.rows)} dòng, {len(frames)} khung, {payload['seconds']} giây", flush=True)
    return 0


def full_rows(payload: dict) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for phone, name, handle in payload.get("rows", []):
        phone, name, handle = _cell(phone), _cell(name), _cell(handle)
        if phone and name and handle:
            found[phone] = (name, handle)
    return found


def compare_sets(base: Path, trial: Path) -> dict[str, object]:
    """Số dòng đủ mất, đổi, thêm theo từng video và tổng cộng."""
    videos = []
    totals = {"base": 0, "trial": 0, "lost": 0, "changed": 0, "gained": 0}
    for path in sorted(base.glob("*.json")):
        other = trial / path.name
        if not other.is_file():
            continue
        left = full_rows(json.loads(path.read_text(encoding="utf-8")))
        right = full_rows(json.loads(other.read_text(encoding="utf-8")))
        lost = [phone for phone in left if phone not in right]
        changed = [phone for phone in left if phone in right and right[phone] != left[phone]]
        gained = [phone for phone in right if phone not in left]
        item = {
            "video": path.stem,
            "base": len(left),
            "trial": len(right),
            "lost": len(lost),
            "changed": len(changed),
            "gained": len(gained),
        }
        videos.append(item)
        for key in totals:
            totals[key] += int(item[key])
    passed = bool(videos) and totals["lost"] == 0 and totals["changed"] == 0 and totals["trial"] >= totals["base"]
    return {"videos": videos, "totals": totals, "passed": passed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read", help="đọc mọi video trong một thư mục")
    read.add_argument("--videos", type=Path, required=True)
    read.add_argument("--out", type=Path, required=True)
    read.add_argument("--transform", choices=["", "fps2"], default="")
    read.add_argument("--crf", type=int, default=DEFAULT_CRF)
    read.add_argument("--preset", default=DEFAULT_PRESET)
    compare = sub.add_parser("compare", help="so lần đọc thử với lần đọc gốc")
    compare.add_argument("base", type=Path)
    compare.add_argument("trial", type=Path)
    args = parser.parse_args(argv)
    if args.command == "read":
        return read_set(args.videos, args.out, args.transform, args.crf, args.preset)
    report = compare_sets(args.base, args.trial)
    for item in report["videos"]:
        print(
            f"{item['video']}: gốc {item['base']} dòng đủ, thử {item['trial']}, "
            f"mất {item['lost']}, đổi {item['changed']}, thêm {item['gained']}"
        )
    totals = report["totals"]
    print(
        f"Tổng: gốc {totals['base']}, thử {totals['trial']}, mất {totals['lost']}, "
        f"đổi {totals['changed']}, thêm {totals['gained']}. {'ĐẠT' if report['passed'] else 'KHÔNG ĐẠT'}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
