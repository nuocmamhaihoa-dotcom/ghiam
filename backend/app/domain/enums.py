"""Domain enums."""

from enum import StrEnum


class CallStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    SCORED = "scored"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    DISPUTED = "disputed"
    FINALIZED = "finalized"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "Insufficient Evidence"


class RuleSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


class ScoreResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_EVIDENCE = "Insufficient Evidence"


class RootCauseVerdict(StrEnum):
    IDENTIFIED = "identified"
    INSUFFICIENT_EVIDENCE = "Insufficient Evidence"
    NONE = "none"


class RevenueLeakVerdict(StrEnum):
    ESTIMATED = "estimated"
    INSUFFICIENT_EVIDENCE = "Insufficient Evidence"
    NONE = "none"


class Speaker(StrEnum):
    AGENT = "agent"
    CUSTOMER = "customer"
    UNKNOWN = "unknown"


class StageKey(StrEnum):
    OPENING = "opening"
    DISCOVERY = "discovery"
    PITCH = "pitch"
    OBJECTION = "objection"
    CLOSING = "closing"
    COMPLIANCE = "compliance"
    SOFT_SKILLS = "soft_skills"
    OTHER = "other"
