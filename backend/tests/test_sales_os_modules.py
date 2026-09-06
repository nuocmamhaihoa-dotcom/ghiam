"""First-party tests for Sales OS Sprint 8–10 modules."""

from __future__ import annotations

from app.application.services.auto_sop import AutoSOPGenerator
from app.application.services.fraud_compliance import FraudComplianceService
from app.application.services.live_assistant import LiveCallAssistant
from app.application.services.memory_graph import MemoryGraphService
from app.application.services.multi_product import MultiProductIntelligence
from app.application.services.objection_simulator import ObjectionSimulator
from app.application.services.personality_engine import PersonalityEngine
from app.application.services.pipeline_stages import PIPELINE_ORDER
from app.application.services.pragmatics import PragmaticsEngine
from app.application.services.sales_forecast import SalesForecastService


def _turns() -> list[dict]:
    return [
        {
            "speaker": "customer",
            "text": "Giá đắt quá, bên kia rẻ hơn. Bao giờ giao? Có bảo hành không?",
            "start": 0.0,
            "end": 4.0,
        },
        {
            "speaker": "agent",
            "text": "Em hiểu. Gói này gồm bảo hành 12 tháng và giao trong 48 giờ.",
            "start": 4.0,
            "end": 8.0,
        },
    ]


def test_live_call_assistant_sla() -> None:
    out = LiveCallAssistant().suggest(_turns(), now_ts=20.0)
    assert out["status"] in {"ok", "Insufficient Evidence"}
    assert int(out["latency_ms"]) < 2000
    assert out["sla_ok"] is True


def test_personality_engine() -> None:
    out = PersonalityEngine().analyze(_turns())
    assert out["status"] in {"ok", "Insufficient Evidence"}


def test_memory_graph_service() -> None:
    graph = MemoryGraphService().build_from_analysis(
        call_id="test-call",
        violations=[{"rule_id": "R-OBJ-01"}],
        root_cause={"primary_cause_code": "RC-PRICE"},
        coaching={"tips": [{"title": "reframe value"}]},
        intents=[{"name": "price_concern"}],
        objections=[{"type": "price"}],
        products=["PKG-HEALTH"],
    )
    assert graph["stats"]["node_count"] >= 2
    assert graph["stats"]["edge_count"] >= 1
    hits = MemoryGraphService().search(graph, node_type="rulebook")
    assert hits["count"] >= 1


def test_objection_simulator() -> None:
    sim = ObjectionSimulator()
    assert sim.list_scenarios("price")
    started = sim.start(group="price", seed=3)
    assert started["status"] == "ok"
    scenario_id = started["scenario"]["id"]
    graded = sim.grade(
        scenario_id=scenario_id,
        agent_reply="Em đồng ý giá là điểm cân nhắc. Em tặng bảo hành và hỗ trợ giao nhanh.",
    )
    assert graded["status"] in {"ok", "Insufficient Evidence"}


def test_fraud_compliance_service() -> None:
    out = FraudComplianceService().scan(_turns())
    assert out["status"] in {"ok", "Insufficient Evidence"}


def test_auto_sop_generator() -> None:
    out = AutoSOPGenerator().generate(
        [{"id": "g1", "score": 95, "transcript": _turns()}],
        version="test-v1",
    )
    assert out["status"] in {"ok", "Insufficient Evidence"}


def test_sales_forecast_service() -> None:
    out = SalesForecastService().forecast(
        historical=[
            {"conversion_rate": 0.18, "revenue": 90_000_000},
            {"conversion_rate": 0.2, "revenue": 100_000_000},
            {"conversion_rate": 0.22, "revenue": 110_000_000},
        ],
        horizon_days=30,
    )
    assert out["status"] in {"ok", "Insufficient Evidence"}


def test_multi_product_intelligence() -> None:
    out = MultiProductIntelligence().suggest(_turns(), current_sku="PKG-HEALTH")
    assert out["status"] in {"ok", "Insufficient Evidence"}


def test_pragmatics_and_pipeline_order() -> None:
    result = PragmaticsEngine().analyze_transcript(_turns())
    payload = PragmaticsEngine().to_dict(result)
    assert "status" in payload
    assert "pragmatics" in PIPELINE_ORDER
    assert "memory_graph" in PIPELINE_ORDER
    assert PIPELINE_ORDER.index("pragmatics") < PIPELINE_ORDER.index("memory_graph")
