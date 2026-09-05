# AI QA TELESALE ENTERPRISE — Documentation Index

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise design pack — complete |
| Constitution | `.cursor/rules/project-rules.mdc` (`alwaysApply: true`) |

---

## Fixed AI Pipeline (immutable order)

```
Audio
→ Whisper
→ Speaker Diarization
→ Transcript Normalization
→ Semantic Segmentation
→ Evidence Extraction
→ Rule Engine
→ LLM Judge (5-agent Ensemble)
→ Root Cause Engine
→ Coaching Engine
→ Revenue Leak AI
→ Dashboard
→ JSON Output
```

**Không được đổi thứ tự.** Mọi module trong docs dưới đây map vào pipeline này.

### Judge Ensemble (5 agents)

1. Evidence AI — chỉ tìm bằng chứng  
2. SOP Judge — chỉ chấm Rulebook từ DB  
3. Psychology AI — tâm lý khách  
4. Sales Expert AI — kỹ năng bán  
5. Consensus AI — điểm cuối; chỉ kết luận khi đủ dữ liệu  

Thiếu dữ liệu → **`Insufficient Evidence`**.

### Standard scoring JSON

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

## Document set (15)

| # | File | Focus |
|---|------|--------|
| 01 | [01_PRD.md](./01_PRD.md) | Product requirements, personas, SLAs, non-goals |
| 02 | [02_System_Architecture.md](./02_System_Architecture.md) | Clean Architecture, services, scale to millions |
| 03 | [03_Database.md](./03_Database.md) | PostgreSQL schema, rulebook versioning, audit |
| 04 | [04_API_Spec.md](./04_API_Spec.md) | FastAPI / OpenAPI, JWT RBAC, envelopes |
| 05 | [05_Scoring_Engine.md](./05_Scoring_Engine.md) | Rule Engine + LLM Judge scoring |
| 06 | [06_Rulebook_1000.md](./06_Rulebook_1000.md) | 1000-rule taxonomy, DB-only rules |
| 07 | [07_Root_Cause_Engine.md](./07_Root_Cause_Engine.md) | Causal graphs, evidence-gated |
| 08 | [08_Coaching_Engine.md](./08_Coaching_Engine.md) | Evidence-based coaching + DNA |
| 09 | [09_VCIE.md](./09_VCIE.md) | Vietnamese Conversation Intelligence |
| 10 | [10_SOP_Industries.md](./10_SOP_Industries.md) | Multi-industry SOP packs |
| 11 | [11_Test_Cases.md](./11_Test_Cases.md) | Enterprise test strategy |
| 12 | [12_UI_UX.md](./12_UI_UX.md) | Next.js portals (Dashboard/QA/Admin/Employee) |
| 13 | [13_Deployment.md](./13_Deployment.md) | Docker Compose → K8s scale path |
| 14 | [14_Security.md](./14_Security.md) | JWT, RBAC, tenant isolation, PII |
| 15 | [15_Monitoring.md](./15_Monitoring.md) | Health, SLO, tracing, quality signals |

Sprint reports: [sprints/SPRINT_REPORT.md](./sprints/SPRINT_REPORT.md)

---

## Scale posture

- **Ingest:** Redis streams + horizontal STT/diarization/scoring workers  
- **Data:** PostgreSQL partitioning, read replicas, S3 for audio  
- **Rules:** never in source; versioned releases in DB  
- **Target:** millions of calls / month without redesigning pipeline order  

---

## Non-negotiables (from Constitution)

- Clean Architecture · SOLID · Repository · Service · DI  
- Next.js + TS + Tailwind · FastAPI · PostgreSQL · Redis · S3 · Docker Compose · JWT/RBAC · OpenAPI  
- No business-rule hardcoding  
- No conclusions without evidence + timestamps  
- Audit log on every mutation  
