# 08 — Coaching Engine
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Document ID | `DOC-AI-08-COACH` |
| Version | `1.0.0` |
| Status | Approved — Enterprise |
| Pipeline position | After **Root Cause Engine** → before **Revenue Leak AI** |
| Scale target | ≥ 5M coaching plans/month; personalization via Conversation DNA |
| Owners | QA Director, L&D, ML Engineering, Frontend Lead |
| Constitution | Evidence-only · No hardcoded tips · `Insufficient Evidence` · Audit Log |

---

## 1. Purpose

Coaching Engine chuyển scorecard + root cause + golden-call contrast thành **kế hoạch huấn luyện có bằng chứng** cho agent và team lead.

Mỗi recommendation phải cite:

- `rule_id` (+ rule version)
- evidence quote + audio timestamps
- root cause code (nếu có)
- optional golden-call span đối chiếu

Không evidence → không coaching bịa; trả `Insufficient Evidence`.

---

## 2. Fixed pipeline context

```
… → Rule Engine → LLM Judge → Root Cause Engine
→ ★ Coaching Engine ★ → Revenue Leak AI → Dashboard → JSON Output
```

---

## 3. Inputs

```json
{
  "call_id": "uuid",
  "agent_id": "uuid",
  "score": 62,
  "stage_scores": {},
  "violations": [],
  "evidence": [],
  "root_cause": {"primary_code": "RC-0042", "status": "ok"},
  "dna": {
    "strengths": ["rapport"],
    "weaknesses": ["discovery", "pricing_timing"],
    "repeat_mistakes": ["early_pricing"],
    "trend_30d": {"discovery": -8}
  },
  "golden_call_id": "GOLD-000123",
  "rulebook_release_id": "uuid",
  "locale": "vi-VN"
}
```

Templates coaching **chỉ** lấy từ PostgreSQL (`coaching_templates`, gắn `rule_id` / `root_cause_code`). Service không hardcode câu coaching.

---

## 4. Output fragment

```json
{
  "coaching": {
    "status": "ok",
    "top_errors": [
      {
        "rank": 1,
        "rule_id": "R-0312",
        "title": "Báo giá trước khi đủ discovery",
        "evidence_quote": "Giá bên em chỉ 3 triệu thôi ạ",
        "audio_ts_start": 42.1,
        "audio_ts_end": 45.0,
        "root_cause_code": "RC-0042",
        "fix": "Hoàn tất tối thiểu 3 câu discovery trước khi nêu giá",
        "severity": "major"
      }
    ],
    "top_strengths": [
      {
        "rank": 1,
        "rule_id": "R-0088",
        "title": "Mở đầu rõ ràng có tên + công ty",
        "evidence_quote": "Em chào anh, em Minh bên ..."
      }
    ],
    "golden_contrast": {
      "golden_call_id": "GOLD-000123",
      "good_span": "Anh đang quan tâm căn mấy phòng ạ?",
      "bad_span": "Giá bên em chỉ 3 triệu thôi ạ",
      "stage": "discovery"
    },
    "drills": [
      {
        "id": "DRILL-014",
        "type": "roleplay",
        "scenario": "Khách BĐS nói 'đắt quá'",
        "success_criteria": ["đồng cảm", "hỏi ngân sách", "value trước giá"],
        "linked_rules": ["R-0312", "R-0450"],
        "estimated_minutes": 12
      }
    ],
    "roleplay_script": {
      "customer_lines": ["Đắt quá, bên kia rẻ hơn"],
      "expected_agent_behaviors": ["acknowledge", "probe", "reframe_value"]
    },
    "retest_plan": {
      "due_in_days": 7,
      "focus_rules": ["R-0312", "R-0144"],
      "success_metric": "stage_scores.discovery >= 75 AND no R-0312 fail"
    },
    "dna_delta_expected": {"discovery": 10, "pricing": 5},
    "generated_at": "2026-09-05T14:00:00Z"
  }
}
```

IE case:

```json
{
  "coaching": {
    "status": "Insufficient Evidence",
    "reason": "No evidenced violations or strengths available for template binding",
    "generated_at": "2026-09-05T14:00:00Z"
  }
}
```

---

## 5. Selection logic

1. Rank violations by `severity × weight × DNA_repeat_boost`.
2. Take top 3 errors with evidence; top 3 strengths with evidence.
3. Bind DB templates by `rule_id` then fallback `root_cause_code`.
4. Select golden call: same industry + stage + `benchmark_score ≥ 90`, immutable.
5. Build drills from template library; skip if template missing (metric + IE for that drill slot).
6. Write `coaching_plans` + audit log.

**Cấm:** LLM tự viết tip không gắn `rule_id`/evidence. LLM chỉ paraphrase template đã duyệt nếu `tenant.allow_llm_paraphrase=true`, vẫn giữ evidence citations.

---

## 6. Conversation DNA integration

| DNA field | Coaching use |
|-----------|--------------|
| `repeat_mistakes` | Boost rank; force drill |
| `weaknesses` | Stage-focused retest |
| `strengths` | Positive reinforcement cards |
| `trend_30d` | Escalate to team lead if declining |

DNA updates **after** coaching retest scoring — never invent progress.

---

## 7. Employee Portal UX mapping

| Block | UI |
|-------|----|
| top_errors | “Cần sửa” with audio jump-to timestamp |
| top_strengths | “Giữ vững” |
| golden_contrast | Split transcript view |
| drills | Start roleplay / mark complete |
| retest_plan | Calendar due + linked calls |

UI **không** chứa threshold nghiệp vụ.

---

## 8. Scale

- Async generation on `coaching.jobs` Redis stream
- Template cache per `rulebook_release_id`
- p95 ≤ 400ms after RCE complete
- Partition `coaching_plans` by month
- Multi-tenant isolation on `tenant_id`

---

## 9. Acceptance criteria

- [ ] Every tip cites rule_id + evidence timestamps OR status IE
- [ ] Templates only from DB
- [ ] Golden contrast only from immutable golden calls
- [ ] Audit log on create/update/retest
- [ ] Contract tests assert coaching fragment schema

---

## 10. Related

- [07_Root_Cause_Engine.md](./07_Root_Cause_Engine.md)
- [06_Rulebook_1000.md](./06_Rulebook_1000.md)
- [12_UI_UX.md](./12_UI_UX.md)
- [05_Scoring_Engine.md](./05_Scoring_Engine.md)
- [09_VCIE.md](./09_VCIE.md)
