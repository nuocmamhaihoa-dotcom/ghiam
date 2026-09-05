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
            "calls:read",
            "calls:write",
            "scorecard:read",
            "scorecard:override",
            "rulebook:read",
            "rulebook:write",
            "rulebook:publish",
            "coaching:read",
            "coaching:manage",
            "revenue:read",
            "audit:read",
            "admin:users",
            "dashboard:read",
            "datasets:read",
            "datasets:write",
        }
    ),
    RoleCode.QA_LEAD: frozenset(
        {
            "calls:read",
            "calls:write",
            "scorecard:read",
            "scorecard:override",
            "rulebook:read",
            "rulebook:write",
            "rulebook:publish",
            "coaching:read",
            "coaching:manage",
            "revenue:read",
            "audit:read",
            "dashboard:read",
            "datasets:read",
            "datasets:write",
        }
    ),
    RoleCode.COACH: frozenset(
        {
            "calls:read",
            "scorecard:read",
            "rulebook:read",
            "coaching:read",
            "coaching:manage",
            "revenue:read",
            "dashboard:read",
            "datasets:read",
        }
    ),
    RoleCode.AGENT: frozenset(
        {
            "calls:read",
            "scorecard:read",
            "coaching:read",
            "dashboard:read",
        }
    ),
    RoleCode.VIEWER: frozenset(
        {
            "calls:read",
            "scorecard:read",
            "rulebook:read",
            "coaching:read",
            "revenue:read",
            "dashboard:read",
            "datasets:read",
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
