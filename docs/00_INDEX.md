# AI QA TELESALE ENTERPRISE — Documentation Index

| Field | Value |
|-------|-------|
| Version | `2.0.0` |
| Status | Enterprise design pack — **20/20 complete** |
| Constitution | `.cursor/rules/project-rules.mdc` (`alwaysApply: true`) |
| Stack | Next.js · TypeScript · Tailwind · FastAPI · PostgreSQL · Redis · S3 · Docker Compose · JWT/RBAC · OpenAPI |

---

## Fixed AI Pipeline (immutable order)

```
Audio
→ Whisper
→ Speaker Diarization
→ Transcript
→ Semantic Segmentation
→ Evidence Extraction
→ Rule Engine
→ AI Judge Ensemble (5 agents)
→ Root Cause Graph
→ Coaching AI
→ Revenue Leak AI
→ Dashboard
→ JSON Output
```

**Không được đổi thứ tự.** Mọi tài liệu dưới đây map vào pipeline này.

### AI Judge Ensemble (5 agents)

1. **Evidence AI** — chỉ tìm bằng chứng  
2. **SOP Judge** — chỉ chấm Rulebook từ Database  
3. **Psychology AI** — tâm lý khách  
4. **Sales Expert AI** — kỹ năng bán  
5. **Consensus AI** — điểm cuối; chỉ kết luận khi đủ dữ liệu  

Thiếu dữ liệu → **`Insufficient Evidence`**.

### Canonical scoring JSON

```json
{
  "score": 0,
  "stage_scores": {},
  "violations": [],
  "evidence": [],
  "root_cause": {},
  "coaching": {},
  "revenue_leak": {}
}
```

---

## Document set (20)

| # | File | Focus |
|---|------|--------|
| 01 | [01_PRD.md](./01_PRD.md) | Product requirements, personas, SLAs, non-goals |
| 02 | [02_System_Architecture.md](./02_System_Architecture.md) | Clean Architecture, services, scale path |
| 03 | [03_Database.md](./03_Database.md) | PostgreSQL schema, rulebook versioning, audit |
| 04 | [04_API_Spec.md](./04_API_Spec.md) | FastAPI / OpenAPI, JWT RBAC, envelopes |
| 05 | [05_Scoring_Engine.md](./05_Scoring_Engine.md) | Rule Engine + AI Judge scoring |
| 06 | [06_Rulebook_1000.md](./06_Rulebook_1000.md) | 1000-rule taxonomy, DB-only rules |
| 07 | [07_Root_Cause_Engine.md](./07_Root_Cause_Engine.md) | Causal graphs, evidence-gated |
| 08 | [08_Coaching_Engine.md](./08_Coaching_Engine.md) | Evidence-based coaching plans |
| 09 | [09_VCIE.md](./09_VCIE.md) | Vietnamese Conversation Intelligence |
| 10 | [10_SOP_Industries.md](./10_SOP_Industries.md) | Multi-industry SOP packs |
| 11 | [11_Test_Cases.md](./11_Test_Cases.md) | Enterprise test strategy |
| 12 | [12_UI_UX.md](./12_UI_UX.md) | Next.js portals (Dashboard/QA/Admin/Employee) |
| 13 | [13_Deployment.md](./13_Deployment.md) | Docker Compose → K8s scale path |
| 14 | [14_Security.md](./14_Security.md) | JWT, RBAC, tenant isolation, PII |
| 15 | [15_Monitoring.md](./15_Monitoring.md) | Health, SLO, tracing, quality signals |
| 16 | [16_Revenue_Leak_AI.md](./16_Revenue_Leak_AI.md) | Recoverable revenue estimation |
| 17 | [17_Golden_Call.md](./17_Golden_Call.md) | 500 frozen golden benchmarks |
| 18 | [18_Calibration.md](./18_Calibration.md) | AI↔Human calibration & publish gates |
| 19 | [19_Conversation_DNA.md](./19_Conversation_DNA.md) | Agent skill genome / radar |
| 20 | [20_Dataset_Builder.md](./20_Dataset_Builder.md) | VECD factory, gates, export |

Related: [VECD.md](./VECD.md) · [Self_Learning_Lab.md](./Self_Learning_Lab.md) · [Digital_Twin.md](./Digital_Twin.md) · [Negotiation_Strategy.md](./Negotiation_Strategy.md) · [CLTV_Engine.md](./CLTV_Engine.md) · [War_Room.md](./War_Room.md) · [Autonomous_Sales.md](./Autonomous_Sales.md) · [Sales_OS.md](./Sales_OS.md) · [sprints/](./sprints/)

---

## How to implement from this pack

1. Read Constitution → `01_PRD` → `02_System_Architecture` → `03_Database`.  
2. Implement ingest + pipeline workers in **exact order** above.  
3. Rulebook only via DB (`06`); never hardcode.  
4. Wire scoring JSON contract (`05`, `04`) before UI.  
5. Add modules 07→08→16→19 on top of scorecards.  
6. Use `20` + `17` + `18` for regression and release gates.  
7. Deploy per `13`, harden per `14`, observe per `15`.  
8. Wire **Self-Learning Lab** (`Self_Learning_Lab.md`) so every call becomes QA-gated proposals — never auto-mutate production rules.  
9. Every Sprint: **Review → Refactor → Quality Gate**.

---

## Scale posture

- **Ingest:** Redis streams + horizontal STT / diarization / scoring workers  
- **Data:** PostgreSQL partitioning, read replicas, S3 for audio  
- **Rules:** never in source; versioned releases in DB  
- **Target:** millions of calls / month without redesigning pipeline order  

---

## Non-negotiables (Constitution)

- Clean Architecture · SOLID · Repository · Service Layer · DI  
- Next.js + TS + Tailwind · FastAPI · PostgreSQL · Redis · S3 · Docker Compose · JWT/RBAC · OpenAPI  
- No business-rule hardcoding  
- No conclusions without **Evidence + Timestamp**  
- Missing data → **`Insufficient Evidence`**  
- Structured logging · Audit log · Health check · Monitoring  
- Audit log on every mutation  

9. Wire **Digital Twin Salesperson** (`Digital_Twin.md`) — học từ golden/QA/high-conversion, roleplay + coaching, no verbatim clone.
10. Wire **Negotiation Strategy Engine** (`Negotiation_Strategy.md`) — dự đoán 3–5 bước tiếp theo + Strategy Graph đa phương án.

11. Wire **CLTV Engine** (`CLTV_Engine.md`) — lifetime value, churn, upsell/cross-sell, lead prioritization.
12. Wire **War Room AI** (`War_Room.md`) — live ops alerts, queue health, realtime floor command center.
13. Wire **Autonomous Sales AI** (`Autonomous_Sales.md`) — NBA + safe automation + approval-gated policy changes.
