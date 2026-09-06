#!/usr/bin/env python3
"""Generate Vietnamese pragmatics situations in batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "pragmatics"

TEMPLATES: list[tuple[str, str, str]] = [
    ("Để em coi đã", "delay", "south"),
    ("Ừ cũng được", "soft_agreement", "south"),
    ("Đắt quá", "price_concern", "north"),
    ("Để hỏi vợ", "decision_maker_missing", "north"),
    ("Mai gọi lại nhé", "delay", "central"),
    ("Có bảo hành không", "ready_to_buy", "south"),
    ("Em xin phép không", "soft_rejection", "north"),
    ("Khoan đã", "delay", "south"),
    ("Có lừa không", "trust_concern", "south"),
    ("Thanh toán sao", "ready_to_buy", "north"),
]


def generate(batch_size: int, seed: int) -> list[dict]:
    rows: list[dict] = []
    for i in range(batch_size):
        text, intent, dialect = TEMPLATES[(i + seed) % len(TEMPLATES)]
        suffix = " ạ" if (i + seed) % 3 == 0 else ""
        rows.append(
            {
                "id": f"PRAG-SIT-{seed:04d}-{i+1:04d}",
                "text": f"{text}{suffix}",
                "dialect": dialect,
                "expected_top_intent": intent,
                "context_before": [
                    "Em chào chị ạ",
                    "Sản phẩm bên em đang ưu đãi tuần này",
                ],
                "context_after": ["Chị còn băn khoăn gì không ạ?"],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pragmatics situations")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUT / "situations_generated.jsonl",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = generate(args.batch_size, args.seed)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"written": len(rows), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
