"""Shared types for Audio Intelligence Engine V2."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
ARCHIVE_EXTS = {".zip", ".rar"}
SUPPORTED_EXTS = AUDIO_EXTS | VIDEO_EXTS | ARCHIVE_EXTS

PIPELINE_STAGES = (
    "upload",
    "validation",
    "audio_repair",
    "noise_removal",
    "voice_separation",
    "speaker_diarization",
    "transcript",
    "evidence_extraction",
    "quality_score",
    "ai_analysis",
)

CALL_STAGES = (
    "opening",
    "rapport",
    "discovery",
    "qualification",
    "presentation",
    "pricing",
    "objection",
    "closing",
    "follow_up",
)

QUALITY_THRESHOLDS: dict[str, float] = {
    "audio_quality_min": 55.0,
    "transcript_confidence_min": 0.55,
    "speaker_confidence_min": 0.55,
    "timestamp_coverage_min": 0.90,
    "evidence_integrity_min": 0.90,
}

DIALECTS = ("north", "central", "south", "unknown")
CUSTOMER_EMOTIONS = ("interested", "trust", "hesitation", "frustration", "exit_risk", "neutral")
AGENT_EMOTIONS = ("confidence", "rush", "calm", "stress", "neutral")

INTERRUPT_KINDS = (
    "helpful_interruption",
    "harmful_interruption",
    "cooperative_overlap",
    "cutting_off",
)
SILENCE_KINDS = ("thinking", "searching", "confusion", "connection_issue")

BUYING_SIGNAL_PATTERNS = (
    ("delivery", ("bao giờ giao", "giao khi nào", "ship khi nào", "nhận hàng", "bao lâu thì nhận")),
    ("payment", ("thanh toán sao", "chuyển khoản", "trả góp", "cọc", "thanh toán thế nào")),
    ("warranty", ("bảo hành", "bảo hành bao lâu", "đổi trả", "bảo hành mấy tháng")),
    ("invoice", ("hóa đơn", "xuất hóa đơn", "vat", "hđ", "có hóa đơn")),
)

OBJECTION_CONTEXTS = (
    ("price", ("đắt", "cao quá", "giảm giá", "rẻ hơn", "giá hơi cao", "hơi cao")),
    ("trust", ("tin được không", "lừa", "uy tín", "review", "có thật không")),
    ("competitor", ("bên kia", "đối thủ", "so với", "chỗ khác", "bên khác")),
    ("authority", ("hỏi vợ", "hỏi sếp", "quyết định", "bàn với", "hỏi lại")),
    ("delay", ("để sau", "suy nghĩ", "mai tính", "chưa cần", "để anh suy nghĩ")),
)

NEXT_STEPS = {
    "delivery": "Confirm delivery window and create order hold",
    "payment": "Offer payment options and send payment link",
    "warranty": "Explain warranty policy with proof points",
    "invoice": "Confirm invoice type (VAT/personal) and timing",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class FileMeta:
    file_id: str
    filename: str
    extension: str
    media_type: str
    size_bytes: int = 0
    duration_sec: float = 0.0
    codec: str = "unknown"
    sample_rate: int = 0
    bit_rate: int = 0
    channels: int = 0
    language: str = "vi"
    integrity_ok: bool = True
    integrity_issues: list[str] = field(default_factory=list)
    folder_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    score: float
    noise: float = 0.0
    echo: float = 0.0
    distortion: float = 0.0
    clipping: float = 0.0
    silence_ratio: float = 0.0
    low_volume: float = 0.0
    overlap_ratio: float = 0.0
    repair_recommended: bool = False
    blockers: list[str] = field(default_factory=list)
    passed_gate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptLine:
    speaker: str
    start_sec: float
    end_sec: float
    confidence: float
    original_text: str
    normalized_text: str
    dialect: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    claim: str
    timestamp_start: float
    timestamp_end: float
    speaker: str
    transcript: str
    rule_id: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
