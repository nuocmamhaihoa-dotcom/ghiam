"""Shared RBAC role codes and permission matrix."""

from __future__ import annotations

from enum import StrEnum


class RoleCode(StrEnum):
    ADMIN = "admin"
    QA_LEAD = "qa_lead"
    COACH = "coach"
    AGENT = "agent"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[RoleCode, frozenset[str]] = {
    RoleCode.ADMIN: frozenset(
        {
            "admin:users",
            "analytics:read",
            "appeals:read",
            "appeals:write",
            "audit:read",
            "calls:read",
            "calls:write",
            "coaching:manage",
            "coaching:read",
            "dashboard:read",
            "datasets:read",
            "datasets:write",
            "qa:calibrate",
            "qa:read",
            "revenue:read",
            "rulebook:publish",
            "rulebook:read",
            "rulebook:write",
            "scorecard:override",
            "scorecard:read",
        }
    ),
    RoleCode.QA_LEAD: frozenset(
        {
            "analytics:read",
            "appeals:read",
            "appeals:write",
            "audit:read",
            "calls:read",
            "calls:write",
            "coaching:manage",
            "coaching:read",
            "dashboard:read",
            "datasets:read",
            "datasets:write",
            "qa:calibrate",
            "qa:read",
            "revenue:read",
            "rulebook:publish",
            "rulebook:read",
            "rulebook:write",
            "scorecard:override",
            "scorecard:read",
        }
    ),
    RoleCode.COACH: frozenset(
        {
            "analytics:read",
            "appeals:read",
            "calls:read",
            "coaching:manage",
            "coaching:read",
            "dashboard:read",
            "datasets:read",
            "qa:read",
            "revenue:read",
            "rulebook:read",
            "scorecard:read",
        }
    ),
    RoleCode.AGENT: frozenset(
        {
            "analytics:read",
            "appeals:read",
            "appeals:write",
            "calls:read",
            "coaching:read",
            "dashboard:read",
            "scorecard:read",
        }
    ),
    RoleCode.VIEWER: frozenset(
        {
            "analytics:read",
            "appeals:read",
            "calls:read",
            "coaching:read",
            "dashboard:read",
            "datasets:read",
            "qa:read",
            "revenue:read",
            "rulebook:read",
            "scorecard:read",
        }
    ),
}


def permissions_for_roles(roles: list[str]) -> set[str]:
    perms: set[str] = set()
    for role in roles:
        try:
            code = RoleCode(role)
        except ValueError:
            continue
        perms |= ROLE_PERMISSIONS[code]
    return perms
