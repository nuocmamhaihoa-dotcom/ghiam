"""Digital Twin engine — train, roleplay, dashboard, quality."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from digital_twin.roleplay import run_roleplay, similarity_score, twin_expected_reply
from digital_twin.store import TwinStore
from digital_twin.trainer import (
    build_conversation_dna,
    build_skill_profile,
    build_voice_pattern,
    extract_style_signals,
    filter_training_calls,
)
from digital_twin.types import (
    QUALITY_THRESHOLDS,
    DigitalTwinProfile,
    TwinStatus,
    new_id,
    now_iso,
)


class DigitalTwinEngine:
    """Create AI twins of top salespeople without verbatim cloning."""

    def __init__(self, store: TwinStore | None = None) -> None:
        self.store = store or TwinStore()

    def train_twin(
        self,
        *,
        agent_id: str,
        display_name: str,
        calls: list[dict[str, Any]],
        activate: bool = False,
    ) -> dict[str, Any]:
        accepted, rejected = filter_training_calls(calls)
        min_calls = int(QUALITY_THRESHOLDS["min_training_calls"])
        if len(accepted) < min_calls:
            return {
                "ok": False,
                "error": "insufficient_eligible_calls",
                "accepted_calls": len(accepted),
                "rejected_calls": len(rejected),
                "min_required": min_calls,
                "note": "Only golden / QA-approved / high-conversion calls are allowed.",
            }

        signals = [extract_style_signals(c) for c in accepted]
        skills = build_skill_profile(signals)
        voice = build_voice_pattern(signals)
        dna = build_conversation_dna(signals)

        avg_score = sum(float(c.get("qa_score") or c.get("score") or 75) for c in accepted) / len(accepted)
        confidence = round(min(0.97, 0.45 + 0.04 * len(accepted) + (avg_score / 100) * 0.35), 4)

        twin = DigitalTwinProfile(
            twin_id=new_id("twin"),
            agent_id=agent_id,
            display_name=display_name,
            skill_profile=skills,
            strength_score=skills.strength_score(),
            weakness_score=skills.weakness_score(),
            voice_pattern=voice,
            conversation_dna=dna,
            confidence=confidence,
            status=(
                TwinStatus.ACTIVE
                if activate and confidence >= QUALITY_THRESHOLDS["min_confidence"]
                else TwinStatus.TRAINED
            ),
            training_call_ids=[str(c.get("call_id") or c.get("id") or "") for c in accepted],
            evidence_count=len(accepted),
            conversation_style=dna.conversation_style,
            discovery_style=dna.discovery_style,
            closing_style=dna.closing_style,
            objection_strategy=dna.objection_strategy,
            metadata={
                "rejected_calls": len(rejected),
                "verbatim_cloning": False,
                "source_labels": sorted({c.get("_eligibility") for c in accepted}),
            },
        )
        payload = twin.to_dict()
        self.store.save_twin(payload)
        for c in accepted:
            self.store.append_training_index(
                {
                    "twin_id": twin.twin_id,
                    "call_id": c.get("call_id") or c.get("id"),
                    "eligibility": c.get("_eligibility"),
                    "trained_at": now_iso(),
                }
            )
        metrics = self.store.get_metrics()
        metrics["twins_trained"] = int(metrics.get("twins_trained") or 0) + 1
        self.store.save_metrics(metrics)
        return {
            "ok": True,
            "twin": payload,
            "accepted_calls": len(accepted),
            "rejected_calls": len(rejected),
            "verbatim_cloning": False,
        }

    def list_twins(self) -> list[dict[str, Any]]:
        return self.store.list_twins()

    def get_twin(self, twin_id: str) -> dict[str, Any] | None:
        return self.store.get_twin(twin_id)

    def act_as_twin(self, twin_id: str, customer_text: str, step: int = 0) -> dict[str, Any]:
        twin = self.store.get_twin(twin_id)
        if not twin:
            return {"ok": False, "error": "twin_not_found"}
        reply = twin_expected_reply(twin, customer_text, step=step)
        return {
            "ok": True,
            "twin_id": twin_id,
            "reply": reply,
            "style": twin.get("conversation_style"),
            "verbatim_cloning": False,
        }

    def roleplay(
        self,
        twin_id: str,
        *,
        trainee_id: str,
        scenario: str,
        trainee_turns: list[str],
        customer_turns: list[str] | None = None,
    ) -> dict[str, Any]:
        twin = self.store.get_twin(twin_id)
        if not twin:
            return {"ok": False, "error": "twin_not_found"}
        prev = None
        prior = self.store.list_sessions(twin_id)
        trainee_prior = [s for s in prior if s.get("trainee_id") == trainee_id]
        if trainee_prior:
            prev = float(trainee_prior[-1].get("similarity_score") or 0)
        session = run_roleplay(
            twin,
            trainee_id=trainee_id,
            scenario=scenario,
            trainee_turns=trainee_turns,
            customer_turns=customer_turns,
            prev_similarity=prev,
        )
        payload = session.to_dict()
        self.store.append_session(payload)
        metrics = self.store.get_metrics()
        n = int(metrics.get("roleplay_sessions") or 0)
        metrics["roleplay_sessions"] = n + 1
        metrics["avg_similarity"] = round(
            ((float(metrics.get("avg_similarity") or 0) * n) + session.similarity_score) / (n + 1),
            4,
        )
        metrics["avg_improvement"] = round(
            ((float(metrics.get("avg_improvement") or 0) * n) + session.improvement_score) / (n + 1),
            4,
        )
        self.store.save_metrics(metrics)
        return {"ok": True, "session": payload}

    def score_similarity(self, twin_id: str, trainee_text: str, customer_text: str = "") -> dict[str, Any]:
        twin = self.store.get_twin(twin_id)
        if not twin:
            return {"ok": False, "error": "twin_not_found"}
        expected = twin_expected_reply(twin, customer_text or "Em đang tìm hiểu thêm.", step=0)
        score = similarity_score(trainee_text, expected, twin)
        return {
            "ok": True,
            "similarity_score": score,
            "twin_reference": expected,
            "verbatim_cloning": False,
        }

    def dashboard(self) -> dict[str, Any]:
        twins = self.store.list_twins()
        sessions = self.store.list_sessions()
        metrics = self.store.get_metrics()
        avg_sim = metrics.get("avg_similarity") or 0
        gaps: dict[str, float] = {}
        for s in sessions[-50:]:
            for k, v in (s.get("skill_gap") or {}).items():
                gaps[k] = gaps.get(k, 0.0) + float(v)
        if gaps:
            for k in list(gaps):
                gaps[k] = round(gaps[k] / max(1, min(50, len(sessions))), 4)
        top_diff = sorted(gaps.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "widgets": {
                "twin_count": len(twins),
                "avg_similarity": avg_sim,
                "avg_improvement": metrics.get("avg_improvement") or 0,
                "roleplay_sessions": metrics.get("roleplay_sessions") or 0,
                "skill_gap_index": round(sum(gaps.values()) / max(1, len(gaps)), 4) if gaps else 0.0,
                "progress": round(float(metrics.get("avg_improvement") or 0), 4),
            },
            "top_differences": [{"skill": k, "gap": v} for k, v in top_diff],
            "twins": [
                {
                    "twin_id": t.get("twin_id"),
                    "display_name": t.get("display_name"),
                    "confidence": t.get("confidence"),
                    "strength_score": t.get("strength_score"),
                    "weakness_score": t.get("weakness_score"),
                    "status": t.get("status"),
                }
                for t in twins
            ],
            "metrics": metrics,
            "verbatim_cloning_blocked": True,
        }

    def quality_snapshot(self) -> dict[str, Any]:
        from digital_twin.quality import evaluate_quality

        return evaluate_quality(self.store)


_engine: DigitalTwinEngine | None = None


def get_digital_twin_engine(root: Path | None = None) -> DigitalTwinEngine:
    global _engine
    if root is not None:
        return DigitalTwinEngine(store=TwinStore(root=root))
    if _engine is None:
        _engine = DigitalTwinEngine()
    return _engine
