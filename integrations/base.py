"""Connector plugin contract for Sales OS integrations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class SyncResult:
    connector: str
    ok: bool
    records_in: int = 0
    records_out: int = 0
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["success"] = self.ok
        return d


class ConnectorPlugin(Protocol):
    key: str
    name: str
    kind: str  # crm | telephony | ads | messaging | productivity | calendar

    def health(self) -> dict[str, Any]:
        ...

    def sync(self, payload: dict[str, Any] | None = None) -> SyncResult:
        ...


__all__ = ["ConnectorPlugin", "SyncResult"]
