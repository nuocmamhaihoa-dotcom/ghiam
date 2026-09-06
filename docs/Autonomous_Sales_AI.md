# Autonomous Sales AI

AI Sales Operating System — self-driving sales layer. Humans approve; AI proposes and executes only safe actions.

## Mission

AI autonomously:

1. Scores and assigns leads
2. Recommends call / channel strategy (Next Best Action)
3. Runs live coaching nudges
4. Schedules follow-ups
5. Detects revenue leaks
6. Proposes new rules / SOPs
7. Routes learning into QA approval — **never silent rulebook mutation**

## Architecture

```
backend/autonomous/      # Orchestrator (workflow, dashboard, approvals)
backend/automation/      # Safe automation executor package
backend/recommendation/  # Next Best Action package
```

## Workflow

```
Lead → Score → Assignment → Call Strategy → Live Coaching
  → Follow-up → Revenue Analysis → Self Learning
  → QA Approval → Knowledge Update
```

## Next Best Action

Decisions:

| Action | Meaning |
|--------|---------|
| `call_now` | Gọi ngay |
| `callback` | Gọi lại |
| `zalo_message` | Nhắn Zalo |
| `email` | Gửi Email |
| `reassign_sale` | Chuyển Sale |
| `escalate_leader` | Chuyển Leader |
| `close_lead` | Đóng Lead |
| `send_proposal` | Gửi đề xuất |
| `coaching_nudge` | Coaching |

Every decision includes:

- **Confidence**
- **Evidence**
- **Expected Revenue Impact**

## Automation safety

**Safe (auto-run):** callback reminder, follow-up message, CRM note, task, coaching nudge, lead assignment, follow-up schedule.

**Restricted (proposal only):** rule_change, sop_change, pricing_change, routing_policy_change.

## Self Learning

Connects Memory Graph + Self Learning Lab + QA Approval.

- Emits **proposals** only
- Applies knowledge update **only after human approval**
- Never auto-edits the Rulebook

## CEO Dashboard

Widgets:

- Autonomous Actions
- Revenue Impact
- AI Accuracy
- Approval Queue
- Knowledge Growth
- Forecast
- Risk
- (+ NBA mix, automation counters, workflow counts)

## API

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/autonomous/assign` | Lead assignment |
| `POST /v1/autonomous/recommend` | NBA only |
| `POST /v1/autonomous/nba` | NBA + safe automation |
| `POST /v1/autonomous/follow-up` | Follow-up schedule |
| `POST /v1/autonomous/automations/trigger` | Safe automation |
| `POST /v1/autonomous/workflow` | Full OS workflow |
| `POST /v1/autonomous/approvals/propose` | Rule/SOP proposal |
| `POST /v1/autonomous/approvals/decide` | Human approval |
| `POST /v1/autonomous/learning/propose` | Self-learning proposal |
| `GET /v1/autonomous/dashboard` | CEO dashboard |
| `GET /v1/autonomous/quality` | Quality snapshot |

## Quality Gate

Must pass:

- Workflow Integrity ≥ 0.90
- Automation Safety ≥ 0.85
- Approval Enforcement ≥ 0.95
- Revenue Forecast ≥ 0.70
- Memory Consistency ≥ 0.90
- Rule Safety ≥ 0.95
- Recommendation Precision ≥ 0.70
- Evidence Validation ≥ 0.70
