"""Shared types for Enterprise Memory Graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NO_DATA = "Không có dữ liệu trong hệ thống."

NODE_TYPES = (
    "product",
    "sop",
    "rulebook",
    "intent",
    "objection",
    "pricing",
    "policy",
    "promotion",
    "golden_call",
    "customer_type",
    "root_cause",
    "coaching",
    "compliance",
    "faq",
    "call",
    "revenue_leak",
)

RELATION_TYPES = (
    "product_has_sop",
    "product_has_objection",
    "objection_maps_rule",
    "customer_has_intent",
    "golden_call_has_coaching",
    "root_cause_causes_revenue_leak",
    "product_has_pricing",
    "product_has_policy",
    "product_has_promotion",
    "rule_enforces_compliance",
    "intent_linked_objection",
    "coaching_addresses_root_cause",
    "call_violates_rule",
    "call_expresses_intent",
    "call_raises_objection",
    "call_discusses_product",
    "call_needs_coaching",
    "call_has_root_cause",
)


@dataclass(slots=True)
class Node:
    id: str
    type: str
    label: str
    version: str = "1.0.0"
    source: str = "system"
    timestamp: float = 0.0
    confidence: float = 1.0
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Edge:
    id: str
    source: str
    target: str
    relation: str
    confidence: float = 1.0
    version: str = "1.0.0"
    source_ref: str = "system"
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
