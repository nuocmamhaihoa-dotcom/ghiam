#!/usr/bin/env python3
"""Validate VECD datasets — schema, duplicates, dialect/industry balance."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

UTTERANCE_FIELDS = [
    "id",
    "conversation_id",
    "speaker",
    "text",
    "dialect",
    "industry",
    "stage",
    "intent",
    "emotion",
    "objection_type",
    "buying_signal",
    "confidence",
    "recommended_response",
    "root_cause_if_failed",
]

TARGETS = {
    "customers": 100_000,
    "agents": 50_000,
    "conversations": 10_000,
    "intents": 1_000,
    "objections": 5_000,
    "emotions": 500,
    "buying_signals": 500,
    "root_causes": 500,
    "golden_calls": 500,
    "qa_benchmark": 10_000,
}


def latest_jsonl(folder: Path, prefix: str) -> Path | None:
    """Pick the largest corpus by line count (not lexicographic name)."""
    files = list(folder.glob(f"{prefix}_*.jsonl"))
    if not files:
        return None

    def line_count(path: Path) -> int:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    return max(files, key=line_count)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dialect_ok(rows: list[dict], tol: float = 0.03) -> tuple[bool, dict]:
    c = Counter(r.get("dialect") for r in rows)
    total = sum(c.values()) or 1
    ratios = {k: c.get(k, 0) / total for k in ("north", "central", "south")}
    ok = (
        abs(ratios["north"] - 0.35) <= tol
        and abs(ratios["central"] - 0.20) <= tol
        and abs(ratios["south"] - 0.45) <= tol
    )
    return ok, ratios


def check_utterances(name: str, path: Path) -> dict:
    rows = load_jsonl(path)
    errors: list[str] = []
    if len(rows) != TARGETS[name]:
        errors.append(f"count:{len(rows)}!= {TARGETS[name]}")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_ids")
    texts = [r.get("text") for r in rows]
    if len(texts) != len(set(texts)):
        errors.append(f"duplicate_texts:{len(texts)-len(set(texts))}")
    for i, row in enumerate(rows[:2000]):
        for f in UTTERANCE_FIELDS:
            if f not in row:
                errors.append(f"missing:{f}:row{i}")
                break
            if f == "objection_type":
                continue
            if row[f] in ("",):
                errors.append(f"empty:{f}:row{i}")
                break
        if len(errors) > 30:
            break
    ok_d, ratios = dialect_ok(rows)
    if not ok_d:
        errors.append(f"dialect_balance:{ratios}")
    industries = {r.get("industry") for r in rows}
    if len(industries) < 20:
        errors.append(f"industries:{len(industries)}<20")
    return {
        "collection": name,
        "file": str(path),
        "count": len(rows),
        "ok": not errors,
        "errors": errors[:40],
        "dialect_ratios": ratios,
        "industries": len(industries),
    }


def check_count(name: str, path: Path, required: list[str]) -> dict:
    rows = load_jsonl(path)
    errors: list[str] = []
    if len(rows) != TARGETS[name]:
        errors.append(f"count:{len(rows)}!={TARGETS[name]}")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_ids")
    for i, row in enumerate(rows[:1000]):
        for f in required:
            if f not in row or row[f] in ("", None):
                errors.append(f"missing:{f}:row{i}")
                if len(errors) > 30:
                    break
        if len(errors) > 30:
            break
    return {
        "collection": name,
        "file": str(path),
        "count": len(rows),
        "ok": not errors,
        "errors": errors[:40],
    }


def main() -> int:
    results = []
    cust = latest_jsonl(ROOT / "customers", "customers")
    agent = latest_jsonl(ROOT / "agents", "agents")
    if not cust or not agent:
        print("Missing customers/agents jsonl")
        return 1
    results.append(check_utterances("customers", cust))
    results.append(check_utterances("agents", agent))

    checks = [
        ("conversations", ["id", "dialect", "industry", "kind", "utterances"]),
        ("intents", ["id", "name", "definition", "trigger", "counter_example", "confidence_rule", "coaching"]),
        ("emotions", ["id", "name", "trigger", "voice_pattern", "text_pattern", "coaching"]),
        ("buying_signals", ["id", "text", "strength_score", "confidence", "recommended_next_step"]),
        ("objections", ["id", "group", "text", "hidden_meaning", "root_cause", "good_response", "forbidden_response", "practice_exercise"]),
        ("root_causes", ["id", "customer_line", "agent_line", "root_cause", "coaching", "graph"]),
        ("golden_calls", ["id", "immutable", "checksum", "source_conversation_id"]),
        ("qa_benchmark", ["id", "ai_score", "human_score", "difference", "reviewer", "reason"]),
    ]
    for name, req in checks:
        path = latest_jsonl(ROOT / name, name)
        if not path:
            results.append({"collection": name, "ok": False, "errors": ["missing_file"]})
            continue
        results.append(check_count(name, path, req))

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = {
        "all_ok": all(r.get("ok") for r in results),
        "results": results,
    }
    (REPORTS / "validation_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
