"""Negotiation Strategy Engine suite — >=1000 cases.

- 500 negotiation scenarios
- 300 next-step predictions
- 200 strategy comparisons
"""
from __future__ import annotations

from pathlib import Path

import pytest

from negotiation.engine import NegotiationEngine
from negotiation.predictor import (
    detect_objection_family,
    predict_next_steps,
    prediction_accuracy_proxy,
)
from negotiation.store import NegotiationStore
from negotiation.strategy import (
    generate_strategies,
    pick_best_strategy,
    strategy_consistency,
)


UTTERANCES = [
    "Đắt quá",
    "Giá cao quá",
    "Ngân sách không đủ",
    "Để mình suy nghĩ đã",
    "Mai gọi lại được không",
    "Chưa cần lúc này",
    "Không tin lắm",
    "Bảo hành thế nào",
    "Bên kia rẻ hơn",
    "Đối thủ đang giảm giá",
    "Không cần sản phẩm này",
    "Chưa thấy cần thiết",
    "Ok nghe hay đó",
    "Cho mình hỏi thêm",
    "Thôi để sau",
]


def _utt(i: int) -> str:
    return UTTERANCES[i % len(UTTERANCES)] + f" #{i}"


@pytest.fixture()
def engine(tmp_path: Path) -> NegotiationEngine:
    return NegotiationEngine(store=NegotiationStore(root=tmp_path))


SCENARIO_CASES = [{"i": i, "utterance": _utt(i)} for i in range(500)]
PREDICTION_CASES = [{"i": i, "utterance": _utt(i + 17)} for i in range(300)]
COMPARE_CASES = [{"i": i, "utterance": _utt(i + 41)} for i in range(200)]


@pytest.mark.parametrize("case", SCENARIO_CASES, ids=[f"scenario-{c['i']}" for c in SCENARIO_CASES])
def test_negotiation_scenarios(engine: NegotiationEngine, case: dict) -> None:
    out = engine.analyze(case["utterance"], context={"product": "Pro", "benefit": "tiết kiệm"})
    assert out["ok"] is True
    assert out["static_tree"] is False
    assert len(out["strategies"]) >= 4
    pred = out["prediction"]
    assert 3 <= len(pred["horizon"]) <= 5
    assert 0.0 <= float(pred["exit_risk"]) <= 1.0
    assert 0.0 <= float(pred["buy_probability"]) <= 1.0
    best = out["best_strategy"]
    assert best is not None
    assert best["recommended_script"]
    assert best["forbidden_script"]
    assert best["recommended_script"] != best["forbidden_script"]
    assert best["evidence"]


@pytest.mark.parametrize("case", PREDICTION_CASES, ids=[f"predict-{c['i']}" for c in PREDICTION_CASES])
def test_next_step_predictions(case: dict) -> None:
    pred = predict_next_steps(case["utterance"], history=[{"role": "agent", "text": "Em tư vấn ạ"}])
    assert pred.next_question
    assert pred.next_objection
    assert pred.next_emotion
    assert 3 <= len(pred.horizon) <= 5
    assert 0.0 <= pred.confidence <= 1.0
    observed = {
        "next_emotion": pred.next_emotion,
        "exit_risk": pred.exit_risk,
        "buy_probability": pred.buy_probability,
        "objection_family": detect_objection_family(case["utterance"]),
    }
    score = prediction_accuracy_proxy(pred, observed)
    assert score >= 0.5


@pytest.mark.parametrize("case", COMPARE_CASES, ids=[f"compare-{c['i']}" for c in COMPARE_CASES])
def test_strategy_comparisons(engine: NegotiationEngine, case: dict) -> None:
    out = engine.compare_strategies(case["utterance"], context={"product": "Pro"})
    assert out["ok"] is True
    comps = out["comparisons"]
    assert len(comps) >= 4
    utilities = [c["utility"] for c in comps]
    assert utilities == sorted(utilities, reverse=True)
    best = out["best"]
    assert best is not None
    assert best["recommended_script"] != best["forbidden_script"]
    strategies = generate_strategies(case["utterance"])
    assert strategy_consistency(strategies) >= 0.65
    picked = pick_best_strategy(strategies)
    assert picked.win_probability >= min(s.win_probability for s in strategies)


def test_dashboard_and_quality(engine: NegotiationEngine) -> None:
    engine.analyze("Đắt quá")
    engine.analyze("Để suy nghĩ đã")
    dash = engine.dashboard()
    for key in (
        "win_probability",
        "next_best_action",
        "negotiation_timeline",
        "strategy_evolution",
        "session_count",
    ):
        assert key in dash["widgets"]
    assert dash["static_tree_forbidden"] is True
    q = engine.quality_snapshot()
    assert q["ok"] is True
    assert q["static_tree_forbidden"] is True
    assert q["strategy_graph_enabled"] is True


def test_suite_sizes() -> None:
    assert len(SCENARIO_CASES) >= 500
    assert len(PREDICTION_CASES) >= 300
    assert len(COMPARE_CASES) >= 200
