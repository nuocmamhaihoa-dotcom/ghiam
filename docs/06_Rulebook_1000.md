# 06 — Rulebook 1000
## AI QA TELESALE ENTERPRISE — Bộ quy tắc 1000 (DB-versioned)

**Phiên bản catalog:** 1.0.0  
**Phạm vi:** ~1000 rules chuẩn cho telesale VI, multi-industry packs  
**Nguyên tắc:** Mọi rule sống trong PostgreSQL (`rules` / `rule_versions` / `rulebook_releases`) — **cấm hardcode** trong Next.js/FastAPI/prompt

---

## 1. Mục tiêu & Non-goals

### Goals
- Cung cấp taxonomy đủ lớn (≥1000) để chấm opening → close → compliance → soft skills.
- Mỗi rule có code ổn định, evidence requirements, weight, severity, evaluator_type.
- Hỗ trợ publish/rollback theo campaign.
- Map được sang Root Cause + Coaching + Revenue impact codes.

### Non-goals
- Không phải toàn bộ 1000 rules chỉ dành cho một SKU.
- Không nhúng regex mẫu như “source of truth” trong repo app (seed/migration có kiểm soát thì được).
- Không dùng rule để suy diễn khi thiếu evidence — engine trả `Insufficient Evidence`.

---

## 2. Actors

| Actor | Quyền |
|-------|--------|
| QA Director | Soạn thảo, publish, rollback |
| Compliance | Approve auto-fail / legal wording |
| Coach | Đề xuất rule mới qua Sprint change |
| Scoring Engine | Read-only snapshot |
| Calibration | Đo hiệu lực rule |

---

## 3. Rule Record Schema (normative)

```json
{
  "rule_code": "R-OPEN-001",
  "category": "opening",
  "subcategory": "greeting",
  "title": "Chào khách hàng",
  "description": "Agent phải có lời chào/xưng hô lịch sự ở opening.",
  "severity": "major",
  "weight": 1.35,
  "auto_fail": false,
  "required": true,
  "evaluator_type": "span_classifier",
  "evidence_requirements": {
    "min_spans": 1,
    "speakers_allowed": ["agent"],
    "stage_keys": ["opening"],
    "slots": ["greeting_phrase"],
    "min_confidence": 0.7
  },
  "evaluator_config": {
    "positive_labels": ["has_greeting"],
    "applicability": {"directions": ["inbound", "outbound"]}
  },
  "revenue_impact_code": null,
  "cause_code_on_fail": "RC-OPEN-NO-GREET",
  "coaching_template_code": "CT-OPEN-GREET-01",
  "industries": ["*"],
  "version": 3
}
```

**Verdict semantics:** Engine-only; rule không “tự pass”.

---

## 4. Numbering & Category Budget (1000)

| Range | Category | Count | Mô tả |
|-------|----------|------:|-------|
| R-OPEN-001..120 | `opening` | 120 | Chào, xưng tên, công ty, phép thoại, mục đích gọi |
| R-DISC-001..100 | `discovery` | 100 | Câu hỏi nhu cầu, xác nhận tình trạng, listening |
| R-PITC-001..120 | `pitch` | 120 | USP, lợi ích, chứng cứ, phù hợp nhu cầu |
| R-OBJ-001..150 | `objection` | 150 | Giá, “đắt”, “suy nghĩ”, “đã có”, thời gian, chồng/vợ |
| R-CLOS-001..120 | `closing` | 120 | Chốt SL, giá, địa chỉ, đồng ý, next step |
| R-COMP-001..100 | `compliance` | 100 | Cam kết, cấm nói quá, PII, ghi âm consent (nếu luật) |
| R-SOFT-001..080 | `soft_skills` | 80 | Ngắt lời, tốc độ, thái độ, empathy |
| R-PROD-001..100 | `product_specific` | 100 | Pack theo ngành (BH, TPCN, điện máy, GD, BĐS, FMCG…) |
| R-PROC-001..060 | `process` | 60 | CRM tag, follow-up promise, transfer |
| R-META-001..050 | `metadata_quality` | 50 | Audio/meta đủ để chấm |
| **Total** | | **1000** | |

Industry packs overlay: `R-PROD-*` + optional weight overrides trong release — vẫn DB.

---

## 5. Severity & Weight Guidelines

| Severity | Weight band | Auto-fail typical |
|----------|-------------|-------------------|
| info | 0.2–0.5 | never |
| minor | 0.5–1.0 | rare |
| major | 1.0–1.6 | sometimes |
| critical | 1.6–2.5 | often |

Compliance critical (sai giá cố ý, đe dọa, thông tin y tế sai) → `auto_fail=true`.

---

## 6. Evidence Requirement Patterns

| Pattern code | Dùng cho |
|--------------|----------|
| `EP-AGENT-OPEN-SPAN` | Opening agent speech |
| `EP-AGENT-SLOT` | Slot extraction (price, qty, address) |
| `EP-CUST-OBJECTION` | Customer objection + agent reply pair |
| `EP-PAIR-CLOSE` | Consent + confirmation |
| `EP-NEGATIVE-ABSENCE` | Cấm nói — cần full-call scan; absence of bad span = pass **chỉ khi** coverage STT đủ; nếu STT low → IE |

Absence rules **không** được pass khi `avg_confidence` < tenant threshold → IE.

---

## 7. Catalog Chi tiết theo Category

Dưới đây liệt kê **đầy đủ mã rule** theo block. Mỗi mã là rule độc lập trong DB. Các mục **FULL** mô tả đủ fields; các mã còn lại inherit pattern category (title ngắn trong bảng). Seed script materialize đủ 1000 rows.

### 7.1 Opening — R-OPEN-001..120 (120)

#### FULL examples
**R-OPEN-001 — Chào khách hàng**  
severity major · weight 1.35 · span_classifier · slot `greeting_phrase` · cause `RC-OPEN-NO-GREET` · CT-OPEN-GREET-01

**R-OPEN-002 — Xưng tên agent**  
major · 1.20 · slot `agent_name` · RC-OPEN-NO-NAME

**R-OPEN-003 — Nêu tên công ty/thương hiệu**  
major · 1.20 · slot `company_name` · RC-OPEN-NO-BRAND

**R-OPEN-004 — Xin phép được nói chuyện**  
minor · 0.90 · labels `permission_ask` · RC-OPEN-NO-PERMIT

**R-OPEN-005 — Nêu mục đích cuộc gọi**  
major · 1.30 · slot `call_purpose` · RC-OPEN-NO-PURPOSE

**R-OPEN-006 — Xác nhận đúng người nghe**  
major · 1.25 · labels `right_party_contact` · RC-OPEN-WRONG-PARTY

**R-OPEN-007 — Không mở đầu bằng pitch cứng**  
minor · 0.80 · composite (pitch_before_purpose → fail) · RC-OPEN-HARD-SELL

**R-OPEN-008 — Giọng chào không quát/nói overlapping mở đầu**  
soft bridge · 0.70 · needs diarization overlap metrics · else IE

#### Inventory R-OPEN-009..120
| Codes | Theme |
|-------|-------|
| 009–020 | Lịch sự xưng hô (anh/chị/quý khách), tránh biệt danh thô |
| 021–035 | Brand/legal entity đúng campaign |
| 036–050 | Consent ghi âm / thông báo cuộc gọi (nếu campaign flag) |
| 051–070 | Đúng script khung opening theo SOP version (DB SOP ref) |
| 071–090 | Pace control đầu call, không đọc máy |
| 091–110 | Inbound vs outbound opening variants |
| 111–120 | Escalation nếu khách báo máy nhầm / không rảnh |

---

### 7.2 Discovery — R-DISC-001..100 (100)

#### FULL
**R-DISC-001 — Có ≥1 câu hỏi mở về nhu cầu**  
major · 1.30 · labels `open_question` · RC-DISC-NO-QUESTION

**R-DISC-002 — Xác nhận lại câu trả lời khách (paraphrase)**  
minor · 0.90 · labels `reflect_listening` · RC-DISC-NO-LISTEN

**R-DISC-003 — Hỏi tình trạng hiện tại liên quan SP**  
major · 1.20 · industry slot · RC-DISC-NO-CONTEXT

**R-DISC-004 — Không bỏ qua tín hiệu “đang bận”**  
major · 1.10 · customer slot `busy_signal` + agent handle · else fail/IE

#### Inventory
| Codes | Theme |
|-------|-------|
| 005–025 | Pain questions, budget awareness (không ép) |
| 026–045 | Decision maker identification |
| 046–065 | Timeline/need urgency |
| 066–085 | Prior usage / competitor awareness |
| 086–100 | Note-taking verbal confirm (“em ghi nhận…”) |

---

### 7.3 Pitch — R-PITC-001..120 (120)

#### FULL
**R-PITC-001 — Nêu tên sản phẩm đúng**  
critical for close packs · 1.35 · slot `product_name` · RC-PITCH-NO-NAME · maps ChốtKiểm criterion

**R-PITC-002 — Liên kết lợi ích với nhu cầu đã discovery**  
major · 1.40 · composite need_span + benefit_span · RC-PITCH-NO-FIT

**R-PITC-003 — Đưa USP / khác biệt**  
minor · 1.00 · labels `usp` · RC-PITCH-GENERIC

**R-PITC-004 — Không phóng đại công dụng (compliance bridge)**  
critical · auto_fail possible · negative absence + claim classifier · RC-COMP-OVERCLAIM

#### Inventory
| Codes | Theme |
|-------|-------|
| 005–030 | Feature→benefit translations |
| 031–050 | Social proof / testimonial mention (nếu evidence) |
| 051–070 | Demo/explain how-to-use |
| 071–090 | Bundle / combo clarity |
| 091–110 | Risk reversal (đổi trả) chỉ khi policy cho phép |
| 111–120 | Avoid jargon; simplify |

---

### 7.4 Objection Handling — R-OBJ-001..150 (150)

#### FULL
**R-OBJ-001 — Nhận diện objection giá**  
major · 1.20 · customer slot `objection_price` · (detection rule; scoring companion)

**R-OBJ-010 — Phản hồi objection giá có kỹ thuật**  
major · 1.50 · pair customer price objection + agent technique label ∈ config list · RC-OBJ-PRICE-01

**R-OBJ-020 — Không tranh cãi / nói xấu khách**  
critical · auto_fail · negative · RC-SOFT-HOSTILE

**R-OBJ-030 — “Để suy nghĩ” — có câu hỏi làm rõ**  
major · 1.30 · RC-OBJ-THINK-01

**R-OBJ-040 — “Đang dùng bên khác” — có differentiate**  
major · 1.30 · RC-OBJ-COMPETITOR

**R-OBJ-050 — Objection chồng/vợ / decision maker**  
major · 1.40 · RC-OBJ-DECISION-MAKER

#### Inventory blocks
| Codes | Objection type |
|-------|----------------|
| 001–015 | Detection taxonomy |
| 016–045 | Price / đắt / không tiền |
| 046–070 | Time / bận / gọi lại |
| 071–095 | Trust / lừa đảo / chưa nghe tên |
| 096–115 | Product fit / không cần |
| 116–135 | Logistics / ship / COD |
| 136–150 | Multi-objection sequencing & prioritization |

Mỗi block có rule **detect**, **acknowledge**, **probe**, **respond**, **re-close** (5-pattern × types ≈ cover 150).

---

### 7.5 Closing — R-CLOS-001..120 (120)

#### FULL (aligned ChốtKiểm enterprise extension)
**R-CLOS-001 — Chốt số lượng** · critical · 1.40 · slot `quantity` · RC-CLOSE-NO-QTY  
**R-CLOS-002 — Chốt giá bán** · critical · 1.40 · slot `price_amount` · auto_fail if wrong price band · RC-CLOSE-NO-PRICE  
**R-CLOS-003 — Chốt địa chỉ giao** · critical · 1.40 · slot `address` · RC-CLOSE-NO-ADDR  
**R-CLOS-004 — Xác nhận khách đồng ý** · critical · 1.50 · labels `customer_agree` · RC-CLOSE-NO-CONSENT  
**R-CLOS-005 — Tóm tắt đơn trước kết thúc** · major · 1.20 · RC-CLOSE-NO-SUMMARY  
**R-CLOS-006 — Hỏi chốt rõ ràng (ask for order)** · major · 1.45 · RC-CLOSE-NO-ASK  
**R-CLOS-007 — Xử lý đồng ý mềm (soft-yes) bằng confirm** · major · 1.30 · RC-CLOSE-SOFT-YES  
**R-CLOS-008 — Không chốt khi khách từ chối rõ** · critical · auto_fail · RC-COMP-FORCE-CLOSE  

#### Inventory
| Codes | Theme |
|-------|-------|
| 009–030 | Payment/COD/time window |
| 031–050 | Upsell ethical rules |
| 051–070 | Callback scheduling quality |
| 071–090 | Outro courtesy + hotline |
| 091–110 | Multi-item close consistency |
| 111–120 | Post-close silence / rush |

---

### 7.6 Compliance — R-COMP-001..100 (100)

#### FULL
**R-COMP-001 — Không đe dọa / xúc phạm** · critical · auto_fail  
**R-COMP-002 — Không hứa kết quả y tế tuyệt đối** · critical · auto_fail · TPCN/BH packs  
**R-COMP-003 — Giá nói đúng price list campaign** · critical · slot vs metadata `list_price` · mismatch fail; missing list_price → IE  
**R-COMP-004 — Không thu thập CVV/thẻ full** · critical · auto_fail · redact  
**R-COMP-005 — Không ghi nhận đồng ý khi không có evidence** · critical — engine-level also  

#### Inventory
| Codes | Theme |
|-------|-------|
| 006–025 | Advertising standards / overpromise |
| 026–045 | Privacy / đọc PII không cần thiết |
| 046–065 | Recording disclosure |
| 066–085 | Discrimination / harassment |
| 086–100 | Mandatory disclaimers per industry |

---

### 7.7 Soft Skills — R-SOFT-001..080 (80)

Themes: không ngắt lời (overlap %), empathy markers, speaking rate band (WPM từ duration+words — nếu thiếu duration → IE), dead-air dài, filler quá mức, lịch sự outro, energy (chỉ khi prosody artifact có; không thì IE — **không đoán cảm xúc**).

FULL: **R-SOFT-010 WPM trong band campaign** · minor · needs duration · else IE.

---

### 7.8 Product Specific — R-PROD-001..100 (100)

| Sub-pack | Codes | Examples |
|----------|-------|----------|
| FMCG / SOAP | 001–020 | Thành phần, hạn dùng, hướng dẫn gội |
| TPCN | 021–040 | Không chữa bệnh, đối tượng dùng |
| Bảo hiểm | 041–060 | Quyền lợi, loại trừ, thời gian chờ |
| Điện máy | 061–075 | Bảo hành, lắp đặt |
| Giáo dục | 076–088 | Lịch học, hoàn phí policy |
| BĐS | 089–100 | Pháp lý, không cam kết lợi nhuận ảo |

Release chọn subset theo campaign; codes vẫn tồn tại trong tenant library.

---

### 7.9 Process — R-PROC-001..060 (60)

CRM disposition đúng, hứa gọi lại có datetime, transfer có warm intro, ticket id đọc lại, không double-order without confirm, tag loss reason (verbal).

---

### 7.10 Metadata Quality — R-META-001..050 (50)

Audio present, duration ≥ min, agent_id present, campaign mapped, language vi, stereo/mono policy, silence ratio. Failures thường → call-level IE chứ không “pass 0 điểm giả”.

---

## 8. Cross-walk tới ChốtKiểm / MVP hiện có

| Legacy criterion key | Rule codes |
|----------------------|------------|
| greeting | R-OPEN-001 |
| productName | R-PITC-001 |
| quantity | R-CLOS-001 |
| price | R-CLOS-002 |
| deliveryAddress | R-CLOS-003 |
| customerAgreed | R-CLOS-004 |
| customerRefused | R-OBJ-* + R-CLOS-008 |
| customerAttitude | R-SOFT-* (evidence only) |
| agentAttitude | R-SOFT-* / R-COMP-001 |

Enterprise **mở rộng** đủ 1000; legacy 9 tiêu chí là subset release `chotkiem-compat`.

---

## 9. Release Packs (ví dụ)

| Pack | Rules included | Use |
|------|----------------|-----|
| `core-400` | OPEN+DISC+PITC+CLOS+COMP essentials | New tenants |
| `full-1000` | All | Enterprise |
| `fmcg-close` | core + PROD 001–020 + CLOS | Soap/FMCG |
| `shadow-test` | full-1000 shadow | Calibration |

Publish tạo `rulebook_releases.snapshot_hash`.

---

## 10. Lifecycle & Sprint

```
draft rule_version → peer review → sprint_changes(in_sprint)
  → calibration on gold set → publish release → monitor IE/fail rates → rollback if needed
```

Emergency fix: dual approval `qa_director` + `compliance`, audit `rulebook.emergency_publish`.

---

## 11. Insufficient Evidence Mapping

| Situation | Rule outcome |
|-----------|--------------|
| Slot không extract được | IE |
| STT thấp trên region rule | IE |
| Price list meta thiếu cho R-COMP-003 | IE |
| Prosody thiếu cho soft energy rules | IE |
| Negative absence + coverage thấp | IE (không pass) |

---

## 12. Failure Modes

- Seed thiếu rule_code trong release → publish validation fail.
- Duplicate codes → DB unique constraint.
- Weight NaN → rejected at API.
- Evaluator_config invalid JSON schema → cannot activate version.

---

## 13. Audit Requirements

`rulebook.rule_create|update|version|publish|rollback|seed_import|emergency_publish` với before/after weight/auto_fail.

---

## 14. Seed Materialization (1000)

Pseudo:
```text
for category in BUDGET:
  for i in 1..count:
    upsert rule_code
    insert rule_version 1 with template by subcategory
attach to release full-1000
audit seed_import count=1000
```

Titles/descriptions tiếng Việt chuẩn hóa; evaluator_config tham chiếu slot dictionary chung.

---

## 15. Slot Dictionary (shared)

`greeting_phrase`, `agent_name`, `company_name`, `call_purpose`, `product_name`, `quantity`, `price_amount`, `address`, `customer_agree`, `customer_refuse`, `objection_price`, `objection_think`, `callback_time`, `usp`, `overclaim_phrase`, …

Slots versioned in `slot_dictionary` (optional table) — Scoring references names only.

---

## 16. Governance KPI

| KPI | Target |
|-----|--------|
| Active rules in prod release | tracked |
| Orphan rules (no hit 90d) | review deprecate |
| IE rate per rule | < 15% steady-state (trừ soft prosody) |
| Human κ critical rules | ≥ 0.75 before publish |

---

## 17. Example Release Snapshot Fragment

```json
{
  "release": "2026.09.05-full-1000",
  "count": 1000,
  "items": [
    {"rule_code": "R-OPEN-001", "version": 3, "weight": 1.35},
    {"rule_code": "R-CLOS-002", "version": 5, "weight": 1.40, "auto_fail": true}
  ]
}
```

---

## 18. Document Control

Owner: QA Director · Technical: Backend + ML · Related: Scoring, Root Cause, Coaching

**Hết Rulebook 1000 v1.0.0 — đủ ngân sách 1000 mã rule, schema, packs, governance.**
