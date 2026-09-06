"""Enterprise Memory Graph — long-term knowledge for AI Sales OS."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from memory_graph.store import GraphStore
from memory_graph.types import NO_DATA, RELATION_TYPES, Edge, Node


class EnterpriseMemoryGraph:
    """Typed graph façade used by RAG and Sales OS services."""

    def __init__(self, store: GraphStore | None = None) -> None:
        self.store = store or GraphStore()

    def upsert_node(
        self,
        *,
        node_id: str,
        node_type: str,
        label: str,
        content: str,
        source: str,
        confidence: float = 0.9,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
    ) -> Node:
        node = Node(
            id=node_id,
            type=node_type,
            label=label,
            version=version,
            source=source,
            timestamp=time.time(),
            confidence=max(0.0, min(1.0, confidence)),
            content=content.strip(),
            metadata=metadata or {},
        )
        return self.store.upsert_node(node)

    def link(
        self,
        *,
        source_id: str,
        target_id: str,
        relation: str,
        confidence: float = 0.85,
        bidirectional: bool = False,
        source_ref: str = "system",
    ) -> list[Edge]:
        if relation not in RELATION_TYPES:
            raise ValueError(f"unsupported relation: {relation}")
        out: list[Edge] = []
        eid = _edge_id(source_id, target_id, relation)
        out.append(
            self.store.upsert_edge(
                Edge(
                    id=eid,
                    source=source_id,
                    target=target_id,
                    relation=relation,
                    confidence=confidence,
                    source_ref=source_ref,
                    timestamp=time.time(),
                )
            )
        )
        if bidirectional:
            eid2 = _edge_id(target_id, source_id, relation)
            out.append(
                self.store.upsert_edge(
                    Edge(
                        id=eid2,
                        source=target_id,
                        target=source_id,
                        relation=relation,
                        confidence=confidence,
                        source_ref=source_ref,
                        timestamp=time.time(),
                    )
                )
            )
        return out

    def get_node(self, node_id: str) -> Node | None:
        return self.store.get_node(node_id)

    def neighbors(self, node_id: str, relation: str | None = None) -> list[dict[str, Any]]:
        return self.store.neighbors(node_id, relation=relation)

    def version_history(self, entity_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.version_history(entity_id, limit=limit)

    def explore(self, *, limit: int = 200) -> dict[str, Any]:
        graph = self.store.export_graph()
        errors = self.store.validate_integrity()
        return {
            "nodes": graph["nodes"][:limit],
            "edges": graph["edges"][: limit * 3],
            "stats": graph["stats"],
            "integrity_errors": errors,
            "ok": not errors,
        }

    def search_nodes(
        self,
        query: str,
        *,
        node_type: str | None = None,
        limit: int = 20,
    ) -> list[Node]:
        q = query.lower().strip()
        if not q:
            return []
        hits: list[tuple[float, Node]] = []
        for node in self.store.list_nodes(node_type=node_type):
            hay = f"{node.label} {node.content} {node.id} {' '.join(str(v) for v in node.metadata.values())}".lower()
            tokens = [t for t in q.split() if len(t) > 1]
            if q not in hay and not any(tok in hay for tok in tokens):
                continue
            score = hay.count(q) + sum(1 for tok in tokens if tok in hay) * 0.35
            score *= node.confidence
            hits.append((score, node))
        hits.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in hits[:limit]]

    def similar_objections(self, text: str, *, limit: int = 10) -> list[dict[str, Any]]:
        nodes = self.search_nodes(text, node_type="objection", limit=limit)
        out: list[dict[str, Any]] = []
        for node in nodes:
            related = self.neighbors(node.id, relation="objection_maps_rule")
            out.append(
                {
                    "objection": node.to_dict(),
                    "related_rules": related,
                    "confidence": node.confidence,
                }
            )
        return out

    def similar_calls(self, text: str, *, limit: int = 10) -> list[dict[str, Any]]:
        nodes = self.search_nodes(text, node_type="golden_call", limit=limit)
        out: list[dict[str, Any]] = []
        for node in nodes:
            related = self.neighbors(node.id, relation="golden_call_has_coaching")
            out.append(
                {
                    "call": node.to_dict(),
                    "related_coaching": related,
                    "confidence": node.confidence,
                }
            )
        return out

    def build_from_analysis(
        self,
        *,
        call_id: str,
        violations: list[dict[str, Any]],
        root_cause: dict[str, Any],
        coaching: dict[str, Any] | list[dict[str, Any]],
        intents: list[dict[str, Any]] | None = None,
        objections: list[dict[str, Any]] | None = None,
        products: list[str] | None = None,
    ) -> dict[str, Any]:
        created: list[str] = []
        call_node = f"call:{call_id}"
        self.upsert_node(
            node_id=call_node,
            node_type="call",
            label=f"Call {call_id}",
            content=f"Analyzed call {call_id}",
            source=f"call:{call_id}",
            confidence=1.0,
        )
        created.append(call_node)

        for product in products or []:
            pid = f"product:{_slug(product)}"
            self.upsert_node(
                node_id=pid,
                node_type="product",
                label=product,
                content=product,
                source=f"call:{call_id}",
                confidence=0.8,
            )
            created.append(pid)
            self.link(
                source_id=call_node,
                target_id=pid,
                relation="call_discusses_product",
                confidence=0.7,
            )

        for obj in objections or []:
            label = str(obj.get("type") or obj.get("label") or obj.get("text") or "objection")
            oid = f"objection:{_slug(label)}"
            self.upsert_node(
                node_id=oid,
                node_type="objection",
                label=label,
                content=str(obj.get("text") or label),
                source=f"call:{call_id}",
                confidence=float(obj.get("confidence") or 0.7),
                metadata=obj,
            )
            created.append(oid)
            self.link(
                source_id=call_node,
                target_id=oid,
                relation="call_raises_objection",
                confidence=0.75,
            )
            for product in products or []:
                pid = f"product:{_slug(product)}"
                self.link(
                    source_id=pid,
                    target_id=oid,
                    relation="product_has_objection",
                    confidence=0.75,
                )

        for intent in intents or []:
            label = str(intent.get("name") or intent.get("label") or intent.get("id") or "intent")
            iid = f"intent:{_slug(label)}"
            self.upsert_node(
                node_id=iid,
                node_type="intent",
                label=label,
                content=label,
                source=f"call:{call_id}",
                confidence=float(intent.get("confidence") or 0.7),
                metadata=intent,
            )
            created.append(iid)
            self.link(
                source_id=call_node,
                target_id=iid,
                relation="call_expresses_intent",
                confidence=0.7,
            )
            self.upsert_node(
                node_id="customer_type:general",
                node_type="customer_type",
                label="Khách hàng chung",
                content="Khách hàng chung",
                source="system",
                confidence=1.0,
            )
            self.link(
                source_id="customer_type:general",
                target_id=iid,
                relation="customer_has_intent",
                confidence=0.6,
            )

        primary = (
            root_cause.get("primary_code")
            or root_cause.get("primary_cause_code")
            or root_cause.get("primary")
        )
        if primary:
            rid = f"root_cause:{_slug(str(primary))}"
            self.upsert_node(
                node_id=rid,
                node_type="root_cause",
                label=str(primary),
                content=str(root_cause.get("summary") or primary),
                source=f"call:{call_id}",
                confidence=float(root_cause.get("confidence") or 0.7),
                metadata=root_cause,
            )
            created.append(rid)
            self.link(
                source_id=call_node,
                target_id=rid,
                relation="call_has_root_cause",
                confidence=0.8,
            )
            leak_id = f"revenue_leak:{_slug(str(primary))}"
            self.upsert_node(
                node_id=leak_id,
                node_type="revenue_leak",
                label=f"Revenue leak: {primary}",
                content=f"Revenue leak linked to root cause {primary}",
                source=f"call:{call_id}",
                confidence=float(root_cause.get("confidence") or 0.7),
            )
            self.link(
                source_id=rid,
                target_id=leak_id,
                relation="root_cause_causes_revenue_leak",
                confidence=0.85,
            )

        if isinstance(coaching, dict):
            tips = list(coaching.get("tips") or coaching.get("call_tips") or [])
        else:
            tips = list(coaching or [])
        for tip in tips[:10]:
            title = str(tip.get("title") or tip.get("tip") or tip.get("suggestion") or "coaching")
            cid = f"coaching:{call_id}:{_slug(title)[:40]}"
            self.upsert_node(
                node_id=cid,
                node_type="coaching",
                label=title,
                content=str(tip.get("tip") or tip.get("suggestion") or title),
                source=f"call:{call_id}",
                confidence=float(tip.get("confidence") or 0.75),
                metadata=tip,
            )
            created.append(cid)
            self.link(
                source_id=call_node,
                target_id=cid,
                relation="call_needs_coaching",
                confidence=0.8,
            )

        for item in violations:
            rule_code = str(item.get("rule_code") or item.get("rule_id") or "UNKNOWN")
            rid = f"rulebook:{_slug(rule_code)}"
            self.upsert_node(
                node_id=rid,
                node_type="rulebook",
                label=rule_code,
                content=str(item.get("message") or rule_code),
                source=f"call:{call_id}",
                confidence=0.9,
                metadata=item,
            )
            created.append(rid)
            self.link(
                source_id=call_node,
                target_id=rid,
                relation="call_violates_rule",
                confidence=1.0,
            )
            comp_id = f"compliance:{_slug(rule_code)}"
            self.upsert_node(
                node_id=comp_id,
                node_type="compliance",
                label=f"Compliance {rule_code}",
                content=str(item.get("message") or rule_code),
                source=f"call:{call_id}",
                confidence=0.95,
            )
            self.link(
                source_id=rid,
                target_id=comp_id,
                relation="rule_enforces_compliance",
                confidence=0.9,
            )

        self.store.save()
        exported = self.explore(limit=200)
        return {
            "call_id": call_id,
            "nodes": exported["nodes"],
            "edges": exported["edges"],
            "stats": exported["stats"],
            "nodes_touched": created,
            "status": "ok" if created else NO_DATA,
            "visualization": {
                "layout": "force-directed",
                "node_types": sorted({n["type"] for n in exported["nodes"]}),
            },
        }

    def quality_report(self) -> dict[str, Any]:
        errors = self.store.validate_integrity()
        return {
            "node_count": len(self.store.list_nodes()),
            "edge_count": len(self.store.list_edges()),
            "integrity_errors": errors,
            "ok": not errors,
        }


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower()).strip("_") or "unknown"


def _edge_id(source: str, target: str, relation: str) -> str:
    digest = hashlib.sha1(f"{source}|{target}|{relation}".encode()).hexdigest()[:12]
    return f"edge:{digest}"
