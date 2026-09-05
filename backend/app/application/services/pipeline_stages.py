"""Immutable AI pipeline stages — Constitution §3.

Audio → Whisper → Diarization → Normalize → Segment → Evidence Extract →
Evidence Verify → Rule Engine → Judge Ensemble → Root Cause → Coaching →
Revenue Leak → Dashboard/JSON
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

INSUFFICIENT = "Insufficient Evidence"

PIPELINE_ORDER = (
    "audio",
    "whisper",
    "diarization",
    "normalize",
    "segment",
    "evidence_extract",
    "evidence_verify",
    "rule_engine",
    "judge_ensemble",
    "root_cause",
    "coaching",
    "revenue_leak",
    "dashboard_json",
)


@dataclass
class StageResult:
    name: str
    ok: bool
    artifact: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class PipelineContext:
    call_id: str
    audio_bytes: bytes | None = None
    audio_content_type: str | None = None
    audio_s3_key: str | None = None
    existing_transcript_turns: list[dict[str, Any]] = field(default_factory=list)
    existing_evidence: list[dict[str, Any]] = field(default_factory=list)
    industry: str | None = None
    dialect: str | None = None
    stages: list[StageResult] = field(default_factory=list)

    def record(self, result: StageResult) -> None:
        self.stages.append(result)


class WhisperPort(Protocol):
    def transcribe(
        self, audio_bytes: bytes, *, content_type: str | None = None
    ) -> dict[str, Any]: ...


class DiarizationPort(Protocol):
    def diarize(
        self, audio_bytes: bytes, *, transcript_segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


class LocalWhisperBridge:
    """Uses existing transcript turns when live Whisper is unavailable."""

    def transcribe(
        self, audio_bytes: bytes, *, content_type: str | None = None
    ) -> dict[str, Any]:
        raise RuntimeError(
            f"{INSUFFICIENT}: Whisper API not configured; provide transcript turns."
        )

    def transcribe_from_turns(self, turns: list[dict[str, Any]]) -> dict[str, Any]:
        if not turns:
            raise RuntimeError(f"{INSUFFICIENT}: empty transcript turns for Whisper bridge.")
        segments: list[dict[str, Any]] = []
        texts: list[str] = []
        confs: list[float] = []
        for i, turn in enumerate(turns):
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            conf = float(turn.get("confidence") or turn.get("avg_confidence") or 0.8)
            start = float(turn.get("start") or turn.get("ts_start") or i * 4.0)
            end = float(
                turn.get("end") or turn.get("ts_end") or start + max(1.5, len(text) / 12)
            )
            segments.append(
                {
                    "start": start,
                    "end": end,
                    "text": text,
                    "confidence": conf,
                    "speaker_hint": turn.get("speaker"),
                }
            )
            texts.append(text)
            confs.append(conf)
        if not segments:
            raise RuntimeError(f"{INSUFFICIENT}: no usable speech segments.")
        return {
            "text": " ".join(texts),
            "segments": segments,
            "avg_confidence": round(sum(confs) / len(confs), 3),
            "provider": "transcript_bridge",
        }


class OpenAIWhisperAdapter:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def transcribe(
        self, audio_bytes: bytes, *, content_type: str | None = None
    ) -> dict[str, Any]:
        if not audio_bytes:
            raise RuntimeError(f"{INSUFFICIENT}: empty audio payload for Whisper.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                f"{INSUFFICIENT}: openai package missing for Whisper adapter."
            ) from exc
        client = OpenAI(api_key=self._api_key)
        bio = io.BytesIO(audio_bytes)
        bio.name = "call.wav"
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=bio,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        segments: list[dict[str, Any]] = []
        confs: list[float] = []
        for seg in data.get("segments") or []:
            conf = float(seg.get("avg_logprob", -1.0))
            confidence = max(0.0, min(1.0, 1.0 + conf / 5.0))
            confs.append(confidence)
            segments.append(
                {
                    "start": float(seg.get("start") or 0.0),
                    "end": float(seg.get("end") or 0.0),
                    "text": str(seg.get("text") or "").strip(),
                    "confidence": round(confidence, 3),
                }
            )
        if not segments and data.get("text"):
            segments = [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": str(data["text"]).strip(),
                    "confidence": 0.75,
                }
            ]
            confs = [0.75]
        if not segments:
            raise RuntimeError(f"{INSUFFICIENT}: Whisper returned empty transcript.")
        return {
            "text": str(data.get("text") or " ".join(s["text"] for s in segments)),
            "segments": segments,
            "avg_confidence": round(sum(confs) / len(confs), 3) if confs else 0.0,
            "provider": "openai_whisper",
        }


class HeuristicDiarizer:
    def diarize(
        self, audio_bytes: bytes, *, transcript_segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not transcript_segments:
            raise RuntimeError(f"{INSUFFICIENT}: no transcript segments for diarization.")
        turns: list[dict[str, Any]] = []
        for i, seg in enumerate(transcript_segments):
            hint = str(seg.get("speaker_hint") or seg.get("speaker") or "").lower()
            if hint in {"agent", "nv", "sale", "seller"}:
                speaker = "agent"
            elif hint in {"customer", "khach", "client", "caller"}:
                speaker = "customer"
            else:
                speaker = "agent" if i % 2 == 0 else "customer"
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            turns.append(
                {
                    "speaker": speaker,
                    "start": float(seg.get("start") or 0.0),
                    "end": float(seg.get("end") or 0.0),
                    "text": text,
                    "confidence": float(seg.get("confidence") or 0.8),
                    "turn_index": len(turns),
                }
            )
        if not turns:
            raise RuntimeError(f"{INSUFFICIENT}: diarization produced no turns.")
        return turns


_FILLER = re.compile(r"\b(à+|ờ+|ừ+|ơ+|um+|uh+)\b", re.IGNORECASE)
_SPACE = re.compile(r"\s+")

_STAGE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("opening", ("alo", "em chào", "bên em", "em tên")),
    ("rapport", ("dạo này", "anh/chị đang", "em hỏi thăm")),
    ("discovery", ("anh/chị đang cần", "mục đích", "đang dùng", "nhu cầu")),
    ("qualification", ("ngân sách", "ai quyết định", "thời gian")),
    ("presentation", ("gói", "tính năng", "lợi ích", "giải pháp")),
    ("pricing", ("giá", "phí", "bao nhiêu", "ưu đãi")),
    ("objection", ("đắt", "để xem", "hỏi vợ", "bên kia", "không tin")),
    ("closing", ("chốt", "đăng ký", "đặt cọc", "kí")),
    ("follow_up", ("em gọi lại", "hẹn", "follow")),
]


def normalize_transcript(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for turn in turns:
        text = str(turn.get("text") or "")
        text = _FILLER.sub(" ", text)
        text = _SPACE.sub(" ", text).strip(" .,")
        if not text:
            continue
        row = dict(turn)
        row["text"] = text
        row["normalized"] = True
        normalized.append(row)
    if not normalized:
        raise RuntimeError(f"{INSUFFICIENT}: normalization removed all turns.")
    return normalized


def semantic_segment(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_stage = "opening"
    for turn in turns:
        text = str(turn.get("text") or "").lower()
        for stage, keys in _STAGE_KEYWORDS:
            if any(k in text for k in keys):
                current_stage = stage
                break
        row = dict(turn)
        row["stage"] = current_stage
        segments.append(row)
    if not segments:
        raise RuntimeError(f"{INSUFFICIENT}: semantic segmentation empty.")
    return segments


def extract_evidence(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for turn in segments:
        text = str(turn.get("text") or "").strip()
        if len(text) < 4:
            continue
        span_id = hashlib.sha1(
            f"{turn.get('turn_index')}|{turn.get('start')}|{text}".encode("utf-8")
        ).hexdigest()[:16]
        evidence.append(
            {
                "span_id": span_id,
                "quote": text,
                "speaker": turn.get("speaker"),
                "stage_key": turn.get("stage"),
                "confidence": float(turn.get("confidence") or 0.8),
                "audio_ts_start": turn.get("start"),
                "audio_ts_end": turn.get("end"),
                "turn_index": turn.get("turn_index"),
                "slot": "utterance",
            }
        )
    if not evidence:
        raise RuntimeError(f"{INSUFFICIENT}: no evidence spans extracted.")
    return evidence


def verify_evidence(
    evidence: list[dict[str, Any]],
    *,
    min_confidence: float = 0.65,
    require_timestamp: bool = True,
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for span in evidence:
        conf = float(span.get("confidence") or 0.0)
        if conf < min_confidence:
            continue
        if require_timestamp and span.get("audio_ts_start") is None:
            continue
        if not str(span.get("quote") or "").strip():
            continue
        row = dict(span)
        row["verified"] = True
        verified.append(row)
    if not verified:
        raise RuntimeError(f"{INSUFFICIENT}: no verified evidence spans.")
    return verified
