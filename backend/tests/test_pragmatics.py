"""Tests for Vietnamese Pragmatics Engine."""

from __future__ import annotations

from app.application.services.pragmatics import PragmaticsEngine, _load_library


def test_price_and_buying_signal_resolution() -> None:
    _load_library.cache_clear()
    engine = PragmaticsEngine()
    result = engine.analyze_transcript(
        [
            {"speaker": "agent", "text": "Em chào chị"},
            {"speaker": "customer", "text": "Giá đắt quá"},
            {"speaker": "customer", "text": "Để em xem đã"},
            {"speaker": "customer", "text": "Bao giờ giao được?"},
        ]
    )
    payload = engine.to_dict(result)
    assert payload["status"] == "ok"
    assert payload["summary"]["matched_turns"] >= 2
    assert any(t["matched_pattern_id"] for t in payload["turns"] if t["status"] == "ok")
    buying_turn = next(
        t for t in payload["turns"] if t.get("matched_pattern_id") == "PRAG-BUYING-SIGNAL"
    )
    assert buying_turn["buying_probability"] is not None
    assert buying_turn["buying_probability"] >= 0.5
    assert buying_turn["evidence_quote"]


def test_insufficient_evidence_when_no_match() -> None:
    _load_library.cache_clear()
    engine = PragmaticsEngine()
    result = engine.analyze_transcript(
        [{"speaker": "customer", "text": "Trời hôm nay đẹp quá"}]
    )
    assert result.status == "Insufficient Evidence"
    assert result.timeline == []


def test_empty_transcript_insufficient() -> None:
    engine = PragmaticsEngine()
    result = engine.analyze_transcript([])
    assert result.status == "Insufficient Evidence"
