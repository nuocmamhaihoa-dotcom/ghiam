#!/usr/bin/env python3
"""Validate VECD datasets — duplicate, balance, schema, JSON gates."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "id", "conversation_id", "speaker", "text", "dialect", "industry", "stage",
    "intent", "emotion", "objection_type", "buying_signal", "confidence",
    "recommended_response", "root_cause_if_failed",
]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def check_file(path: Path, fields: list[str] | None = None) -> dict:
    rows = load_jsonl(path)
    errors = []
    ids = []
    for i, r in enumerate(rows):
        ids.append(r.get("id"))
        if fields:
            for f in fields:
                if f not in r:
                    errors.append(f"{path.name}:row{i}:missing:{f}")
                    if len(errors) > 50:
                        break
        if len(errors) > 50:
            break
    if len(ids) != len(set(ids)):
        errors.append(f"{path.name}:duplicate_ids")
    return {"file": str(path), "count": len(rows), "ok": not errors, "errors": errors}


def dialect_balance(path: Path) -> dict:
    rows = load_jsonl(path)
    c = Counter(r.get("dialect") for r in rows)
    total = sum(c.values()) or 1
    ratios = {k: c.get(k, 0) / total for k in ("bac", "trung", "nam")}
    ok = (
        abs(ratios.get("bac", 0) - 0.35) <= 0.08
        and abs(ratios.get("trung", 0) - 0.20) <= 0.08
        and abs(ratios.get("nam", 0) - 0.45) <= 0.08
    )
    return {"ratios": ratios, "ok": ok}


def main() -> None:
    targets = [
        (ROOT / "customers" / "customers_100000.jsonl", REQUIRED),
        (ROOT / "agents" / "agents_50000.jsonl", REQUIRED),
        (ROOT / "conversations" / "conversations_10000.jsonl", ["id", "dialect", "industry", "kind", "utterances"]),
        (ROOT / "intents" / "intents_1000.jsonl", ["id", "code", "name"]),
        (ROOT / "objections" / "objections_5000.jsonl", ["id", "group", "text"]),
        (ROOT / "emotions" / "emotions_500.jsonl", ["id", "code"]),
        (ROOT / "buying_signals" / "buying_signals_500.jsonl", ["id", "text"]),
        (ROOT / "root_causes" / "root_causes_500.jsonl", ["id", "graph"]),
        (ROOT / "golden_calls" / "golden_calls_500.jsonl", ["id", "immutable", "checksum"]),
        (ROOT / "qa_benchmark" / "qa_benchmark_10000.jsonl", ["id", "ai_score", "human_score"]),
        (ROOT.parent / "ai-brain" / "rulebook" / "rules_1000.jsonl", ["id", "category", "name", "weight"]),
    ]
    results = []
    for path, fields in targets:
        if not path.exists():
            results.append({"file": str(path), "ok": False, "errors": ["missing_file"]})
            continue
        results.append(check_file(path, fields))
    bal_c = dialect_balance(ROOT / "customers" / "customers_100000.jsonl")
    bal_a = dialect_balance(ROOT / "agents" / "agents_50000.jsonl")
    out = {
        "results": results,
        "dialect_customer": bal_c,
        "dialect_agent": bal_a,
        "all_ok": all(r["ok"] for r in results) and bal_c["ok"] and bal_a["ok"],
    }
    out_path = ROOT / "reports" / "validation_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_ok": out["all_ok"], "files": len(results)}, indent=2))
    sys.exit(0 if out["all_ok"] else 1)


if __name__ == "__main__":
    main()
