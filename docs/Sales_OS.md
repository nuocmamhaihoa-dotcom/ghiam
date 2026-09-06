# AI Sales Operating System (Sales OS)

Sales OS is the central orchestration layer connecting CRM, telephony, messaging, and AI decision engines for enterprise telesales.

## Architecture

```
enterprise-web (/sales-os)
        │
        ▼
FastAPI /v1/sales-os/*
        │
        ▼
backend/sales_os (SalesOS façade)
   ├── integrations/   plugin connectors
   ├── routing/        AI lead routing + Next Best Action
   ├── forecast/       close-rate & revenue forecast
   └── automation/     callback / follow-up / CRM / task / coaching
```

## Plugin Connectors (13)

| Key | System | Kind |
|-----|--------|------|
| hubspot | HubSpot CRM | crm |
| salesforce | Salesforce CRM | crm |
| bitrix24 | Bitrix24 CRM | crm |
| zoho | Zoho CRM | crm |
| asterisk | Asterisk PBX | telephony |
| threecx | 3CX | telephony |
| callio | Callio | telephony |
| stringee | Stringee | telephony |
| twilio | Twilio | telephony |
| facebook_lead_ads | Facebook Lead Ads | ads |
| zalo_oa | Zalo OA | messaging |
| google_sheets | Google Sheets | productivity |
| google_calendar | Google Calendar | calendar |

Connectors implement `ConnectorPlugin` (`key`, `name`, `kind`, `health()`, `sync()`). Register via `integrations.registry.load_builtin_connectors`.

## AI Lead Routing

Inputs per agent: telesale skill, Conversation DNA, close rate, industry experience, peak hours, workload.

Outputs:

- **Lead Score** (0–100)
- **Assignment Reason** (explainable factor breakdown)
- **Success Probability** (0–1)

## Next Best Action

After each call, decide one of:

- `callback` · `zalo_message` · `email` · `escalate_leader` · `reassign_sale` · `close_lead`

Each decision includes **confidence**, **evidence**, **expected_impact** (and optional `schedule_at`).

## Sales Forecast

Produces:

- Close rate
- Weekly / monthly / quarterly revenue
- Pipeline risk
- Confidence + evidence

## Automation Engine

Jobs: `callback_reminder`, `follow_up`, `crm_update`, `create_task`, `create_coaching`.

NBA decisions can fan out into automation jobs via `run_nba_pipeline`.

## Enterprise Dashboard Roles

CEO · Sales Director · Team Leader · QA · Telesale

Widgets: Revenue Forecast, Lead Health, Revenue Leak, Conversion Funnel, Personality Distribution, Coaching Progress, Team Ranking, AI Confidence, Golden Call Gap, Repeat Mistake.

## API (`/v1/sales-os`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/connectors` | List plugins |
| POST | `/lead-score` | Score a lead |
| POST | `/route` | Route lead → agent |
| POST | `/next-best-action` | Post-call NBA |
| POST | `/forecast` | Revenue forecast |
| POST | `/crm-sync` | Sync one connector |
| POST | `/sync-all` | Sync all connectors |
| POST | `/calendar-sync` | Calendar sync |
| POST | `/call-schedule` | Schedule call |
| POST | `/automation/trigger` | Trigger automation |
| POST | `/dashboard` | Role dashboard |
| GET | `/quality` | Quality snapshot |
| GET | `/audit` | Audit log |

## Quality Gate

Before release, Sales OS must prove:

1. CRM sync succeeds for all connectors
2. No data loss (`records_in == records_out`)
3. Routing follows skill/DNA/close/industry/peak rules
4. Forecast remains stable / bounded
5. Dashboard widgets sync per role
6. Automation jobs complete
7. APIs are registered and callable
8. Audit log captures orchestration events

## Tests

`tests/sales_os/` contains **5000+** parametric cases covering CRM sync, lead routing, forecast accuracy, automation, calendar, and multi-user permissions.
