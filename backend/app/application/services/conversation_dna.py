"""Conversation DNA + emotion timeline builders."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    CallModel,
    ConversationDnaModel,
    EvidenceModel,
    ScoreModel,
    TranscriptModel,
)

DNA_DIMENSIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("rapport", "Xây dựng quan hệ", ("opening", "rapport")),
    ("discovery", "Khai thác nhu cầu", ("discovery", "needs")),
    ("value", "Truyền giá trị", ("pitch", "presentation")),
    ("objection", "Xử lý từ chối", ("objection", "pricing")),
    ("close", "Chốt đơn", ("closing", "close")),
    ("compliance", "Tuân thủ", ("compliance",)),
    ("emotion", "Kiểm soát cảm xúc", ("voice", "emotion")),
    ("clarity", "Rõ ràng thông điệp", ("presentation", "opening")),
]


class ConversationDnaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _score_dimension(self, stage_scores: dict[str, Any], keys: tuple[str, ...]) -> float:
        values: list[float] = []
        for key in keys:
            raw = stage_scores.get(key)
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not values:
            return 50.0
        return round(sum(values) / len(values), 2)

    def _emotion_timeline(
        self,
        evidence: list[EvidenceModel],
        transcript: TranscriptModel | None,
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        duration = 1.0
        if transcript and transcript.turns:
            last = transcript.turns[-1]
            duration = max(float(last.get("end") or last.get("ts_end") or len(transcript.turns) * 5), 1.0)
        elif evidence:
            ends = [float(item.audio_ts_end or item.audio_ts_start or 0) for item in evidence]
            duration = max(max(ends), 1.0)

        if not evidence and transcript and transcript.turns:
            for idx, turn in enumerate(transcript.turns[:40]):
                t_sec = float(turn.get("start") or turn.get("ts_start") or idx * 5)
                speaker = str(turn.get("speaker") or "agent")
                text = str(turn.get("text") or "").lower()
                valence = 0.2
                label = "neutral"
                if any(token in text for token in ("cảm ơn", "được", "ok", "đồng ý", "vui")):
                    valence, label = 0.6, "positive"
                elif any(token in text for token in ("không", "đắt", "phàn nàn", "hủy", "bực")):
                    valence, label = -0.5, "negative"
                points.append(
                    {
                        "t_sec": t_sec,
                        "progress_pct": round(min(100.0, (t_sec / duration) * 100), 2),
                        "valence": valence,
                        "arousal": 0.4,
                        "label": label,
                        "speaker": "customer" if speaker.startswith("cust") else "agent",
                    }
                )
            return points

        for item in evidence[:40]:
            t_sec = float(item.audio_ts_start or 0)
            joined = " ".join(item.labels or []).lower() + " " + (item.quote or "").lower()
            valence = 0.1
            label = "neutral"
            if any(token in joined for token in ("positive", "buy", "đồng ý", "cảm ơn")):
                valence, label = 0.65, "positive"
            elif any(token in joined for token in ("negative", "objection", "không", "đắt")):
                valence, label = -0.55, "negative"
            elif any(token in joined for token in ("anger", "frustrat")):
                valence, label = -0.8, "frustrated"
            points.append(
                {
                    "t_sec": t_sec,
                    "progress_pct": round(min(100.0, (t_sec / duration) * 100), 2),
                    "valence": valence,
                    "arousal": 0.45,
                    "label": label,
                    "speaker": "customer" if (item.speaker or "").startswith("cust") else "agent",
                }
            )
        return points

    async def build_for_call(self, call_id: UUID) -> dict[str, Any]:
        score = (
            await self._session.execute(
                select(ScoreModel)
                .where(ScoreModel.call_id == call_id)
                .order_by(ScoreModel.evaluated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        stage_scores = dict(score.stage_scores or {}) if score else {}
        evidence = list(
            (await self._session.execute(select(EvidenceModel).where(EvidenceModel.call_id == call_id))).scalars()
        )
        transcript = (
            await self._session.execute(
                select(TranscriptModel)
                .where(TranscriptModel.call_id == call_id)
                .order_by(TranscriptModel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        dimensions = [
            {"key": key, "label": label, "score": self._score_dimension(stage_scores, keys)}
            for key, label, keys in DNA_DIMENSIONS
        ]
        weak = sorted(dimensions, key=lambda row: row["score"])[:2]
        summary = (
            "Insufficient Evidence: chưa đủ stage scores để dựng DNA đầy đủ."
            if not stage_scores
            else "Điểm mạnh/yếu theo giai đoạn. Cần cải thiện: "
            + ", ".join(f"{item['label']} ({item['score']})" for item in weak)
        )
        emotion_timeline = self._emotion_timeline(evidence, transcript)
        payload = {
            "dimensions": dimensions,
            "summary": summary,
            "emotion_timeline": emotion_timeline,
            "call_id": str(call_id),
            "score": float(score.overall_score) if score else None,
        }
        existing = (
            await self._session.execute(
                select(ConversationDnaModel).where(ConversationDnaModel.call_id == call_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ConversationDnaModel(
                id=uuid4(),
                call_id=call_id,
                dimensions=dimensions,
                summary=summary,
                emotion_timeline=emotion_timeline,
                payload=payload,
            )
            self._session.add(existing)
        else:
            existing.dimensions = dimensions
            existing.summary = summary
            existing.emotion_timeline = emotion_timeline
            existing.payload = payload
        await self._session.flush()
        return {
            "dimensions": dimensions,
            "summary": summary,
            "emotion_timeline": emotion_timeline,
            "call_id": str(call_id),
        }

    async def get_or_build(self, call_id: UUID | None = None) -> dict[str, Any]:
        if call_id is None:
            call = (
                await self._session.execute(
                    select(CallModel).order_by(CallModel.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if call is None:
                return {
                    "dimensions": [{"key": key, "label": label, "score": 0.0} for key, label, _ in DNA_DIMENSIONS],
                    "summary": "Insufficient Evidence: no calls available for DNA.",
                    "emotion_timeline": [],
                    "call_id": None,
                }
            call_id = call.id
        existing = (
            await self._session.execute(
                select(ConversationDnaModel).where(ConversationDnaModel.call_id == call_id)
            )
        ).scalar_one_or_none()
        if existing:
            return {
                "dimensions": existing.dimensions,
                "summary": existing.summary,
                "emotion_timeline": existing.emotion_timeline,
                "call_id": str(call_id),
            }
        return await self.build_for_call(call_id)
