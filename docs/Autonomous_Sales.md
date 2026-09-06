# Autonomous Sales AI

Autonomous Next Best Action (NBA) layer for the AI Sales Operating System.

## Purpose

Recommend the next sales action, auto-run **safe** automations, and keep rule / SOP / pricing / routing changes behind human approval.

## Inputs

- Live call / lead context: buy signal, sentiment, objection, intent
- Follow-up debt, QA score, churn risk, CLTV score

## Outputs

- NBA recommendation with confidence, rationale, evidence
- Safe automation jobs (callback reminder, follow-up message, CRM note, task, coaching nudge)
- Approval requests for restricted changes
- Dashboard widgets + quality snapshot

## Hard rules

1. Safe automations may auto-execute.
2. Restricted changes (`rule_change`, `sop_change`, `pricing_change`, `routing_policy_change`) **never** auto-apply.
3. Every recommendation / job / approval must carry evidence.

## API

- `POST /v1/autonomous/recommend`
- `POST /v1/autonomous/nba`
- `POST /v1/autonomous/automations/trigger`
- `POST /v1/autonomous/approvals/propose`
- `POST /v1/autonomous/approvals/decide`
- `GET /v1/autonomous/dashboard`
- `GET /v1/autonomous/quality`

## Dashboard widgets

- recommendation_count
- automations_run / automations_blocked
- approvals_pending / approvals_decided
- rule_changes_applied
- avg_confidence
- nba_mix

## Quality Gate

Must pass:

- recommendation_precision ≥ 0.70
- automation_safety ≥ 0.85
- approval_enforcement ≥ 0.95
- evidence_validation ≥ 0.70
- approval-only rule changes confirmed

See also: [Autonomous_Sales_AI.md](./Autonomous_Sales_AI.md) for the full Sales OS workflow.
