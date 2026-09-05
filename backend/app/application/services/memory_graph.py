"""Enterprise Memory Graph — links products, SOP, rules, intents, objections, coaching."""

from __future__ import annotations

from typing import Any


class MemoryGraphService:
    """Build and query a knowledge graph over conversation intelligence entities."""

    NODE_TYPES = (
        "product",
        "sop",
        "rule",
        "intent",
        "objection",
        "customer_type",
        "golden_call",
        "root_cause",
        "coaching",
    )

    def build_from_analysis(
        self,
        *,
        call_id: str,
        violations: list[dict[str, Any]],
        root_cause: dict[str, Any],
        coaching: dict[str, Any],
        intents: list[dict[str, Any]] | None = None,
        objections: list[dict[str, Any]] | None = None,
        products: list[str] | None = None,
    ) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        def add_node(node_id: str, node_type: str, label: str, meta: dict[str, Any] | None = None) -> None:
            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "type": node_type,
                    "label": label,
                    "meta": meta or {},
                }

        def add_edge(source: str, target: str, relation: str, weight: float = 1.0) -> None:
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "weight": weight,
                }
            )

        call_node = f"call:{call_id}"
        add_node(call_node, "golden_call" if False else "call", f"Call {call_id}")

        for item in violations:
            rule_code = str(item.get("rule_code") or item.get("rule_id") or "UNKNOWN")
            rid = f"rule:{rule_code}"
            add_node(rid, "rule", rule_code, {"severity": item.get("severity")})
            add_edge(call_node, rid, "violates", 1.0)
            cause = item.get("root_cause") or root_cause.get("primary_code")
            if cause:
                cid = f"root_cause:{cause}"
                add_node(cid, "root_cause", str(cause))
                add_edge(rid, cid, "caused_by", 0.9)

        primary = root_cause.get("primary_code") or root_cause.get("primary_cause_code")
        if primary:
            cid = f"root_cause:{primary}"
            add_node(cid, "root_cause", str(primary), {"confidence": root_cause.get("confidence")})
            add_edge(call_node, cid, "root_cause", float(root_cause.get("confidence") or 0.5))

        tips = coaching.get("tips") or coaching.get("call_tips") or []
        for tip in tips[:10]:
            tip_id = f"coaching:{tip.get('id') or tip.get('rule_code') or tip.get('title') or len(nodes)}"
            add_node(
                tip_id,
                "coaching",
                str(tip.get("title") or tip.get("tip") or tip_id),
                {"priority": tip.get("priority")},
            )
            add_edge(call_node, tip_id, "needs_coaching", 0.8)

        for intent in intents or []:
            iid = f"intent:{intent.get('id') or intent.get('name')}"
            add_node(iid, "intent", str(intent.get("name") or intent.get("id")), intent)
            add_edge(call_node, iid, "expresses", float(intent.get("confidence") or 0.5))

        for obj in objections or []:
            oid = f"objection:{obj.get('id') or obj.get('type') or obj.get('text', '')[:32]}"
            add_node(oid, "objection", str(obj.get("type") or obj.get("text") or oid), obj)
            add_edge(call_node, oid, "raises", float(obj.get("confidence") or 0.5))

        for product in products or []:
            pid = f"product:{product}"
            add_node(pid, "product", product)
            add_edge(call_node, pid, "discusses", 0.7)

        return {
            "call_id": call_id,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "types": sorted({n["type"] for n in nodes.values()}),
            },
            "visualization": {
                "layout": "force-directed",
                "node_types": list(self.NODE_TYPES),
            },
        }

    def search(self, graph: dict[str, Any], *, node_type: str | None = None, query: str | None = None) -> dict[str, Any]:
        nodes = graph.get("nodes") or []
        q = (query or "").strip().lower()
        filtered = []
        for node in nodes:
            if node_type and node.get("type") != node_type:
                continue
            if q and q not in str(node.get("label", "")).lower() and q not in str(node.get("id", "")).lower():
                continue
            filtered.append(node)
        return {"matches": filtered, "count": len(filtered)}
