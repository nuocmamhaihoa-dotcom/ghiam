"""Canonical types for AI Self-Learning Lab."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class KnowledgeLayer(str, Enum):
    RAW = "layer_1_raw_calls"
    VERIFIED = "layer_2_verified_knowledge"
    APPROVED = "layer_3_approved_rules"
    PRODUCTION = "layer_4_production_knowledge"


class ProposalStatus(str, Enum):
    PENDING = "pending_qa"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    EDITED = "edited"


class ProposalKind(str, Enum):
    PATTERN = "pattern"
    INTENT = "intent"
    OBJECTION = "objection"
    CLUSTER = "cluster"
    GOLDEN_CALL = "golden_call"
    FAILURE_PATTERN = "failure_pattern"
    COACHING = "coaching"
    RULE = "rule"
    SOP = "sop"
    REVENUE_LEAK = "revenue_leak"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(slots=True)
class Evidence:
    call_id: str
    quote: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str = "customer"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PatternHit:
    pattern_id: str
    kind: str
    text: str
    normalized: str
    novelty: float
    confidence: float
    evidence: list[Evidence] = field(default_factory=list)
    known: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass(slots=True)
class Cluster:
    cluster_id: str
    name: str
    variants: list[str]
    hidden_meaning: str
    confidence: float
    size: int
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass(slots=True)
class Proposal:
    proposal_id: str
    kind: ProposalKind
    title: str
    summary: str
    suggested_rule: str
    suggested_coaching: str
    suggested_sop_update: str
    evidence: list[Evidence]
    confidence: float
    novelty: float
    evidence_count: int
    qa_agreement_prediction: float
    expected_impact: str
    status: ProposalStatus = ProposalStatus.PENDING
    layer: KnowledgeLayer = KnowledgeLayer.VERIFIED
    version: str = "0.1.0"
    created_at: str = field(default_factory=now_iso)
    reviewer: str | None = None
    reviewed_at: str | None = None
    diff: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def quality_score(self) -> float:
        return round(
            0.35 * self.confidence
            + 0.25 * self.novelty
            + 0.25 * min(1.0, self.evidence_count / 10.0)
            + 0.15 * self.qa_agreement_prediction,
            4,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value if isinstance(self.kind, ProposalKind) else self.kind
        d["status"] = self.status.value if isinstance(self.status, ProposalStatus) else self.status
        d["layer"] = self.layer.value if isinstance(self.layer, KnowledgeLayer) else self.layer
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        d["quality_score"] = self.quality_score()
        return d


@dataclass(slots=True)
class KnowledgeRecord:
    record_id: str
    layer: KnowledgeLayer
    kind: str
    content: dict[str, Any]
    version: str
    created_at: str = field(default_factory=now_iso)
    proposal_id: str | None = None
    reviewer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["layer"] = self.layer.value if isinstance(self.layer, KnowledgeLayer) else self.layer
        return d


@dataclass(slots=True)
class VersionRecord:
    version_id: str
    entity_id: str
    entity_type: str
    version: str
    reviewer: str
    timestamp: str
    diff: dict[str, Any]
    snapshot: dict[str, Any]
    rollback_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Aliases for approval/lab call sites
KnowledgeRecord = KnowledgeRecord
VersionRecord = VersionRecord
PatternHit = PatternHit
