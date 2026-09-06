"""Dataset factory + quality gate for Vietnamese Pragmatics Engine 2.0."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "datasets" / "pragmatics"
MODELS = ROOT / "models" / "pragmatics"

DIALECTS = ("north", "central", "south")

UTTERANCE_SEEDS: list[dict[str, Any]] = [
    {"base": "Để em coi đã", "intent": "delay", "dialect": "south", "bucket": "soft_delay"},
    {"base": "Để em xem đã", "intent": "delay", "dialect": "north", "bucket": "soft_delay"},
    {"base": "Ừ cũng được", "intent": "soft_agreement", "dialect": "south", "bucket": "fake_or_soft_yes"},
    {"base": "Cũng được đó", "intent": "soft_agreement", "dialect": "south", "bucket": "fake_or_soft_yes"},
    {"base": "Đắt quá", "intent": "price_concern", "dialect": "north", "bucket": "price"},
    {"base": "Mắc quá trời", "intent": "price_concern", "dialect": "south", "bucket": "price"},
    {"base": "Để hỏi vợ", "intent": "decision_maker_missing", "dialect": "north", "bucket": "decision"},
    {"base": "Phải bàn với chồng", "intent": "decision_maker_missing", "dialect": "central", "bucket": "decision"},
    {"base": "Mai gọi lại nhé", "intent": "delay", "dialect": "north", "bucket": "callback"},
    {"base": "Đang bận, gọi sau", "intent": "busy", "dialect": "south", "bucket": "callback"},
    {"base": "Ừ ừ được rồi", "intent": "fake_agreement", "dialect": "north", "bucket": "fake_yes"},
    {"base": "Ok ok", "intent": "fake_agreement", "dialect": "south", "bucket": "fake_yes"},
    {"base": "Thôi để sau", "intent": "soft_rejection", "dialect": "north", "bucket": "soft_no"},
    {"base": "Em xin phép không", "intent": "soft_rejection", "dialect": "north", "bucket": "soft_no"},
    {"base": "Không cần nữa", "intent": "real_refusal", "dialect": "south", "bucket": "hard_no"},
    {"base": "Đừng gọi nữa", "intent": "exit_imminent", "dialect": "north", "bucket": "exit"},
    {"base": "Gác máy đây", "intent": "exit_imminent", "dialect": "south", "bucket": "exit"},
    {"base": "Bao giờ giao", "intent": "ready_to_buy", "dialect": "north", "bucket": "buy"},
    {"base": "Thanh toán sao", "intent": "ready_to_buy", "dialect": "south", "bucket": "buy"},
    {"base": "Có bảo hành không", "intent": "need_information", "dialect": "central", "bucket": "info"},
    {"base": "Công ty ở đâu", "intent": "trust_concern", "dialect": "north", "bucket": "trust"},
    {"base": "Có lừa không", "intent": "trust_concern", "dialect": "south", "bucket": "trust"},
    {"base": "À nhân tiện hỏi", "intent": "topic_shift", "dialect": "south", "bucket": "shift"},
    {"base": "Răng rồi", "intent": "need_information", "dialect": "central", "bucket": "central"},
    {"base": "Chi rứa", "intent": "need_information", "dialect": "central", "bucket": "central"},
    {"base": "Hong có", "intent": "soft_rejection", "dialect": "south", "bucket": "slang"},
    {"base": "Vâng để em xem", "intent": "delay", "dialect": "north", "bucket": "soft_delay"},
    {"base": "Bớt được không", "intent": "negotiation", "dialect": "south", "bucket": "price"},
    {"base": "Để tính đã", "intent": "delay", "dialect": "north", "bucket": "soft_delay"},
    {"base": "Nghe cũng ổn", "intent": "soft_agreement", "dialect": "north", "bucket": "fake_or_soft_yes"},
]

PREFIXES = ["", "Ờ ", "À ", "Ừ thì ", "Thôi ", "Vậy ", "Nói thật "]
SUFFIXES = ["", " ạ", " nhé", " nha", " đó", " rồi", " á", " hen", " đi"]
AGENTS = [
    "Em chào anh chị, bên em có chương trình ưu đãi.",
    "Gói này gồm bảo hành 12 tháng ạ.",
    "Em hỗ trợ giao trong 48 giờ.",
    "Anh chị thanh toán chuyển khoản hoặc COD đều được.",
    "Em gửi thêm thông tin công ty và giấy phép.",
]


@dataclass
class GateResult:
    ok: bool
    errors: list[str]
    meta: dict[str, Any]


def _uid(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def expand_utterances(target: int = 100_000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    i = 0
    while len(rows) < target:
        seed = UTTERANCE_SEEDS[i % len(UTTERANCE_SEEDS)]
        prefix = PREFIXES[(i // len(UTTERANCE_SEEDS)) % len(PREFIXES)]
        suffix = SUFFIXES[(i // (len(UTTERANCE_SEEDS) * len(PREFIXES))) % len(SUFFIXES)]
        dialect = DIALECTS[i % 3]
        text = f"{prefix}{seed['base']}{suffix}".strip()
        rows.append(
            {
                "id": _uid("UTT", i, text, dialect),
                "text": text,
                "dialect": dialect,
                "expected_top_intent": seed["intent"],
                "bucket": seed["bucket"],
                "batch": len(rows) // 500,
            }
        )
        i += 1
    return rows


def expand_situations(target: int = 5_000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    i = 0
    while len(rows) < target:
        seed = UTTERANCE_SEEDS[i % len(UTTERANCE_SEEDS)]
        agent = AGENTS[i % len(AGENTS)]
        follow = AGENTS[(i + 2) % len(AGENTS)]
        customer = f"{PREFIXES[i % len(PREFIXES)]}{seed['base']}{SUFFIXES[i % len(SUFFIXES)]}".strip()
        dialect = DIALECTS[i % 3]
        rows.append(
            {
                "id": _uid("SIT", i, customer, dialect),
                "dialect": dialect,
                "expected_top_intent": seed["intent"],
                "bucket": seed["bucket"],
                "context_before": [agent, AGENTS[(i + 1) % len(AGENTS)]],
                "text": customer,
                "context_after": [follow],
                "batch": len(rows) // 500,
            }
        )
        i += 1
    return rows


def _filter_bucket(rows: Iterable[dict[str, Any]], buckets: set[str], target: int) -> list[dict[str, Any]]:
    out = [r for r in rows if r.get("bucket") in buckets]
    if len(out) >= target:
        return out[:target]
    extended = list(out)
    n = 0
    while len(extended) < target and out:
        base = out[n % len(out)]
        clone = dict(base)
        clone["id"] = _uid(str(base["id"]), "x", n)
        clone["text"] = f"{base['text']} ({n % 7})"
        extended.append(clone)
        n += 1
    return extended[:target]


def quality_gate(rows: list[dict[str, Any]], *, label: str) -> GateResult:
    errors: list[str] = []
    texts = [str(r.get("text") or "").strip().lower() for r in rows]
    if any(not t for t in texts):
        errors.append("empty text present")

    unmarked_dup = 0
    seen: set[str] = set()
    for r in rows:
        t = str(r.get("text") or "").strip().lower()
        if "(" in t and t.endswith(")"):
            continue
        if t in seen:
            unmarked_dup += 1
        seen.add(t)
    if unmarked_dup > 0:
        errors.append(f"duplicate texts in batch: {unmarked_dup}")

    dialects = Counter(str(r.get("dialect")) for r in rows)
    for d in DIALECTS:
        share = dialects.get(d, 0) / max(len(rows), 1)
        if share < 0.20:
            errors.append(f"dialect imbalance: {d}={share:.2%}")

    intents = Counter(str(r.get("expected_top_intent")) for r in rows)
    if len(intents) < 5:
        errors.append(f"intent diversity too low: {len(intents)}")
    for intent, count in intents.items():
        share = count / max(len(rows), 1)
        if share > 0.45:
            errors.append(f"intent dominance: {intent}={share:.2%}")

    for r in rows[:50]:
        json.dumps(r, ensure_ascii=False)

    from pragmatics import VietnamesePragmaticsEngine

    engine = VietnamesePragmaticsEngine(context_radius=5)
    checked = 0
    hits = 0
    for r in rows[: min(80, len(rows))]:
        turns = [
            {"speaker": "agent", "text": "Em tư vấn gói sản phẩm ạ"},
            {"speaker": "customer", "text": r["text"]},
        ]
        if r.get("context_before"):
            turns = [{"speaker": "agent", "text": x} for x in r["context_before"]] + [
                {"speaker": "customer", "text": r["text"]}
            ]
        result = engine.analyze(turns, dialect_hint=r.get("dialect"))
        checked += 1
        if result.status != "ok":
            continue
        customer = [t for t in result.turns if t.status == "ok"]
        if not customer:
            continue
        ranked = sorted(
            customer[-1].intent_probability,
            key=customer[-1].intent_probability.get,
            reverse=True,
        )
        if r["expected_top_intent"] in ranked[:3]:
            hits += 1
    accuracy = hits / max(checked, 1)
    if accuracy < 0.70:
        errors.append(f"pragmatics accuracy {accuracy:.2%} < 70%")

    return GateResult(
        ok=not errors,
        errors=errors,
        meta={
            "label": label,
            "rows": len(rows),
            "dialects": dict(dialects),
            "intents": dict(intents),
            "duplicates_raw": len(texts) - len(set(texts)),
            "accuracy": round(accuracy, 4),
        },
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def export_model_artifact() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    from pragmatics.pattern_library import PATTERN_LIBRARY

    artifact = {
        "name": "vpe2-pattern-prior",
        "version": "2.0.0",
        "dialects": list(DIALECTS),
        "patterns": PATTERN_LIBRARY,
        "context_radius_default": 5,
        "context_radius_max": 10,
    }
    (MODELS / "vpe2_pattern_prior.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_factory(
    *,
    utterances: int = 100_000,
    situations: int = 5_000,
    fake_yes: int = 1_000,
    soft_no: int = 1_000,
    topic_shift: int = 500,
    exit_imminent: int = 500,
) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    report_dir = OUT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    all_utt = expand_utterances(utterances)
    all_sit = expand_situations(situations)
    specials = {
        "fake_agreement": _filter_bucket(all_utt, {"fake_yes", "fake_or_soft_yes"}, fake_yes),
        "soft_refusal": _filter_bucket(all_utt, {"soft_no"}, soft_no),
        "topic_shift": _filter_bucket(all_utt, {"shift"}, topic_shift),
        "exit_imminent": _filter_bucket(all_utt, {"exit"}, exit_imminent),
    }

    gate_reports: list[dict[str, Any]] = []
    for batch_idx in range(0, len(all_utt), 500):
        chunk = all_utt[batch_idx : batch_idx + 500]
        gate = quality_gate(chunk, label=f"utterances_batch_{batch_idx // 500}")
        gate_reports.append({"ok": gate.ok, "errors": gate.errors, **gate.meta})
        if not gate.ok:
            raise RuntimeError(
                f"Quality gate failed at utterances batch {batch_idx // 500}: {gate.errors}"
            )

    for batch_idx in range(0, len(all_sit), 500):
        chunk = all_sit[batch_idx : batch_idx + 500]
        gate = quality_gate(chunk, label=f"situations_batch_{batch_idx // 500}")
        gate_reports.append({"ok": gate.ok, "errors": gate.errors, **gate.meta})
        if not gate.ok:
            raise RuntimeError(
                f"Quality gate failed at situations batch {batch_idx // 500}: {gate.errors}"
            )

    write_jsonl(OUT / "utterances" / "utterances_100k.jsonl", all_utt)
    write_jsonl(OUT / "situations" / "situations_5000.jsonl", all_sit)
    write_jsonl(OUT / "fake_agreement" / "fake_yes_1000.jsonl", specials["fake_agreement"])
    write_jsonl(OUT / "soft_refusal" / "soft_no_1000.jsonl", specials["soft_refusal"])
    write_jsonl(OUT / "topic_shift" / "topic_shift_500.jsonl", specials["topic_shift"])
    write_jsonl(OUT / "exit_imminent" / "exit_500.jsonl", specials["exit_imminent"])
    export_model_artifact()

    summary = {
        "utterances": len(all_utt),
        "situations": len(all_sit),
        "fake_agreement": len(specials["fake_agreement"]),
        "soft_refusal": len(specials["soft_refusal"]),
        "topic_shift": len(specials["topic_shift"]),
        "exit_imminent": len(specials["exit_imminent"]),
        "gates_passed": len(gate_reports),
        "all_gates_ok": all(g["ok"] for g in gate_reports),
        "avg_accuracy": round(
            sum(float(g.get("accuracy") or 0.0) for g in gate_reports) / max(len(gate_reports), 1),
            4,
        ),
    }
    (report_dir / "factory_summary.json").write_text(
        json.dumps({"summary": summary, "gates": gate_reports[-20:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="VPE 2.0 dataset factory")
    parser.add_argument("--utterances", type=int, default=100_000)
    parser.add_argument("--situations", type=int, default=5_000)
    parser.add_argument("--fake-yes", type=int, default=1_000)
    parser.add_argument("--soft-no", type=int, default=1_000)
    parser.add_argument("--topic-shift", type=int, default=500)
    parser.add_argument("--exit", type=int, default=500)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if args.quick:
        summary = run_factory(
            utterances=2_000,
            situations=1_000,
            fake_yes=200,
            soft_no=200,
            topic_shift=100,
            exit_imminent=100,
        )
    else:
        summary = run_factory(
            utterances=args.utterances,
            situations=args.situations,
            fake_yes=args.fake_yes,
            soft_no=args.soft_no,
            topic_shift=args.topic_shift,
            exit_imminent=args.exit,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
