"""VCIE objection classification regression."""

from app.application.services.vcie_engine import VCIEEngine, _libraries


def test_price_objection_maps_to_gia() -> None:
    _libraries.cache_clear()
    result = VCIEEngine().analyze(
        turns=[
            {"speaker": "agent", "text": "Em chào anh", "start_ms": 0, "end_ms": 500, "confidence": 0.9},
            {"speaker": "customer", "text": "Đắt quá", "start_ms": 500, "end_ms": 1200, "confidence": 0.9},
        ]
    )
    labels = [a.label for a in result.annotations if a.kind == "objection"]
    assert "Giá" in labels


def test_empty_turns_insufficient_evidence() -> None:
    result = VCIEEngine().analyze(turns=[])
    assert result.status == "Insufficient Evidence"
