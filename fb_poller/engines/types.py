"""Shared result types. Engines must not import each other."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublicComment:
    text: str
    author_name: str | None = None
    comment_id: str | None = None
    created_time: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "author_name": self.author_name,
            "comment_id": self.comment_id,
            "created_time": self.created_time,
        }


@dataclass
class EngineResult:
    engine: str
    rank: int
    ok: bool
    comments: list[PublicComment] = field(default_factory=list)
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "rank": self.rank,
            "ok": self.ok,
            "count": len(self.comments),
            "error": self.error,
            "note": self.note,
            "comments": [c.as_dict() for c in self.comments],
        }
