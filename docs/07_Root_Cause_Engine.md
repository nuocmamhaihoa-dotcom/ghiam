# 07 — Root Cause Engine
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Document ID | `DOC-AI-07-RCE` |
| Version | `1.0.0` |
| Status | Approved — Enterprise |
| Pipeline position | After **LLM Judge / Judge Ensemble** → before **Coaching Engine** |
| Scale target | ≥ 5M calls/month; graph catalog ≥ 500 versioned paths |
| Owners | ML Engineering, QA Director, Backend Lead |
| Constitution | Evidence-only · Rulebook from DB · `Insufficient Evidence` · Audit Log |

---

## 1. Purpose

Root Cause Engine (RCE) suy ra **chuỗi nguyên nhân gốc** của thất bại bán hàng / điểm QA thấp từ:

1. Violations có evidence (rule_id + transcript span + audio timestamps)
2. Stage score gaps
3. Conversation DNA (lặp lỗi agent)
4. SOP industry pack đang active

RCE **không suy diễn**. Không có evidence hợp lệ → trả `"status": "Insufficient Evidence"`.

---

## 2. Fixed pipeline context

```
Audio → Whisper → Speaker Diarization → Transcript Normalization
→ Semantic Segmentation → Evidence Extraction → Rule Engine
→ LLM Judge (Ensemble) → ★ Root Cause Engine ★
→ Coaching Engine → Revenue Leak AI → Dashboard → JSON Output
```

RCE chỉ chạy khi Judge Ensemble đã phát hành scorecard (kể cả khi nhiều tiêu chí = Insufficient Evidence).

---

## 3. Inputs (contract)

```json
{
  "call_id": "uuid",
  "tenant_id": "uuid",
  "rulebook_release_id": "uuid",
  "sop_pack_id": "uuid",
  "score": 62,
  "stage_scores": {"discovery": 40, "pricing": 55, "objection": 30},
  "violations": [
    {
      "rule_id": "R-0312",
      "severity": "major",
      "evidence": [
        {
          "quote": "Giá bên em chỉ 3 triệu thôi ạ",
          "speaker": "agent",
          "audio_ts_start": 42.1,
          "audio_ts_end": 45.0,
          "char_start": 1204,
          "char_end": 1240
        }
      ],
      "confidence": 0.86
    }
  ],
  "agent_dna_snapshot": {"repeat_mistakes": ["early_pricing"]},
  "industry": "bds"
}
```

**Gate:** mọi violation đưa vào RCE phải có ít nhất một evidence span đủ timestamp. Violation không evidence bị loại và ghi `dropped_for_insufficient_evidence`.

---

## 4. Output (standard envelope fragment)

```json
{
  "root_cause": {
    "status": "ok",
    "primary_code": "RC-0042",
    "primary_label": "Poor Discovery → Early Pricing → Weak Value → No Close",
    "confidence": 0.81,
    "graph": [
      {"from": "No Sale", "to": "No Closing Attempt", "weight": 0.9},
      {"from": "No Closing Attempt", "to": "Weak Value Building", "weight": 0.84},
      {"from": "Weak Value Building", "to": "Early Pricing", "weight": 0.88},
      {"from": "Early Pricing", "to": "Poor Discovery", "weight": 0.91}
    ],
    "evidence": [
      {
        "rule_id": "R-0312",
        "node": "Early Pricing",
        "quote": "Giá bên em chỉ 3 triệu thôi ạ",
        "audio_ts_start": 42.1,
        "audio_ts_end": 45.0
      }
    ],
    "secondary_codes": ["RC-0110", "RC-0077"],
    "recoverability": "high",
    "estimated_revenue_impact_band": "medium",
    "rulebook_release_id": "uuid",
    "graph_catalog_version": "rcg-v3.2.1",
    "evaluated_at": "2026-09-05T14:00:00Z"
  }
}
```

Khi thiếu dữ liệu:

```json
{
  "root_cause": {
    "status": "Insufficient Evidence",
    "primary_code": null,
    "reason": "Fewer than 2 evidenced violations mapped to catalog edges",
    "evaluated_at": "2026-09-05T14:00:00Z"
  }
}
```

---

## 5. Graph catalog (DB-versioned)

### 5.1 Tables (logical)

| Table | Role |
|-------|------|
| `root_cause_nodes` | Canonical nodes (Poor Discovery, Early Pricing, …) |
| `root_cause_edges` | Directed edges with prior weights |
| `root_cause_graphs` | Named paths (500+ enterprise graphs) |
| `rule_to_node_map` | `rule_id` → node(s); **from DB only** |
| `root_cause_releases` | Immutable publish of catalog |

### 5.2 Example path (one of ≥500)

```
No Sale
  ← No Closing Attempt
    ← Ignored Objection
      ← Weak Value Building
        ← Early Pricing
          ← Poor Discovery
```

Mỗi graph có: `trigger_rules[]`, `min_evidence_count`, `industry_affinity[]`, `coaching_template_id`, `revenue_leak_code`.

---

## 6. Algorithm (deterministic-first)

1. **Filter** violations with valid evidence timestamps.
2. **Map** `rule_id` → nodes via `rule_to_node_map` (DB).
3. **Activate** edges when both endpoints have evidence support OR stage-gap heuristic **only if** rule map permits (`allow_stage_gap=true` in DB).
4. **Score paths** = Σ (edge_prior × evidence_confidence × severity_weight) × DNA_repeat_boost.
5. **Select** primary path if `confidence ≥ tenant.threshold` (default 0.65); else Insufficient Evidence.
6. **Persist** result + audit (`who=system`, `rulebook_release_id`, `graph_catalog_version`).

**Cấm:** LLM tự bịa node mới ngoài catalog. LLM chỉ được dùng để *rank* paths đã ứng viên nếu `evaluator_mode=hybrid` và mọi node đã có evidence.

---

## 7. Scale design (millions of calls)

| Concern | Design |
|---------|--------|
| Throughput | Stateless RCE workers; Redis stream `rce.jobs`; idempotent by `call_id+rulebook_release_id` |
| Latency | p95 ≤ 300ms CPU path after scorecard ready |
| Catalog size | 500–5,000 graphs; cached in Redis per `root_cause_release_id` |
| Storage | `call_root_causes` partitioned by month; JSONB graph + evidence refs |
| Multi-tenant | All queries scoped by `tenant_id` |

---

## 8. Failure modes

| Condition | Behavior |
|-----------|----------|
| 0 evidenced violations | `Insufficient Evidence` |
| Rules not in map | Skip + metric `rce_unmapped_rule_total` |
| Catalog unpublished | Fail job; do not invent graph |
| DNA missing | Proceed without DNA boost (not IE) |

---

## 9. Acceptance criteria

- [ ] 100% outputs include `status`, `evaluated_at`, `rulebook_release_id` or IE reason
- [ ] No primary_code without ≥1 evidence span
- [ ] Catalog loaded only from DB release
- [ ] Audit row per evaluation
- [ ] Load test: 1k RCE/sec sustained on worker pool

---

## 10. Related documents

- [05_Scoring_Engine.md](./05_Scoring_Engine.md)
- [06_Rulebook_1000.md](./06_Rulebook_1000.md)
- [08_Coaching_Engine.md](./08_Coaching_Engine.md)
- [03_Database.md](./03_Database.md)
- [15_Monitoring.md](./15_Monitoring.md)
