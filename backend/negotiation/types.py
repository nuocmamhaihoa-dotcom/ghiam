"""Negotiation Strategy Engine — shared types."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


QUALITY_THRESHOLDS: dict[str, float] = {
    "prediction_accuracy": 0.70,
    "strategy_consistency": 0.65,
    "evidence_validation": 0.70,
    "confidence_stability": 0.60,
}

STRATEGY_KINDS = ("empathy", "value", "comparison", "urgency", "clarify")

OBJECTION_CUES: dict[str, tuple[str, ...]] = {
    "price": ("đắt", "cao", "giá", "expensive", "costly", "budget", "ngân sách"),
    "timing": ("để sau", "suy nghĩ", "chưa", "later", "think", "not now", "mai"),
    "trust": ("không tin", "lừa", "scam", "uy tín", "bảo hành", "doubt"),
    "competitor": ("đối thủ", "bên kia", "rẻ hơn", "competitor", "elsewhere"),
    "need": ("không cần", "chưa cần", "không dùng", "no need", "unnecessary"),
}

EMOTION_CUES: dict[str, tuple[str, ...]] = {
    "anxious": ("lo", "sợ", "worried", "anxious", "ngại"),
    "frustrated": ("bực", "mệt", "frustrated", "annoyed", "khó chịu"),
    "curious": ("thế nào", "sao", "how", "what if", "chi tiết"),
    "positive": ("ok", "được", "hay", "good", "interested", "quan tâm"),
    "resistant": ("không", "thôi", "no", "dừng", "đủ rồi"),
}


@dataclass
class NextStepPrediction:
    next_question: str
    next_objection: str
    next_emotion: str
    exit_risk: float
    buy_probability: float
    horizon: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NegotiationStrategy:
    strategy_id: str
    kind: str
    label: str
    win_probability: float
    risk_score: float
    recommended_script: str
    forbidden_script: str
    rationale: str
    evidence: list[str] = field(default_factory=list)
    next_best_action: str = ""
    graph_node_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyGraphNode:
    node_id: str
    label: str
    state: str
    strategies: list[str] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    win_probability: float = 0.5
    exit_risk: float = 0.3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyGraph:
    graph_id: str
    root_node_id: str
    nodes: list[StrategyGraphNode] = field(default_factory=list)
    evolution: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "root_node_id": self.root_node_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "evolution": self.evolution,
        }


@dataclass
class NegotiationSession:
    session_id: str
    customer_utterance: str
    context: dict[str, Any]
    prediction: dict[str, Any]
    strategies: list[dict[str, Any]]
    selected_strategy_id: str | None
    graph: dict[str, Any]
    timeline: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
