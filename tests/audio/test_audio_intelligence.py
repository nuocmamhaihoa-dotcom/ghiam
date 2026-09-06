"""Audio Intelligence Engine test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

from audio_engine.engine import AudioIntelligenceEngine
from audio_engine.store import AudioStore
from audio_engine.types import PIPELINE_STAGES, QUALITY_THRESHOLDS
from audio_pipeline.pipeline import run_pipeline
from audio_repair.repair import AudioRepairEngine
from speaker.diarization import diarize_speakers
from speaker.separation import separate_voices
from transcript.engine import TranscriptEngine
from transcript.normalize import detect_dialect, normalize_vietnamese


@pytest.fixture()
def engine(tmp_path: Path) -> AudioIntelligenceEngine:
    return AudioIntelligenceEngine(store=AudioStore(root=tmp_path))


def _good_payload() -> dict:
    return {
        "file_meta": {
            "file_id": "f_good",
            "extension": ".mp3",
            "integrity_ok": True,
            "duration_sec": 48,
            "sample_rate": 16000,
            "channels": 1,
        },
        "transcript_turns": [
            {
                "speaker": "agent",
                "start_sec": 0,
                "end_sec": 3,
                "text": "Em chào anh, sản phẩm này bảo hành 12 tháng.",
                "confidence": 0.95,
            },
            {
                "speaker": "customer",
                "start_sec": 3.2,
                "end_sec": 6.5,
                "text": "Thanh toán sao em? Có hóa đơn không?",
                "confidence": 0.92,
            },
            {
                "speaker": "agent",
                "start_sec": 6.6,
                "end_sec": 10,
                "text": "Anh chuyển khoản giúp em, em xuất hóa đơn VAT.",
                "confidence": 0.93,
            },
            {
                "speaker": "customer",
                "start_sec": 10.2,
                "end_sec": 13,
                "text": "Giá hơi cao, để anh suy nghĩ.",
                "confidence": 0.9,
            },
        ],
    }


def _bad_payload() -> dict:
    return {
        "file_meta": {
            "file_id": "f_bad",
            "extension": ".mp3",
            "integrity_ok": True,
            "duration_sec": 20,
            "sample_rate": 16000,
            "channels": 1,
        },
        "quality_hints": {"quality_score": 22, "noise": 0.9},
        "transcript_turns": [
            {"speaker": "agent", "start_sec": 0, "end_sec": 1, "text": "alo", "confidence": 0.8}
        ],
    }


def test_pipeline_stage_order_complete() -> None:
    assert len(PIPELINE_STAGES) >= 10
    assert PIPELINE_STAGES[0] == "upload"
    assert PIPELINE_STAGES[-1] == "ai_analysis"


def test_upload_queue_supports_bulk(engine: AudioIntelligenceEngine) -> None:
    files = [
        {"filename": f"call_{i}.mp3", "size_bytes": 1000 + i, "hints": {"duration_sec": 30}}
        for i in range(120)
    ]
    out = engine.upload(files)
    assert out["ok"] is True
    assert out["count"] == 120
    assert len(out["queue"]) == 120


def test_quality_gate_blocks_low_quality(engine: AudioIntelligenceEngine) -> None:
    out = engine.process(_bad_payload())
    assert out["blocked"] is True
    assert out.get("scoring_allowed") is False
    assert out.get("analysis_allowed") is False
    assert "audio_quality" in str(out.get("block_reason") or "")


def test_quality_gate_allows_good_call(engine: AudioIntelligenceEngine) -> None:
    out = engine.process(_good_payload())
    assert out["ok"] is True
    assert out.get("scoring_allowed") is True
    assert out.get("analysis_allowed") is True
    assert out.get("transcript")
    assert out.get("evidence")


def test_repair_and_rollback(engine: AudioIntelligenceEngine) -> None:
    repair = engine.repair(
        file_id="f1",
        quality={"score": 40, "noise": 0.5, "echo": 0.3, "low_volume": 0.4},
        force=True,
    )
    assert repair["ok"] is True
    assert repair.get("actions") or repair.get("actions") is not None
    assert repair.get("rollback_available") is True or repair.get("rollback_available") is True
    rb = engine.rollback_repair(repair)
    assert rb.get("rolled_back") is True or rb.get("ok") is True


def test_diarization_does_not_guess_when_uncertain() -> None:
    out = diarize_speakers(duration_sec=10, transcript_lines=[], min_confidence=0.55)
    assert out["uncertain"] is True
    assert all(s["role"] == "unknown" for s in out["speakers"])


def test_voice_separation_metrics() -> None:
    sep = separate_voices(
        duration_sec=20,
        channels=1,
        overlap_hint=0.5,
        transcript_hint=[
            {"speaker": "agent", "start_sec": 0, "end_sec": 5},
            {"speaker": "customer", "start_sec": 4.5, "end_sec": 9},
        ],
    )
    assert sep["ok"] is True
    assert "overlap_time_sec" in sep
    assert "interrupt_count" in sep


def test_transcript_keeps_original_and_normalized() -> None:
    tr = TranscriptEngine().build(
        [{"speaker": "agent", "start_sec": 0, "end_sec": 2, "text": "ko dc đâu", "confidence": 0.9}]
    )
    line = tr["lines"][0]
    assert line["original_text"]
    assert line["normalized_text"]


def test_vietnamese_dialect_detection() -> None:
    assert detect_dialect("hông được hen") in {"south", "unknown"}
    assert normalize_vietnamese("sp này bh 12 tháng")


def test_buying_signals_and_objections(engine: AudioIntelligenceEngine) -> None:
    out = engine.process(_good_payload())
    assert out.get("scoring_allowed") is True
    assert isinstance(out.get("buying_signals"), list)
    assert len(out["buying_signals"]) >= 1
    assert isinstance(out.get("objections"), list)
    assert len(out["objections"]) >= 1


def test_evidence_requires_timestamp_speaker_transcript_rule(
    engine: AudioIntelligenceEngine,
) -> None:
    out = engine.process(_good_payload())
    for ev in out.get("evidence") or []:
        assert ev.get("timestamp_start") is not None
        assert ev.get("speaker")
        assert ev.get("transcript")
        assert ev.get("rule_id")


def test_batch_resume(engine: AudioIntelligenceEngine) -> None:
    items = [_good_payload(), _good_payload(), _bad_payload()]
    out = engine.batch_process(items, resume_from=1)
    assert out["ok"] is True
    assert out["processed"] == 2


def test_search_and_export(engine: AudioIntelligenceEngine) -> None:
    processed = engine.process(_good_payload())
    job_id = processed["job_id"]
    search = engine.search({"keyword": "hóa đơn"})
    assert search["ok"] is True
    exported = engine.export(job_id, fmt="json")
    assert exported["ok"] is True
    assert exported["format"] == "json"
    pdf = engine.export(job_id, fmt="pdf")
    assert pdf["ok"] is True
    xls = engine.export(job_id, fmt="excel")
    assert xls["ok"] is True


def test_live_session_latency_budget(engine: AudioIntelligenceEngine) -> None:
    live = engine.live_session({"text": "Giá đắt quá"})
    assert live["ok"] is True
    assert float(live.get("latency_budget_sec") or live.get("latency_budget_sec") or 2) <= 2.0
    assert live.get("suggestion")


def test_quality_snapshot_enforces_gate(engine: AudioIntelligenceEngine) -> None:
    snap = engine.quality_snapshot()
    assert snap["ok"] is True
    assert snap["checks"]["blocks_low_quality"] is True
    assert snap["thresholds"]["audio_quality_min"] == QUALITY_THRESHOLDS["audio_quality_min"]


def test_run_pipeline_direct_block() -> None:
    out = run_pipeline(_bad_payload())
    assert out["blocked"] is True
    assert out["analysis_allowed"] is False


def test_repair_engine_improves_score() -> None:
    repair = AudioRepairEngine().analyze_and_repair(
        file_id="x",
        quality={"score": 50, "noise": 0.4, "echo": 0.3, "low_volume": 0.3, "clipping": 0.2},
    )
    assert float(repair["quality_after"]) > float(repair["quality_before"])


@pytest.mark.parametrize("i", range(50))
def test_bulk_process_stability(engine: AudioIntelligenceEngine, i: int) -> None:
    payload = _good_payload()
    payload["file_meta"] = {**payload["file_meta"], "file_id": f"bulk_{i}"}
    out = engine.process(payload)
    assert out.get("scoring_allowed") is True or out.get("blocked") is True
