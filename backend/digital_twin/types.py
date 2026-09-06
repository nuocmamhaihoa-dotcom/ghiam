"""Digital Twin Salesperson — core types."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CallEligibility(str, Enum):
    GOLDEN = "golden"
    QA_APPROVED = "qa_approved"
    HIGH_CONVERSION = "high_conversion"
    REJECTED = "rejected"


class TwinStatus(str, Enum):
    DRAFT = "draft"
    TRAINED = "trained"
    ACTIVE = "active"
    ARCHIVED = "archived"


ALLOWED_TRAINING_LABELS = {
    CallEligibility.GOLDEN.value,
    CallEligibility.QA_APPROVED.value,
    CallEligibility.HIGH_CONVERSION.value,
}

QUALITY_THRESHOLDS: dict[str, float] = {
    "twin_accuracy": 0.70,
    "style_consistency": 0.65,
    "coaching_quality": 0.65,
    "similarity_stability": 0.60,
    "min_training_calls": 3,
    "min_confidence": 0.55,
}


@dataclass
class SkillProfile:
    discovery: float = 0.5
    rapport: float = 0.5
    objection_handling: float = 0.5
    closing: float = 0.5
    value_building: float = 0.5
    empathy: float = 0.5
    voice_control: float = 0.5
    question_quality: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def strength_score(self) -> float:
        vals = list(self.to_dict().values())
        top = sorted(vals, reverse=True)[:3]
        return round(sum(top) / max(1, len(top)), 4)

    def weakness_score(self) -> float:
        vals = list(self.to_dict().values())
        bottom = sorted(vals)[:3]
        return round(1.0 - (sum(bottom) / max(1, len(bottom))), 4)


@dataclass
class VoicePattern:
    avg_turn_length: float = 12.0
    pause_ratio: float = 0.15
    question_rate: float = 0.25
    empathy_markers_per_call: float = 2.0
    tempo: str = "balanced"
    rhythm_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationDNA:
    question_sequence: list[str] = field(default_factory=list)
    closing_sequence: list[str] = field(default_factory=list)
    empathy_pattern: list[str] = field(default_factory=list)
    value_building_pattern: list[str] = field(default_factory=list)
    discovery_style: str = "consultative"
    closing_style: str = "soft_ask"
    objection_strategy: str = "acknowledge_reframe_offer"
    conversation_style: str = "warm_professional"
    style_templates: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DigitalTwinProfile:
    twin_id: str
    agent_id: str
    display_name: str
    skill_profile: SkillProfile
    strength_score: float
    weakness_score: float
    voice_pattern: VoicePattern
    conversation_dna: ConversationDNA
    confidence: float
    status: TwinStatus = TwinStatus.DRAFT
    training_call_ids: list[str] = field(default_factory=list)
    evidence_count: int = 0
    conversation_style: str = "warm_professional"
    discovery_style: str = "consultative"
    closing_style: str = "soft_ask"
    objection_strategy: str = "acknowledge_reframe_offer"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "twin_id": self.twin_id,
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "skill_profile": self.skill_profile.to_dict(),
            "strength_score": self.strength_score,
            "weakness_score": self.weakness_score,
            "voice_pattern": self.voice_pattern.to_dict(),
            "conversation_style": self.conversation_style,
            "discovery_style": self.discovery_style,
            "closing_style": self.closing_style,
            "objection_strategy": self.objection_strategy,
            "conversation_dna": self.conversation_dna.to_dict(),
            "confidence": self.confidence,
            "status": self.status.value if isinstance(self.status, TwinStatus) else str(self.status),
            "training_call_ids": list(self.training_call_ids),
            "evidence_count": self.evidence_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class RoleplayTurn:
    speaker: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoleplaySession:
    session_id: str
    twin_id: str
    trainee_id: str
    scenario: str
    turns: list[RoleplayTurn]
    twin_replies: list[str]
    similarity_score: float
    improvement_score: float
    coaching: list[str]
    top_differences: list[str]
    skill_gap: dict[str, float]
    created_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "twin_id": self.twin_id,
            "trainee_id": self.trainee_id,
            "scenario": self.scenario,
            "turns": [t.to_dict() for t in self.turns],
            "twin_replies": list(self.twin_replies),
            "similarity_score": self.similarity_score,
            "improvement_score": self.improvement_score,
            "coaching": list(self.coaching),
            "top_differences": list(self.top_differences),
            "skill_gap": dict(self.skill_gap),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
