"""Đọc video quay màn hình danh bạ TikTok, xuất bảng: số điện thoại, tên khách, id.

Dùng:
    python -m doc_video video.mp4 -o ket_qua.csv
    python -m doc_video anh1.jpg anh2.jpg --chi-tiet
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from doc_video.join import Result, add_full_name_rows, add_side_rows, build
from doc_video.ocr import check_tesseract
from doc_video.video import FRAME_FPS, read_inputs


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_result(result: Result, output: Path) -> list[Path]:
    review = output.with_name(f"{output.stem}.can-xem{output.suffix}")
    unopened = output.with_name(f"{output.stem}.chua-mo{output.suffix}")
    write_csv(output, ["Số điện thoại", "Tên khách", "ID"], [[r.phone, r.name, r.handle] for r in result.rows])
    write_csv(
        review,
        ["Số điện thoại", "Tên ở danh bạ", "Tên ở hồ sơ", "ID", "Lý do"],
        [[r.phone, r.list_name, r.profile_name, r.handle, r.reason] for r in result.review],
    )
    write_csv(unopened, ["Số điện thoại", "Tên khách"], [list(item) for item in result.unopened])
    return [output, review, unopened]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m doc_video", description="Ghép số điện thoại, tên khách, id từ video danh bạ TikTok.")
    parser.add_argument("inputs", nargs="+", type=Path, help="Video hoặc ảnh, theo đúng thứ tự quay.")
    parser.add_argument("-o", "--output", type=Path, default=Path("ket_qua.csv"), help="File bảng kết quả (CSV mở bằng Excel).")
    parser.add_argument(
        "--fps",
        type=float,
        default=FRAME_FPS,
        help="Nhịp giữ màn hình đứng yên mỗi giây. Mặc định 4. Lần mở ngắn vẫn được lấy riêng.",
    )
    parser.add_argument("--workers", type=int, default=None, help="Số tiến trình đọc song song.")
    parser.add_argument("--chi-tiet", action="store_true", help="In những gì đọc được trên từng khung.")
    args = parser.parse_args(argv)
    missing = [str(path) for path in args.inputs if not path.exists()]
    if missing:
        print("Không thấy file: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        check_tesseract()
        frames = read_inputs(args.inputs, fps=args.fps, workers=args.workers)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.chi_tiet:
        for frame in frames:
            where = f"{frame.at:7.1f}s" if frame.at >= 0 else "    ảnh"
            if frame.kind == "list":
                print(f"{where} danh bạ: " + "; ".join(f"{c.phone} {c.name}" for c in frame.contacts))
            elif frame.kind == "profile":
                print(f"{where} hồ sơ: {frame.profile_name} {frame.handle}")
    result = add_full_name_rows(add_side_rows(build(frames), frames), frames)
    print(f"Bảng kết quả: {len(result.rows)} hàng")
    for row in result.rows:
        print(f"  {row.phone} | {row.name} | {row.handle}")
    print(f"Cần xem: {len(result.review)} hàng")
    for item in result.review:
        print(f"  {item.phone or '—'} | {item.list_name or item.profile_name} | {item.handle or '—'} | {item.reason}")
    print(f"Có số nhưng chưa mở hồ sơ: {len(result.unopened)}")
    written = write_result(result, args.output)
    print("Đã ghi: " + ", ".join(str(path) for path in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
