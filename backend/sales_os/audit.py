"""Sales OS audit log."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class AuditEntry:
    id: str
    action: str
    actor: str
    resource: str
    details: dict[str, Any] = field(default_factory=dict)
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        action: str,
        *,
        actor: str = "system",
        resource: str = "",
        details: dict | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            id=f"aud_{uuid4().hex[:10]}",
            action=action,
            actor=actor,
            resource=resource,
            details=details or {},
            at=datetime.now(timezone.utc).isoformat(),
        )
        self._entries.append(entry)
        return entry

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["AuditLog", "AuditEntry"]
