"""Automation package — legacy job engine + safe Autonomous Sales executor."""
from __future__ import annotations

from automation.engine import AutomationEngine, AutomationJob
from automation.executor import AutomationExecutor, execute_safe_automation, is_restricted, is_safe

__all__ = [
    "AutomationEngine",
    "AutomationJob",
    "AutomationExecutor",
    "execute_safe_automation",
    "is_restricted",
    "is_safe",
]
