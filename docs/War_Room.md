# War Room AI

Live operations command center for the AI Sales Operating System.

## Purpose

Give supervisors a realtime floor view: active calls, queue health, conversion pressure, and actionable alerts with evidence.

## Inputs

- Live agent presence (status, active calls, idle time)
- Live call telemetry (sentiment, objection, buy signal)
- Floor KPI (conversion vs baseline, queue size, wait time)

## Outputs

- Snapshot widgets: active calls, online agents, queue, wait, conversion, open/critical alerts, avg buy signal
- Alert stream with severity, evidence, and recommended action
- Websocket fanout for realtime dashboard updates
- Quality snapshot (alert precision, freshness, coverage, evidence validation)

## Alert kinds

- `conversion_drop`
- `sla_breach`
- `queue_overflow`
- `objection_spike`
- `churn_risk`
- `idle_agent`

## API

- `POST /v1/war-room/agents`
- `POST /v1/war-room/calls`
- `POST /v1/war-room/kpi`
- `POST /v1/war-room/alerts/scan`
- `POST /v1/war-room/alerts/ack`
- `GET /v1/war-room/snapshot`
- `GET /v1/war-room/dashboard`
- `GET /v1/war-room/quality`
- `WS /v1/war-room/ws`

## Quality Gate

Must pass:

- Alert precision ≥ 0.70
- Freshness ≥ 0.70
- Coverage ≥ 0.65
- Evidence validation ≥ 0.70
- Realtime enabled
