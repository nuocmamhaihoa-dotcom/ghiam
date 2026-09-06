"""VPE 2.0 engine unit + corpus tests (>2000 cases)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pragmatics import VietnamesePragmaticsEngine
from pragmatics.types import INSUFFICIENT

FIXTURES = Path(__file__).parent / "fixtures_2100.jsonl"


def _load_fixtures() -> list[dict]:
    rows: list[dict] = []
    with FIXTURES.open(encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))
    return rows


FIXTURE_ROWS = _load_fixtures()


@pytest.fixture(scope="module")
def engine() -> VietnamesePragmaticsEngine:
    return VietnamesePragmaticsEngine(context_radius=5)


def test_fixture_corpus_size() -> None:
    assert len(FIXTURE_ROWS) >= 2000


def test_empty_transcript_insufficient(engine: VietnamesePragmaticsEngine) -> None:
    result = engine.analyze([])
    assert result.status == INSUFFICIENT


def test_context_radius_bounds() -> None:
    with pytest.raises(ValueError):
        VietnamesePragmaticsEngine(context_radius=2)
    with pytest.raises(ValueError):
        VietnamesePragmaticsEngine(context_radius=11)


def test_multi_intent_not_keyword_only(engine: VietnamesePragmaticsEngine) -> None:
    result = engine.analyze(
        [
            {"speaker": "agent", "text": "Em chào anh, bên em có gói ưu đãi"},
            {"speaker": "customer", "text": "Ừ cũng được"},
            {"speaker": "agent", "text": "Anh chốt luôn hôm nay được không ạ"},
            {"speaker": "customer", "text": "Để hỏi vợ đã"},
        ],
        dialect_hint="north",
    )
    assert result.status == "ok"
    assert len(result.timeline) >= 1
    customer = [t for t in result.turns if t.status == "ok"]
    assert customer
    for turn in customer:
        assert len(turn.intent_probability) >= 2
        total = sum(turn.intent_probability.values())
        assert 0.99 <= total <= 1.01
        assert turn.evidence
        assert turn.confidence > 0


def test_buying_signal_after_delay(engine: VietnamesePragmaticsEngine) -> None:
    result = engine.analyze(
        [
            {"speaker": "customer", "text": "Để em coi đã"},
            {"speaker": "agent", "text": "Dạ anh cần em giải thích thêm không"},
            {"speaker": "customer", "text": "Bao giờ giao được?"},
        ],
        dialect_hint="south",
    )
    assert result.status == "ok"
    last = [t for t in result.turns if t.status == "ok"][-1]
    assert last.buying_probability is not None
    assert last.buying_probability >= 0.3
    top = max(last.intent_probability, key=last.intent_probability.get)
    assert top in {"ready_to_buy", "need_information", "interested"}


def test_dialect_central_cues(engine: VietnamesePragmaticsEngine) -> None:
    result = engine.analyze(
        [{"speaker": "customer", "text": "Răng rồi"}],
        dialect_hint="central",
    )
    assert result.status == "ok"
    assert result.dialect == "central"


@pytest.mark.parametrize("row", FIXTURE_ROWS, ids=lambda r: r["id"])
def test_corpus_top3_intent(engine: VietnamesePragmaticsEngine, row: dict) -> None:
    result = engine.analyze(
        [
            {"speaker": "agent", "text": "Em tư vấn gói sản phẩm ạ"},
            {"speaker": "customer", "text": row["text"]},
        ],
        dialect_hint=row["dialect"],
    )
    assert result.status == "ok"
    customer = [t for t in result.turns if t.status == "ok"]
    assert customer, f"no match for {row['text']}"
    ranked = sorted(
        customer[-1].intent_probability,
        key=customer[-1].intent_probability.get,
        reverse=True,
    )
    assert row["expected_top_intent"] in ranked[:3]
    assert customer[-1].hidden_meaning
    assert customer[-1].evidence
    assert 0.0 <= (customer[-1].buying_probability or 0.0) <= 1.0
    assert 0.0 <= (customer[-1].exit_risk or 0.0) <= 1.0


def test_app_service_wrapper_compatible() -> None:
    from app.application.services.pragmatics import PragmaticsEngine

    service = PragmaticsEngine(context_radius=5)
    result = service.analyze_transcript(
        [
            {"speaker": "customer", "text": "Đắt quá"},
            {"speaker": "customer", "text": "Mai gọi lại nhé"},
        ]
    )
    payload = service.to_dict(result)
    assert payload["status"] == "ok"
    assert "timeline" in payload
    assert "intent_evolution" in payload
    assert "emotion_evolution" in payload
    assert isinstance(payload.get("intents"), list)
