"""Rollback Engine for rules, models, prompts, and datasets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import ArtifactType, new_id, now_iso


@dataclass
class VersionSnapshot:
    version_id: str
    artifact_id: str
    artifact_type: str
    version: str
    payload: dict[str, Any]
    created_at: str
    created_by: str = "system"
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RollbackEngine:
    """Every production-bound change must be versioned for rollback."""

    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def snapshot(
        self,
        artifact_id: str,
        artifact_type: ArtifactType | str,
        payload: Mapping[str, Any],
        *,
        version: str | None = None,
        created_by: str = "system",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> VersionSnapshot:
        atype = artifact_type.value if isinstance(artifact_type, ArtifactType) else str(artifact_type)
        existing = self.store.list_versions(artifact_id)
        for row in existing:
            if row.get("active"):
                self.store.append_version({**row, "active": False, "superseded_at": now_iso()})

        next_ver = version or f"v{len(existing) + 1}"
        snap = VersionSnapshot(
            version_id=new_id("ver"),
            artifact_id=artifact_id,
            artifact_type=atype,
            version=next_ver,
            payload=dict(payload),
            created_at=now_iso(),
            created_by=created_by,
            active=True,
            metadata=dict(metadata or {}),
        )
        self.store.append_version(snap.to_dict())
        return snap

    def list_versions(self, artifact_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_versions(artifact_id)

    def current(self, artifact_id: str) -> dict[str, Any] | None:
        rows = [r for r in self.store.list_versions(artifact_id) if r.get("active")]
        return rows[-1] if rows else None

    def rollback(
        self,
        artifact_id: str,
        *,
        to_version_id: str | None = None,
        to_version: str | None = None,
        actor: str = "qa",
    ) -> dict[str, Any]:
        rows = self.store.list_versions(artifact_id)
        if not rows:
            raise ValueError(f"No versions for artifact: {artifact_id}")

        target: dict[str, Any] | None = None
        if to_version_id:
            target = next((r for r in rows if r.get("version_id") == to_version_id), None)
        elif to_version:
            target = next((r for r in reversed(rows) if r.get("version") == to_version), None)
        else:
            history = [r for r in rows if not r.get("active")]
            target = history[-1] if history else None

        if target is None:
            raise ValueError("Rollback target version not found")

        for row in rows:
            if row.get("active"):
                self.store.append_version({**row, "active": False, "deactivated_at": now_iso()})

        restored = VersionSnapshot(
            version_id=new_id("ver"),
            artifact_id=artifact_id,
            artifact_type=str(target.get("artifact_type") or "rule"),
            version=f"rollback-{target.get('version')}-{now_iso()[:10]}",
            payload=dict(target.get("payload") or {}),
            created_at=now_iso(),
            created_by=actor,
            active=True,
            metadata={
                "rollback_of": target.get("version_id"),
                "rollback_from_version": target.get("version"),
                "reason": "explicit_rollback",
            },
        )
        self.store.append_version(restored.to_dict())
        return {
            "rolled_back": True,
            "artifact_id": artifact_id,
            "restored": restored.to_dict(),
            "from_version": target.get("version"),
        }

    def readiness(self) -> dict[str, Any]:
        versions = self.store.list_versions()
        by_type: dict[str, int] = {}
        artifacts: set[str] = set()
        for row in versions:
            t = str(row.get("artifact_type") or "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            if row.get("artifact_id"):
                artifacts.add(str(row["artifact_id"]))
        multi = sum(1 for aid in artifacts if len(self.store.list_versions(aid)) >= 2)
        return {
            "version_count": len(versions),
            "artifact_count": len(artifacts),
            "artifacts_rollback_ready": multi,
            "by_type": by_type,
            "ready": True,
            "supports": ["rule", "model", "prompt", "dataset", "coaching", "sop"],
        }
