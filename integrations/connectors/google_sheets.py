"""Google Sheets connector plugin."""
from __future__ import annotations

from typing import Any

from integrations.base import ConnectorPlugin, SyncResult


class GoogleSheetsConnector:
    key = "google_sheets"
    name = "Google Sheets"
    kind = "productivity"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "connector": self.key, "mode": "plugin", "kind": self.kind}

    def sync(self, payload: dict[str, Any] | None = None) -> SyncResult:
        data = payload or {}
        records = int(data.get("records", data.get("records_in", 1)))
        dry_run = bool(data.get("dry_run", True))
        # Simulate lossless sync: out == in when ok
        return SyncResult(
            connector=self.key,
            ok=True,
            records_in=records,
            records_out=records,
            message=f"{self.name} sync ok",
            details={"kind": self.kind, "dry_run": dry_run},
            audit=[{"event": "sync", "connector": self.key, "records": records, "dry_run": dry_run}],
        )


__all__ = ["GoogleSheetsConnector"]
