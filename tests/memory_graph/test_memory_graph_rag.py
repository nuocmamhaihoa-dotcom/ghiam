"""Enterprise Memory Graph + RAG suite (>3000 cases)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_graph.graph import EnterpriseMemoryGraph
from memory_graph.store import GraphStore
from memory_graph.sync import sync_all
from memory_graph.types import NO_DATA, NODE_TYPES, RELATION_TYPES
from rag.pipeline import EvidenceRAG
from rag.vector_index import VectorIndex

FIX = Path(__file__).with_name("fixtures_3200.jsonl")


def _load() -> list[dict]:
    rows = []
    with FIX.open(encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))
    return rows


FIXTURES = _load()


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    root = tmp_path_factory.mktemp("mgr")
    store = GraphStore(graph_path=root / "g.json", history_path=root / "h.jsonl")
    graph = EnterpriseMemoryGraph(store=store)
    sync_all(graph, reset=True)
    index = VectorIndex(path=root / "idx.json")
    rag = EvidenceRAG(graph=graph, index=index)
    rag.reindex()
    return graph, rag


@pytest.mark.parametrize("row", FIXTURES, ids=[r["id"] for r in FIXTURES])
def test_memory_graph_fixture(harness, row):
    graph, rag = harness
    kind = row["kind"]

    if kind == "retrieval":
        ans = rag.ask(row["question"], limit=5)
        if row["expect_data"]:
            assert ans["status"] == "ok"
            assert ans["answer"] != NO_DATA
            assert ans["citations"]
            # evidence-backed: every citation id exists in graph
            for c in ans["citations"]:
                assert graph.get_node(c["id"]) is not None
        else:
            assert ans["answer"] == NO_DATA
            assert ans["citations"] == []

    elif kind == "citation":
        ans = rag.ask(row["question"], limit=5)
        assert ans["status"] == "ok"
        assert ans["citations"]
        for c in ans["citations"]:
            node = graph.get_node(c["id"])
            assert node is not None
            assert c.get("source")
            assert float(c.get("score") or 0) > 0

    elif kind == "version":
        before = graph.get_node(row["node_id"])
        assert before is not None
        old_version = before.version
        graph.upsert_node(
            node_id=row["node_id"],
            node_type=before.type,
            label=before.label,
            content=row["content"],
            source=before.source,
            confidence=before.confidence,
        )
        after = graph.get_node(row["node_id"])
        assert after is not None
        assert after.content == row["content"]
        # version bumps on content change
        assert after.version != old_version or after.content == before.content
        hist = graph.version_history(row["node_id"], limit=5)
        assert hist

    elif kind == "duplicate":
        before_count = len(graph.store.list_nodes())
        graph.upsert_node(
            node_id=row["node_id"],
            node_type="objection",
            label=row["label"],
            content=row["content"],
            source="taxonomy/objection",
            confidence=0.92,
        )
        after_count = len(graph.store.list_nodes())
        assert after_count == before_count
        errors = graph.store.validate_integrity()
        assert not any("duplicate" in e for e in errors)

    elif kind == "no_data":
        ans = rag.ask(row["question"], limit=5)
        assert ans["answer"] == NO_DATA
        assert ans["status"] == "no_data"
        assert ans["citations"] == []

    elif kind == "integrity":
        report = graph.quality_report()
        assert report["ok"] is True
        assert report["node_count"] > 0
        assert report["edge_count"] > 0
        # no dangling relations
        for edge in graph.store.list_edges():
            assert graph.get_node(edge.source) is not None
            assert graph.get_node(edge.target) is not None
            assert edge.relation in RELATION_TYPES
        for node in graph.store.list_nodes():
            assert node.type in NODE_TYPES

    else:
        raise AssertionError(f"unknown kind {kind}")


def test_sync_corpora_and_relations(tmp_path):
    store = GraphStore(graph_path=tmp_path / "g.json", history_path=tmp_path / "h.jsonl")
    graph = EnterpriseMemoryGraph(store=store)
    result = sync_all(graph, reset=True)
    assert result["ok"] is True
    assert result["synced_nodes"] >= 20
    # required relation families exist
    rels = {e.relation for e in graph.store.list_edges()}
    for required in (
        "product_has_sop",
        "product_has_objection",
        "objection_maps_rule",
        "customer_has_intent",
        "golden_call_has_coaching",
        "root_cause_causes_revenue_leak",
    ):
        assert required in rels


def test_service_build_compatible_with_scoring(tmp_path, monkeypatch):
    # Isolate default store paths by injecting temp graph via monkeypatch on GraphStore defaults is hard;
    # instead exercise service methods that accept live graph through sync.
    from app.application.services.memory_graph import MemoryGraphService

    svc = MemoryGraphService()
    # Use ephemeral store
    store = GraphStore(graph_path=tmp_path / "g.json", history_path=tmp_path / "h.jsonl")
    svc.graph = EnterpriseMemoryGraph(store=store)
    from rag.pipeline import EvidenceRAG
    from rag.vector_index import VectorIndex

    svc.rag = EvidenceRAG(graph=svc.graph, index=VectorIndex(path=tmp_path / "idx.json"))
    payload = svc.build_from_analysis(
        call_id="c-test",
        violations=[{"rule_code": "R1", "message": "cam kết ngoài bảng"}],
        root_cause={"primary_code": "OVERPROMISE", "confidence": 0.9},
        coaching={"tips": [{"title": "Cite pricing", "tip": "Dẫn chứng bảng giá"}]},
        intents=[{"name": "price_compare", "confidence": 0.8}],
        objections=[{"type": "price", "text": "Đắt quá", "confidence": 0.9}],
        products=["Thẻ tín dụng"],
    )
    assert payload["stats"]["node_count"] >= 1
    assert payload["nodes"]
    assert payload["edges"]
    ask = svc.ask("Phí thường niên thẻ tín dụng")
    # may be no_data if not synced; sync then ask
    svc.sync(reset=False)
    ask = svc.ask("Phí thường niên thẻ tín dụng")
    assert ask["answer"]
    if ask["status"] == "ok":
        assert ask["citations"]
    else:
        assert ask["answer"] == NO_DATA
