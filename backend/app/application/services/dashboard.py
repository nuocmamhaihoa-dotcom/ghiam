"""Dashboard aggregation: employee scores, heatmap, KPI, root cause, revenue leak."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    CallModel,
    CoachingPlanModel,
    RevenueLeakModel,
    RootCauseModel,
    ScoreModel,
    UserModel,
    ViolationModel,
)

STAGE_KEYS = ("opening", "discovery", "pitch", "objection", "close", "outro")


def _is_insufficient_evidence(result: str | None) -> bool:
    value = (result or "").lower().replace(" ", "_")
    return value in {"insufficient_evidence"}


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def overview(
        self,
        *,
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        window_to = window_to or now
        window_from = window_from or (window_to - timedelta(days=7))

        calls = list(
            (
                await self._session.execute(
                    select(CallModel).where(
                        CallModel.created_at >= window_from,
                        CallModel.created_at <= window_to,
                    )
                )
            ).scalars()
        )
        call_ids = [call.id for call in calls]
        calls_total = len(calls)

        scores: list[ScoreModel] = []
        if call_ids:
            scores = list(
                (
                    await self._session.execute(
                        select(ScoreModel).where(ScoreModel.call_id.in_(call_ids))
                    )
                ).scalars()
            )

        latest_by_call: dict[UUID, ScoreModel] = {}
        for score in sorted(scores, key=lambda row: row.evaluated_at):
            latest_by_call[score.call_id] = score
        scored_rows = list(latest_by_call.values())
        scored = len(scored_rows)
        insufficient_evidence = sum(
            1 for row in scored_rows if _is_insufficient_evidence(row.result)
        )
        failed = sum(
            1
            for row in scored_rows
            if (row.result or "").lower() in {"fail", "failed", "auto_fail"}
        )
        avg_score = (
            round(sum(float(row.overall_score) for row in scored_rows) / scored, 2)
            if scored
            else 0.0
        )
        auto_fail_rate = (
            round(sum(1 for row in scored_rows if row.auto_fail_triggered) / scored, 4)
            if scored
            else 0.0
        )

        leaks: list[RevenueLeakModel] = []
        if call_ids:
            leaks = list(
                (
                    await self._session.execute(
                        select(RevenueLeakModel).where(RevenueLeakModel.call_id.in_(call_ids))
                    )
                ).scalars()
            )

        estimated_revenue_leak_vnd = 0.0
        ie_leak_calls = 0
        for leak in leaks:
            if "insufficient" in (leak.verdict or "").lower():
                ie_leak_calls += 1
                continue
            if leak.estimated_amount is not None:
                estimated_revenue_leak_vnd += float(leak.estimated_amount)

        violations: list[ViolationModel] = []
        if call_ids:
            violations = list(
                (
                    await self._session.execute(
                        select(ViolationModel).where(ViolationModel.call_id.in_(call_ids))
                    )
                ).scalars()
            )
        fail_counter: Counter[str] = Counter()
        title_by_rule: dict[str, str] = {}
        for violation in violations:
            if (violation.verdict or "").lower() != "fail":
                continue
            fail_counter[violation.rule_code] += 1
            title_by_rule[violation.rule_code] = violation.title
        top_failed_rules = [
            {
                "rule_code": code,
                "title": title_by_rule.get(code, code),
                "fail_count": count,
            }
            for code, count in fail_counter.most_common(10)
        ]

        root_rows: list[RootCauseModel] = []
        if call_ids:
            root_rows = list(
                (
                    await self._session.execute(
                        select(RootCauseModel).where(RootCauseModel.call_id.in_(call_ids))
                    )
                ).scalars()
            )
        cause_counter: Counter[str] = Counter()
        label_by_cause: dict[str, str] = {}
        for row in root_rows:
            code = row.primary_cause_code
            if not code:
                continue
            cause_counter[code] += 1
            label_by_cause[code] = code
            for cause in row.causes or []:
                if isinstance(cause, dict) and cause.get("code") == code:
                    label_by_cause[code] = str(cause.get("label") or code)
        top_root_causes = [
            {
                "cause_code": code,
                "label": label_by_cause.get(code, code),
                "count": count,
            }
            for code, count in cause_counter.most_common(10)
        ]

        agent_ids = {call.agent_user_id for call in calls if call.agent_user_id}
        users: dict[UUID, UserModel] = {}
        if agent_ids:
            for user in (
                await self._session.execute(select(UserModel).where(UserModel.id.in_(agent_ids)))
            ).scalars():
                users[user.id] = user

        call_agent = {call.id: call.agent_user_id for call in calls}
        by_agent_scores: dict[UUID, list[ScoreModel]] = defaultdict(list)
        for score in scored_rows:
            agent_id = call_agent.get(score.call_id)
            if agent_id:
                by_agent_scores[agent_id].append(score)

        leak_by_call = {leak.call_id: leak for leak in leaks}
        employee_scores: list[dict[str, Any]] = []
        stage_heatmap: list[dict[str, Any]] = []
        for agent_id, agent_scores in by_agent_scores.items():
            user = users.get(agent_id)
            name = user.full_name if user else str(agent_id)
            leak_vnd = 0.0
            ie_count = 0
            for score in agent_scores:
                leak = leak_by_call.get(score.call_id)
                if (
                    leak
                    and leak.estimated_amount is not None
                    and "insufficient" not in (leak.verdict or "").lower()
                ):
                    leak_vnd += float(leak.estimated_amount)
                if _is_insufficient_evidence(score.result):
                    ie_count += 1
            count = len(agent_scores)
            employee_scores.append(
                {
                    "user_id": str(agent_id),
                    "full_name": name,
                    "team_name": "Unassigned",
                    "avg_score": round(
                        sum(float(score.overall_score) for score in agent_scores) / count,
                        2,
                    ),
                    "calls": count,
                    "leak_vnd": round(leak_vnd, 2),
                    "ie_rate": round(ie_count / count, 4) if count else 0.0,
                }
            )
            stage_buckets: dict[str, list[float]] = defaultdict(list)
            for score in agent_scores:
                for key, value in (score.stage_scores or {}).items():
                    try:
                        stage_buckets[str(key)].append(float(value))
                    except (TypeError, ValueError):
                        continue
            stages = {
                key: (
                    round(sum(stage_buckets[key]) / len(stage_buckets[key]), 2)
                    if stage_buckets.get(key)
                    else None
                )
                for key in STAGE_KEYS
            }
            stage_heatmap.append({"agent_name": name, "stages": stages})

        employee_scores.sort(key=lambda row: row["avg_score"], reverse=True)

        open_coaching = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(CoachingPlanModel)
                    .where(CoachingPlanModel.status.in_(["active", "open", "in_progress"]))
                )
            ).scalar_one()
        )

        ie_rate = round(insufficient_evidence / scored, 4) if scored else 0.0
        kpis = [
            {
                "key": "avg_score",
                "label": "Điểm TB",
                "value": avg_score,
                "unit": "điểm",
                "delta_pct": 0.0,
            },
            {
                "key": "auto_fail_rate",
                "label": "Auto-fail",
                "value": auto_fail_rate,
                "unit": "%",
                "delta_pct": 0.0,
            },
            {
                "key": "ie_rate",
                "label": "Tỷ lệ thiếu BC",
                "value": ie_rate,
                "unit": "%",
                "delta_pct": 0.0,
            },
            {
                "key": "revenue_leak",
                "label": "Revenue leak",
                "value": round(estimated_revenue_leak_vnd, 0),
                "unit": "VND",
                "delta_pct": 0.0,
            },
            {
                "key": "coaching_open",
                "label": "Coaching mở",
                "value": open_coaching,
                "unit": "case",
                "delta_pct": 0.0,
            },
        ]

        return {
            "window": {"from": window_from.isoformat(), "to": window_to.isoformat()},
            "calls_total": calls_total,
            "scored": scored,
            "insufficient_evidence": insufficient_evidence,
            "failed": failed,
            "avg_score": avg_score,
            "auto_fail_rate": auto_fail_rate,
            "estimated_revenue_leak_vnd": round(estimated_revenue_leak_vnd, 2),
            "ie_leak_calls": ie_leak_calls,
            "top_failed_rules": top_failed_rules,
            "top_root_causes": top_root_causes,
            "employee_scores": employee_scores,
            "kpis": kpis,
            "stage_heatmap": stage_heatmap,
        }

    async def revenue_leak_summary(
        self,
        *,
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> dict[str, Any]:
        overview = await self.overview(window_from=window_from, window_to=window_to)
        now = datetime.now(timezone.utc)
        window_to = window_to or now
        window_from = window_from or (window_to - timedelta(days=7))
        call_ids = [
            call.id
            for call in (
                await self._session.execute(
                    select(CallModel).where(
                        CallModel.created_at >= window_from,
                        CallModel.created_at <= window_to,
                    )
                )
            ).scalars()
        ]
        components: Counter[str] = Counter()
        amounts: dict[str, float] = defaultdict(float)
        if call_ids:
            for leak in (
                await self._session.execute(
                    select(RevenueLeakModel).where(RevenueLeakModel.call_id.in_(call_ids))
                )
            ).scalars():
                if "insufficient" in (leak.verdict or "").lower():
                    continue
                for component in leak.components or []:
                    if not isinstance(component, dict):
                        continue
                    code = str(
                        component.get("cause_code")
                        or component.get("code")
                        or "UNKNOWN"
                    )
                    amount = float(
                        component.get("amount")
                        or component.get("estimated_amount")
                        or 0
                    )
                    components[code] += 1
                    amounts[code] += amount
        top_components = [
            {
                "cause_code": code,
                "amount": round(amounts[code], 2),
                "call_count": components[code],
            }
            for code, _ in components.most_common(10)
        ]
        if not top_components:
            divisor = max(1, len(overview["top_root_causes"]))
            top_components = [
                {
                    "cause_code": cause["cause_code"],
                    "amount": round(overview["estimated_revenue_leak_vnd"] / divisor, 2),
                    "call_count": cause["count"],
                }
                for cause in overview["top_root_causes"][:5]
            ]
        return {
            "estimated_total": overview["estimated_revenue_leak_vnd"],
            "currency": "VND",
            "insufficient_evidence_calls": overview["ie_leak_calls"],
            "top_components": top_components,
        }
