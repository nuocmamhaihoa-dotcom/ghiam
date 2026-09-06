"""Factory quality-gate smoke tests."""

from __future__ import annotations

from collections import Counter

from pragmatics.factory import expand_utterances, quality_gate, run_factory


def test_expand_dialect_balance() -> None:
    rows = expand_utterances(1500)
    dialects = Counter(r["dialect"] for r in rows)
    for d in ("north", "central", "south"):
        assert dialects[d] / len(rows) >= 0.20


def test_quality_gate_batch() -> None:
    rows = expand_utterances(500)
    gate = quality_gate(rows, label="unit_batch")
    assert gate.ok, gate.errors
    assert gate.meta["accuracy"] >= 0.70


def test_quick_factory(tmp_path, monkeypatch) -> None:
    import pragmatics.factory as factory

    monkeypatch.setattr(factory, "OUT", tmp_path)
    monkeypatch.setattr(factory, "MODELS", tmp_path / "models")
    summary = run_factory(
        utterances=500,
        situations=500,
        fake_yes=50,
        soft_no=50,
        topic_shift=30,
        exit_imminent=30,
    )
    assert summary["all_gates_ok"] is True
    assert summary["utterances"] == 500
