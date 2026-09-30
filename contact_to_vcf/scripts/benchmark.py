"""Time a streaming conversion. Usage: python scripts/benchmark.py 100000"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from models.records import JobConfig  # noqa: E402
from processors.streaming_engine import execute  # noqa: E402


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "bench.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            handle.write("name,phone\n")
            for index in range(count):
                handle.write(f"N{index},09{index:08d}\n")
        started = time.perf_counter()
        result = execute(
            JobConfig(
                input_path=source,
                output_dir=root / "out",
                file_format="csv",
                delimiter=",",
                has_header=True,
                name_column=0,
                phone_column=1,
                contacts_per_file=5000,
                chunk_size=5000,
            )
        )
        elapsed = time.perf_counter() - started
    report = result.report
    if report is None:
        raise SystemExit("Benchmark không tạo báo cáo")
    print(report.render(), end="")
    print(f"WALL: {elapsed:.2f}s")
    if report.exported != count:
        raise SystemExit(f"Thiếu contact: {report.exported} / {count}")


if __name__ == "__main__":
    main()
