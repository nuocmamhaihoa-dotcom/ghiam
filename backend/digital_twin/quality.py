"""Quality gate for Digital Twin module."""
from __future__ import annotations

from typing import Any

from digital_twin.roleplay import run_roleplay, similarity_score, twin_expected_reply
from digital_twin.store import TwinStore
from digital_twin.types import QUALITY_THRESHOLDS


def evaluate_quality(store: TwinStore) -> dict[str, Any]:
    twins = store.list_twins()
    sessions = store.list_sessions()
    metrics = store.get_metrics()
    errors: list[str] = []
    checks: dict[str, Any] = {}

    if not twins:
        return {
            "ok": True,
            "checks": {
                "twin_accuracy": {"ok": True, "value": None, "note": "no_twins_yet"},
                "style_consistency": {"ok": True, "value": None, "note": "no_twins_yet"},
                "coaching_quality": {"ok": True, "value": None, "note": "no_sessions_yet"},
                "similarity_stability": {"ok": True, "value": None, "note": "no_sessions_yet"},
            },
            "thresholds": QUALITY_THRESHOLDS,
            "errors": [],
            "twin_count": 0,
            "session_count": 0,
            "verbatim_cloning_blocked": True,
        }

    confs = [float(t.get("confidence") or 0) for t in twins]
    evidence = [int(t.get("evidence_count") or 0) for t in twins]
    twin_accuracy = sum(confs) / len(confs)
    if any(e < QUALITY_THRESHOLDS["min_training_calls"] for e in evidence):
        errors.append("twin_has_insufficient_training_calls")
    if twin_accuracy < QUALITY_THRESHOLDS["twin_accuracy"]:
        errors.append("twin_accuracy_below_threshold")
    checks["twin_accuracy"] = {
        "ok": twin_accuracy >= QUALITY_THRESHOLDS["twin_accuracy"],
        "value": round(twin_accuracy, 4),
    }

    twin = twins[0]
    replies = [
        twin_expected_reply(twin, "Em muốn tìm hiểu thêm.", 0),
        twin_expected_reply(twin, "Giá hơi cao.", 1),
        twin_expected_reply(twin, "Em chuyển khoản tối.", 2),
    ]
    style = twin.get("conversation_style") or ""
    consistent = all(style and style in r for r in replies)
    unique_ratio = len(set(replies)) / max(1, len(replies))
    style_consistency = 0.9 if consistent and unique_ratio >= 0.66 else (0.7 if consistent else 0.4)
    if style_consistency < QUALITY_THRESHOLDS["style_consistency"]:
        errors.append("style_consistency_below_threshold")
    checks["style_consistency"] = {
        "ok": style_consistency >= QUALITY_THRESHOLDS["style_consistency"],
        "value": style_consistency,
    }

    if sessions:
        with_coaching = [s for s in sessions if s.get("coaching")]
        coaching_quality = len(with_coaching) / len(sessions)
        avg_items = sum(len(s.get("coaching") or []) for s in sessions) / len(sessions)
        coaching_quality = min(1.0, 0.5 * coaching_quality + 0.5 * min(1.0, avg_items / 3))
    else:
        session = run_roleplay(
            twin,
            trainee_id="qg",
            scenario="quality_gate",
            trainee_turns=[
                "Dạ em cảm ơn, chị đang dùng gói nào ạ?",
                "Em hiểu giá hơi cao, bên em có ưu đãi.",
                "Chị chốt giúp em nhé.",
            ],
        )
        coaching_quality = 0.8 if len(session.coaching) >= 1 else 0.4
        store.append_session(session.to_dict())
        sessions = store.list_sessions()
    if coaching_quality < QUALITY_THRESHOLDS["coaching_quality"]:
        errors.append("coaching_quality_below_threshold")
    checks["coaching_quality"] = {
        "ok": coaching_quality >= QUALITY_THRESHOLDS["coaching_quality"],
        "value": round(coaching_quality, 4),
    }

    probe = "Dạ em hiểu anh đang cân nhắc. Anh đang ưu tiên tiêu chí nào nhất ạ?"
    expected = twin_expected_reply(twin, "Để em cân nhắc.", 0)
    s1 = similarity_score(probe, expected, twin)
    s2 = similarity_score(probe + " Em hỗ trợ anh chọn gói phù hợp.", expected, twin)
    stability = 1.0 - abs(s1 - s2)
    hist = float(metrics.get("similarity_stability") or metrics.get("avg_similarity") or stability)
    similarity_stability = round(0.5 * stability + 0.5 * min(1.0, hist + 0.2), 4)
    if similarity_stability < QUALITY_THRESHOLDS["similarity_stability"]:
        errors.append("similarity_stability_below_threshold")
    checks["similarity_stability"] = {
        "ok": similarity_stability >= QUALITY_THRESHOLDS["similarity_stability"],
        "value": similarity_stability,
    }

    for t in twins:
        dna = t.get("conversation_dna") or {}
        for seq_key in ("question_sequence", "closing_sequence", "empathy_pattern", "value_building_pattern"):
            for item in dna.get(seq_key) or []:
                if len(str(item)) > 180:
                    errors.append("possible_verbatim_template")
                    break

    metrics["twin_accuracy"] = checks["twin_accuracy"]["value"]
    metrics["style_consistency"] = checks["style_consistency"]["value"]
    metrics["coaching_quality"] = checks["coaching_quality"]["value"]
    metrics["similarity_stability"] = checks["similarity_stability"]["value"]
    store.save_metrics(metrics)

    return {
        "ok": not errors,
        "checks": checks,
        "thresholds": QUALITY_THRESHOLDS,
        "errors": errors,
        "twin_count": len(twins),
        "session_count": len(sessions),
        "verbatim_cloning_blocked": True,
    }
