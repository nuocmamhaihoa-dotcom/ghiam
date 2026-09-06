"""Safe automation executor package for Autonomous Sales AI."""
from __future__ import annotations

from typing import Any

# Prefer autonomous canonical allow-lists when available; keep local fallback.
try:
    from autonomous.types import RESTRICTED_CHANGES as RESTRICTED_KINDS
    from autonomous.types import SAFE_AUTOMATIONS as SAFE_KINDS
    from autonomous.automation import execute_automation as _exec
    from autonomous.automation import is_restricted_change as is_restricted
    from autonomous.automation import is_safe_automation as is_safe
except Exception:  # pragma: no cover
    SAFE_KINDS = (
        "callback_reminder",
        "follow_up_message",
        "crm_note",
        "create_task",
        "coaching_nudge",
        "lead_assignment",
        "follow_up_schedule",
    )
    RESTRICTED_KINDS = (
        "rule_change",
        "sop_change",
        "pricing_change",
        "routing_policy_change",
    )

    def is_safe(kind: str) -> bool:
        return kind in SAFE_KINDS

    def is_restricted(kind: str) -> bool:
        return kind in RESTRICTED_KINDS

    def _exec(kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("autonomous automation unavailable")


def execute_safe_automation(kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _exec(kind, payload)


class AutomationExecutor:
    def execute(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return execute_safe_automation(kind, payload)
