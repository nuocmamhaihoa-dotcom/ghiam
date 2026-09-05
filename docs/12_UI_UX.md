# 12 — UI / UX Design (Enterprise Portals)

| Field | Value |
|-------|-------|
| Document ID | `DOC-UX-12-UI` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Stack | Next.js + TypeScript + TailwindCSS |
| Owners | Frontend Lead, QA Director, Product |
| Related | VCIE timelines, SOP, Security RBAC |

---

## 1. Product surfaces

| App route group | Audience | Base path |
|-----------------|----------|-----------|
| Executive / Manager Dashboard | COO, Sales managers | `/app/dashboard` |
| QA Workbench | QA analysts / managers | `/app/qa` |
| Admin Console | Tenant admins, rule authors | `/app/admin` |
| Employee Portal | Agents / telesales | `/app/me` |

Shell: common auth gate, tenant switcher (if multi-tenant user), environment badge (`staging` only).

**Rule:** UI never embeds pass/fail business thresholds; it renders `AnalysisResult` from API.

---

## 2. Design principles

1. **Evidence first** — every score cell opens evidence drawer with audio scrub + quote + timestamps.
2. **Insufficient Evidence is first-class** — amber badge, never silent zero.
3. **One job per view** — avoid dashboard-as-junk-drawer; progressive disclosure.
4. **Accessible** — WCAG 2.2 AA; keyboard audio transport; screen-reader labels for charts.
5. **Dense but calm** — enterprise data density with clear typography hierarchy (not consumer marketing landing patterns inside app).
6. **Auditability** — who changed what visible on Admin entities.

Visual tokens (CSS variables in `frontend/styles/tokens.css`): `--color-bg`, `--color-surface`, `--color-ink`, `--color-accent`, `--color-danger`, `--color-warning-ie` (Insufficient Evidence), `--color-success`. Charts use a fixed qualitative palette independent of marketing gradients.

---

## 3. Standard result panel component

`AnalysisResultPanel` is shared across Dashboard drill-down, QA, Employee:

| Block | Binding |
|-------|---------|
| Overall score | `score` (or IE state if null) |
| Stage radar / bars | `stage_scores` |
| Violations table | `violations[]` |
| Evidence list + player | `evidence[]` |
| Root cause card | `root_cause` |
| Coaching list | `coaching.tips[]` |
| Revenue leak | `revenue_leak` |

Empty/IE rendering rules encoded in component, not ad-hoc pages.

---

## 4. Manager Dashboard (`/app/dashboard`)

### 4.1 Information architecture

| Section | Purpose | Widgets |
|---------|---------|---------|
| Overview | Health of sales quality | Score trend, IE rate, appeal backlog |
| Revenue leak | Money at risk | Leak by code, by team, by industry |
| Heatmaps | Where calls fail | Stage × team heatmap; hour-of-day heatmap |
| Emotion | Soft-skill risk | Aggregate frustration peaks |
| KPI | Operational | WPM band compliance (from features), talk ratio, silence |
| Root cause | Systemic issues | Pareto of `root_cause.primary_code` |
| Coaching | Enablement | Tip completion / drill assignment rates |

### 4.2 Heatmaps

**Stage × Agent heatmap**

- Rows: agents; columns: stages; cell: avg stage score (7d).
- Click → filtered call list → AnalysisResultPanel.
- Cells with >30% IE rules show hatch pattern + tooltip “Insufficient Evidence rate”.

**Emotion heatmap**

- X: call progress 0–100%; Y: team; color: mean valence.
- Uses VCIE emotion timeline aggregates API:  
  `GET /api/v1/analytics/emotion-heatmap?from=&to=&team_id=`

**Violation heatmap**

- Rule_id frequency × team.

### 4.3 Revenue leak view

- KPI cards: estimated loss VND (sum of `revenue_leak.estimated_loss_vnd` where status=ok).
- Table: leak_code, count, avg probability, top evidence deep link.
- Exclude IE rows from monetary sum; show count of IE separately.

### 4.4 KPI strip (configurable via Admin, stored DB)

Examples of KPI keys (values from API): `avg_score`, `close_rate_proxy`, `objection_handle_rate`, `silence_p95`, `interrupt_rate`, `ie_rate`, `appeal_open`.

### 4.5 Root cause explorer

- Pareto chart of primary_code.
- Drill to calls; show contributing_factors chips (read-only from API).

### 4.6 Routes

| Path | View |
|------|------|
| `/app/dashboard` | Overview |
| `/app/dashboard/heatmaps` | Heatmaps |
| `/app/dashboard/revenue-leak` | Leak |
| `/app/dashboard/root-causes` | Pareto |
| `/app/dashboard/coaching` | Org coaching |
| `/app/dashboard/calls/[callId]` | Call deep dive |

RBAC: `manager`, `qa_manager`, `admin`, `executive`.

---

## 5. QA Workbench (`/app/qa`)

### 5.1 Queues

| Queue | Filter |
|-------|--------|
| Calibration sample | flagged `calibration=true` |
| Low confidence | model confidence < threshold **from DB config** |
| Compliance risk | industry risk_tier high + any compliance violation |
| Appeals | `appeal_open` / `under_review` |
| IE heavy | IE rule count ≥ N (config) |

### 5.2 Call review UI

Layout (3 panes):

1. **Audio + transcript** — synced highlight by `audio_ts_*`; dialect tag; intent icons.
2. **Scorecard** — AnalysisResultPanel; toggle rulebook version.
3. **QA actions** — accept, add annotation, open appeal decision, request reanalyze.

Emotion timeline drawn under waveform; silence regions shaded; interrupt markers as vertical ticks.

### 5.3 Appeal mode UI

- Side-by-side: current score vs proposed human adjustment.
- Must attach evidence spans (UI enforces).
- Decision reasons enum from DB `appeal_reason_codes`.
- Timeline of audit events.

### 5.4 Routes

| Path | View |
|------|------|
| `/app/qa` | Queue home |
| `/app/qa/review/[callId]` | Review |
| `/app/qa/appeals` | Appeal list |
| `/app/qa/appeals/[id]` | Decision |
| `/app/qa/calibration` | Gold comparison |

RBAC: `qa_analyst` (review/annotate), `qa_manager` (decide overturn).

---

## 6. Admin Console (`/app/admin`)

### 6.1 Modules

| Module | Path | Capabilities |
|--------|------|--------------|
| Users & RBAC | `/app/admin/users` | Invite, roles, disable |
| Industry packs | `/app/admin/industries` | Enable 50 SOPs |
| SOP editor | `/app/admin/sop/[sopId]` | Stages, bindings, publish |
| Rulebook editor | `/app/admin/rulebook` | DSL editor + dry-run |
| Tenant economics | `/app/admin/economics` | avg_deal_value for leak |
| Integrations | `/app/admin/integrations` | CRM, PBX, S3 |
| Audit log | `/app/admin/audit` | Search immutable logs |
| Feature flags | `/app/admin/flags` | Non-rule flags only |

### 6.2 SOP editor UX

- Visual stage list with weights (sum validation = 1).
- Bind rules via search; show rule description; dry-run on sample call.
- Publish modal: diff checksum, requires typed confirm `PUBLISH`.
- Never allows saving hard-coded score constants into frontend — values POST to API.

### 6.3 Rule dry-run

`POST /api/v1/rulebook/dry-run` with `call_id` + draft rules → returns AnalysisResult **without persist**; banner “Dry-run — not audited as production score”.

---

## 7. Employee Portal (`/app/me`)

### 7.1 Goals

Self-serve coaching without exposing other agents’ PII beyond policy.

| View | Path | Content |
|------|------|---------|
| My performance | `/app/me` | Personal score trend, IE rate |
| My calls | `/app/me/calls` | Own calls only |
| Call detail | `/app/me/calls/[id]` | AnalysisResultPanel + player |
| Coaching plan | `/app/me/coaching` | Assigned tips/drills |
| Appeals | `/app/me/appeals` | Open/history |
| Practice | `/app/me/drills/[drillId]` | Script practice (no live customer audio) |

### 7.2 Coaching UX

- Tip cards from `coaching.tips` with “Mark practiced”.
- Show linked violation evidence for why tip assigned.
- Gamification optional via config; default off for enterprise tenants.

### 7.3 Privacy

- Employees cannot see revenue_leak monetary org rollups — only per-call leak codes on own calls if flag `employee.see_leak=true`.
- Mask customer phone in UI by default (`+84***..`).

---

## 8. Cross-cutting UI components

| Component | Props (conceptual) |
|-----------|--------------------|
| `EvidenceDrawer` | evidence_id, focuses audio |
| `IeBadge` | status string |
| `StageScoreBar` | stage_scores |
| `ViolationTable` | violations |
| `RootCauseCard` | root_cause |
| `CoachingList` | coaching |
| `RevenueLeakCard` | revenue_leak |
| `EmotionTimeline` | points[] |
| `SilenceOverlay` | silence intervals |
| `DialectChip` | bac/trung/nam/mixed |
| `AuditPopover` | entity_type, entity_id |

---

## 9. Navigation & IA wireframe (textual)

```
[Logo Tenant]  Dashboard  QA  Admin  |  Me ▾   Alerts  User
```

Mobile: Dashboard KPIs stacked; heatmaps horizontal scroll; QA review falls back to stacked panes (audio → scorecard → actions). Employee portal is mobile-first.

---

## 10. Empty / error / IE states

| State | UI |
|-------|-----|
| No calls in range | Illustration + CTA import/PBX |
| Analysis pending | Progress from pipeline stages API |
| Analysis failed_input | Reason + support code `trace_id` |
| Insufficient Evidence block | Amber panel + reason + “Request human review” |
| 403 | Clear role explanation |
| Partial score | Banner listing IE stages |

---

## 11. Analytics API dependencies

| UI need | API |
|---------|-----|
| Heatmaps | `GET /api/v1/analytics/stage-heatmap` |
| Emotion | `GET /api/v1/analytics/emotion-heatmap` |
| Revenue leak rollup | `GET /api/v1/analytics/revenue-leak` |
| Root cause pareto | `GET /api/v1/analytics/root-causes` |
| KPI | `GET /api/v1/analytics/kpis` |
| Call list | `GET /api/v1/calls` |
| Analysis | `GET /api/v1/calls/{id}/analysis` |

All rollup APIs exclude IE monetary fields from sums; include `ie_count`.

---

## 12. Performance UX

- Call list virtualized (≥10k rows).
- Analysis panel streams transcript separately from score JSON.
- Prefetch adjacent queue item in QA.
- Service worker not used for PII audio; signed URLs short-TTL.

---

## 13. i18n

Default `vi-VN`; `en-US` for labels. Rule messages and coaching scripts stored in DB per locale — UI does not translate rule meaning client-side.

---

## 14. Acceptance criteria

1. Four portals implemented with RBAC route guards.
2. Shared AnalysisResultPanel used everywhere scores appear.
3. Heatmaps, emotion timeline, revenue leak, KPI, root cause, coaching views exist for managers.
4. QA appeal workflow enforceable in UI (evidence required).
5. Admin SOP/Rulebook publish with audit visibility.
6. Employee sees only permitted data; PII masked by default.
7. IE states never rendered as score `0` silently.
