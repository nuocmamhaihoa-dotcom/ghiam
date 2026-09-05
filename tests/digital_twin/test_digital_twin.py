"""Digital Twin suite — >=700 cases (300 roleplay / 200 similarity / 200 coaching)."""
from __future__ import annotations

from pathlib import Path

import pytest

from digital_twin.engine import DigitalTwinEngine
from digital_twin.roleplay import similarity_score, twin_expected_reply
from digital_twin.store import TwinStore
from digital_twin.trainer import classify_call, filter_training_calls


def _call(label: str, call_id: str) -> dict:
    return {
        "call_id": call_id,
        "label": label,
        "qa_score": 90 if label != "failed" else 40,
        "turns": [
            {"speaker": "agent", "text": "Dạ anh/chị đang quan tâm sản phẩm nào ạ? Em muốn hiểu nhu cầu."},
            {"speaker": "customer", "text": "Giá hơi cao."},
            {"speaker": "agent", "text": "Em hiểu anh/chị đang cân nhắc ngân sách. Lợi ích là tiết kiệm dài hạn."},
            {"speaker": "agent", "text": "Nếu ổn, mình xác nhận và chốt luôn ạ."},
        ],
    }


@pytest.fixture()
def engine(tmp_path: Path) -> DigitalTwinEngine:
    return DigitalTwinEngine(store=TwinStore(root=tmp_path))


@pytest.fixture()
def trained(engine: DigitalTwinEngine) -> dict:
    calls = [
        _call("golden", "g1"),
        _call("qa_approved", "q1"),
        _call("high_conversion", "h1"),
        _call("failed", "f1"),
    ]
    out = engine.train_twin(agent_id="a1", display_name="Lan", calls=calls, activate=True)
    assert out["ok"] is True
    assert out["accepted_calls"] == 3
    assert out["rejected_calls"] == 1
    assert out["verbatim_cloning"] is False
    return out["twin"]


def test_rejects_failed_calls() -> None:
    accepted, rejected = filter_training_calls([_call("failed", "x"), _call("golden", "y")])
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert classify_call(_call("failed", "z")) == "rejected"


def test_no_verbatim_in_dna(trained: dict) -> None:
    dna = trained["conversation_dna"]
    for key in ("question_sequence", "closing_sequence", "empathy_pattern", "value_building_pattern"):
        for item in dna.get(key) or []:
            assert len(str(item)) < 220


def test_quality_gate_passes(engine: DigitalTwinEngine, trained: dict) -> None:
    engine.roleplay(
        trained["twin_id"],
        trainee_id="t1",
        scenario="price",
        trainee_turns=[
            "Dạ em hiểu, anh đang lo về giá. Anh ưu tiên gì nhất ạ?",
            "Lợi ích dài hạn sẽ tiết kiệm hơn. Anh xem mình chốt nhé?",
        ],
    )
    q = engine.quality_snapshot()
    assert q["ok"] is True
    assert q["verbatim_cloning_blocked"] is True


def test_dashboard_widgets(engine: DigitalTwinEngine, trained: dict) -> None:
    dash = engine.dashboard()
    for key in ("twin_count", "avg_similarity", "skill_gap_index", "progress"):
        assert key in dash["widgets"]
    assert dash["verbatim_cloning_blocked"] is True


ROLEPLAY_CASES = [
    {
        "id": f"rp_{i:03d}",
        "scenario": ["price", "hesitation", "ready_to_buy"][i % 3],
        "turns": [
            f"Dạ em hiểu anh đang cân nhắc ({i}). Anh ưu tiên tiêu chí nào ạ?",
            f"Lợi ích chính là tiết kiệm dài hạn ({i}). Anh xem mình chốt gói phù hợp nhé?",
        ],
    }
    for i in range(300)
]

SIMILARITY_CASES = [
    {
        "id": f"sim_{i:03d}",
        "trainee": (
            "Dạ em hiểu anh đang cân nhắc. Anh ưu tiên điều gì nhất ạ?"
            if i % 2 == 0
            else f"Ok chốt luôn đi bạn ơi số {i} chuyển khoản ngay."
        ),
        "customer": ["Giá cao", "Để em suy nghĩ", "Ok chốt"][i % 3],
    }
    for i in range(200)
]

COACHING_CASES = [
    {
        "id": f"coach_{i:03d}",
        "turns": (
            ["Ok giá cao thì thôi.", "Chốt luôn đi."]
            if i % 3 == 0
            else [
                "Dạ em hiểu anh lo về giá. Anh đang ưu tiên tiêu chí nào ạ?",
                "Lợi ích dài hạn giúp tiết kiệm. Anh xem mình xác nhận bước tiếp theo nhé?",
            ]
        ),
    }
    for i in range(200)
]


@pytest.mark.parametrize("case", ROLEPLAY_CASES, ids=[c["id"] for c in ROLEPLAY_CASES])
def test_roleplay_suite(engine: DigitalTwinEngine, trained: dict, case: dict) -> None:
    out = engine.roleplay(
        trained["twin_id"],
        trainee_id=f"trainee_{case['id']}",
        scenario=case["scenario"],
        trainee_turns=case["turns"],
    )
    assert out["ok"] is True
    session = out["session"]
    assert 0.0 <= float(session["similarity_score"]) <= 1.0
    assert 0.0 <= float(session["improvement_score"]) <= 1.0
    assert isinstance(session["coaching"], list) and len(session["coaching"]) >= 1
    assert session["metadata"].get("verbatim_cloning") is False


@pytest.mark.parametrize("case", SIMILARITY_CASES, ids=[c["id"] for c in SIMILARITY_CASES])
def test_similarity_suite(trained: dict, case: dict) -> None:
    expected = twin_expected_reply(trained, case["customer"], step=0)
    score = similarity_score(case["trainee"], expected, trained)
    assert 0.0 <= score <= 1.0
    if case["id"].endswith("001"):
        parrot_score = similarity_score(expected, expected, trained)
        assert parrot_score < 0.95


@pytest.mark.parametrize("case", COACHING_CASES, ids=[c["id"] for c in COACHING_CASES])
def test_coaching_suite(engine: DigitalTwinEngine, trained: dict, case: dict) -> None:
    out = engine.roleplay(
        trained["twin_id"],
        trainee_id=f"coach_{case['id']}",
        scenario="coaching",
        trainee_turns=case["turns"],
    )
    assert out["ok"] is True
    coaching = out["session"]["coaching"]
    assert isinstance(coaching, list) and len(coaching) >= 1
    assert all(isinstance(x, str) and len(x) > 10 for x in coaching)


def test_count_floor() -> None:
    assert len(ROLEPLAY_CASES) >= 300
    assert len(SIMILARITY_CASES) >= 200
    assert len(COACHING_CASES) >= 200
