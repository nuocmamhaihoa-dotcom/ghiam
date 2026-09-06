"""Shared types for Evolution Engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class EvolutionPhase(str, Enum):
    DETECT = "detect"
    PROPOSE = "propose"
    SHADOW = "shadow"
    EXPERIMENT = "experiment"
    EVALUATE = "evaluate"
    QA_APPROVAL = "qa_approval"
    PRODUCTION = "production"
    MONITOR = "monitor"
    ROLLBACK = "rollback"


class ProposalKind(str, Enum):
    RULE = "rule"
    COACHING = "coaching"
    SOP = "sop"
    MEMORY = "memory"
    PROMPT = "prompt"
    MODEL = "model"
    DATASET = "dataset"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    SHADOWING = "shadowing"
    EXPERIMENTING = "experimenting"
    PENDING_QA = "pending_qa"
    APPROVED = "approved"
    REJECTED = "rejected"
    PRODUCTION = "production"
    ROLLED_BACK = "rolled_back"


class ArtifactType(str, Enum):
    RULE = "rule"
    MODEL = "model"
    PROMPT = "prompt"
    DATASET = "dataset"
    COACHING = "coaching"
    SOP = "sop"


@dataclass(slots=True)
class PipelineOutput:
    call_id: str
    score: float
    root_cause: str
    emotion: str
    buying_signal: float
    coaching: str
    revenue_leak: float
    latency_ms: float = 0.0
    model_versions: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ShadowDelta:
    call_id: str
    field: str
    production: Any
    candidate: Any
    abs_delta: float
    threshold: float
    exceeded: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvolutionProposal:
    proposal_id: str
    kind: ProposalKind
    title: str
    summary: str
    payload: dict[str, Any]
    evidence: list[dict[str, Any]]
    confidence: float
    expected_impact: str
    status: ProposalStatus = ProposalStatus.DRAFT
    created_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d


@dataclass(slots=True)
class ModelMetrics:
    model_name: str
    accuracy: float
    precision: float
    recall: float
    f1: float
    drift: float
    latency_ms: float
    sample_count: int = 0
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
