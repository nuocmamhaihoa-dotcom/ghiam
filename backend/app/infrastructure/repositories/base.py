"""Repository protocols (interfaces)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.entities import (
    CallEntity,
    EvidenceEntity,
    GoldenCallEntity,
    RuleEntity,
    RuleVersionEntity,
    TranscriptEntity,
    UserEntity,
)
from app.domain.value_objects import ScoringResponse


class UserRepository(Protocol):
    async def get_by_email(self, email: str) -> UserEntity | None: ...
    async def get_by_id(self, user_id: UUID) -> UserEntity | None: ...
    async def create(
        self,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        role_codes: list[str],
        tenant_id: UUID | None = None,
    ) -> UserEntity: ...
    async def update_last_login(self, user_id: UUID, when: datetime) -> None: ...
    async def ensure_roles(self, codes: list[tuple[str, str]]) -> None: ...


class CallRepository(Protocol):
    async def create(self, call: CallEntity) -> CallEntity: ...
    async def get(self, call_id: UUID) -> CallEntity | None: ...
    async def get_by_external(
        self, tenant_id: UUID | None, external_call_id: str
    ) -> CallEntity | None: ...
    async def list(
        self,
        *,
        agent_user_id: UUID | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CallEntity]: ...
    async def update_status(self, call_id: UUID, status: str) -> None: ...
    async def save_transcript(self, transcript: TranscriptEntity) -> TranscriptEntity: ...
    async def get_transcript(self, call_id: UUID) -> TranscriptEntity | None: ...
    async def set_audio_key(self, call_id: UUID, s3_key: str) -> None: ...


class EvidenceRepository(Protocol):
    async def list_for_call(self, call_id: UUID) -> list[EvidenceEntity]: ...
    async def bulk_create(self, items: list[EvidenceEntity]) -> list[EvidenceEntity]: ...


class RuleRepository(Protocol):
    async def list(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RuleEntity]: ...
    async def get_by_code(self, rule_code: str) -> RuleEntity | None: ...
    async def get(self, rule_id: UUID) -> RuleEntity | None: ...
    async def create(self, rule: RuleEntity, created_by: UUID | None) -> RuleEntity: ...
    async def update(self, rule: RuleEntity, created_by: UUID | None, change_note: str | None) -> RuleEntity: ...
    async def create_version(
        self, rule_id: UUID, snapshot: dict[str, Any], created_by: UUID | None, change_note: str | None
    ) -> RuleVersionEntity: ...
    async def list_active(self) -> list[RuleEntity]: ...
    async def count(self, *, status: str | None = None) -> int: ...
    async def upsert_from_seed(self, payload: dict[str, Any]) -> RuleEntity: ...


class ScoreRepository(Protocol):
    async def save_scoring(
        self, call_id: UUID, response: ScoringResponse
    ) -> UUID: ...
    async def get_latest_for_call(self, call_id: UUID) -> dict[str, Any] | None: ...


class RootCauseRepository(Protocol):
    async def save(self, call_id: UUID, score_id: UUID | None, payload: dict[str, Any]) -> UUID: ...
    async def get_latest(self, call_id: UUID) -> dict[str, Any] | None: ...


class CoachingRepository(Protocol):
    async def save_for_call(
        self, call_id: UUID, agent_user_id: UUID | None, payload: dict[str, Any]
    ) -> UUID: ...
    async def list_for_agent(self, agent_user_id: UUID, limit: int = 20) -> list[dict[str, Any]]: ...
    async def get(self, plan_id: UUID) -> dict[str, Any] | None: ...


class RevenueLeakRepository(Protocol):
    async def save(self, call_id: UUID, score_id: UUID | None, payload: dict[str, Any]) -> UUID: ...
    async def get_latest(self, call_id: UUID) -> dict[str, Any] | None: ...


class AuditRepository(Protocol):
    async def record(
        self,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class GoldenCallRepository(Protocol):
    async def list(self, limit: int = 50, offset: int = 0) -> list[GoldenCallEntity]: ...
    async def create(self, entity: GoldenCallEntity) -> GoldenCallEntity: ...
    async def get(self, golden_id: UUID) -> GoldenCallEntity | None: ...
