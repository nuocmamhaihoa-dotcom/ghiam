# 10 — Industry SOP Design (50 Verticals)

| Field | Value |
|-------|-------|
| Document ID | `DOC-ARCH-10-SOP` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Scale | Multi-tenant; 50 industry packs; versioned SOP → Rulebook mapping |
| Owners | QA Director, Domain Product, Backend Lead |
| Related | VCIE (`09_VCIE.md`), Rulebook DB, UI (`12_UI_UX.md`) |

---

## 1. Purpose

Define how **Standard Operating Procedures (SOP)** for telesales are modeled, versioned, linked to Rulebook rules, and evaluated at millions-of-calls scale — covering **50 industries** with shared stage grammar and industry-specific mandatory checkpoints.

**Invariants**

- SOP content and pass/fail criteria live in **PostgreSQL**, not in frontend/backend source.
- Every SOP stage maps to zero-or-more `rule_id`s.
- Scoring uses the standard `AnalysisResult` envelope (`score`, `stage_scores`, `violations`, `evidence`, `root_cause`, `coaching`, `revenue_leak`).
- Missing required evidence → `Insufficient Evidence`, never auto-pass.

---

## 2. Domain model

### 2.1 Entities

```
Tenant 1──* IndustryPack 1──* SopDefinition 1──* SopStage
                                      │
                                      └──* SopRuleBinding → Rulebook.Rule
```

### 2.2 Tables

```sql
CREATE SCHEMA sop;

CREATE TABLE sop.industries (
  code            VARCHAR(16) PRIMARY KEY,  -- BDS, SPA, ...
  name_vi         VARCHAR(128) NOT NULL,
  name_en         VARCHAR(128) NOT NULL,
  vertical_group  VARCHAR(64) NOT NULL,
  risk_tier       VARCHAR(16) NOT NULL,     -- low|medium|high|regulated
  is_active       BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE sop.definitions (
  sop_id          VARCHAR(64) PRIMARY KEY,  -- sop.bds.primary.v12
  industry_code   VARCHAR(16) NOT NULL REFERENCES sop.industries(code),
  tenant_id       UUID,                     -- NULL = platform template
  title           VARCHAR(256) NOT NULL,
  version         VARCHAR(32) NOT NULL,
  status          VARCHAR(16) NOT NULL,     -- draft|active|deprecated
  effective_from  TIMESTAMPTZ NOT NULL,
  effective_to    TIMESTAMPTZ,
  checksum        CHAR(64) NOT NULL,
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (industry_code, tenant_id, version)
);

CREATE TABLE sop.stages (
  id              UUID PRIMARY KEY,
  sop_id          VARCHAR(64) NOT NULL REFERENCES sop.definitions(sop_id),
  stage_key       VARCHAR(32) NOT NULL,     -- opening|discovery|...
  sequence_no     INT NOT NULL,
  title_vi        VARCHAR(256) NOT NULL,
  objective       TEXT NOT NULL,
  min_score       NUMERIC(5,2),             -- from DB config, not code
  weight          NUMERIC(5,4) NOT NULL,
  UNIQUE (sop_id, stage_key)
);

CREATE TABLE sop.rule_bindings (
  id              UUID PRIMARY KEY,
  sop_id          VARCHAR(64) NOT NULL REFERENCES sop.definitions(sop_id),
  stage_key       VARCHAR(32) NOT NULL,
  rule_id         VARCHAR(128) NOT NULL,
  required        BOOLEAN NOT NULL DEFAULT true,
  weight_override NUMERIC(5,4),
  UNIQUE (sop_id, rule_id)
);

CREATE TABLE sop.checkpoints (
  id              UUID PRIMARY KEY,
  sop_id          VARCHAR(64) NOT NULL REFERENCES sop.definitions(sop_id),
  checkpoint_code VARCHAR(64) NOT NULL,
  stage_key       VARCHAR(32) NOT NULL,
  description_vi  TEXT NOT NULL,
  evidence_type   VARCHAR(32) NOT NULL,     -- transcript|audio|crm|any
  UNIQUE (sop_id, checkpoint_code)
);
```

### 2.3 Repository ports

```python
class ISopRepository(Protocol):
    async def get_active(self, tenant_id: UUID, industry_code: str) -> SopDefinition: ...
    async def list_bindings(self, sop_id: str) -> list[SopRuleBinding]: ...
    async def publish(self, sop_id: str, actor_id: UUID) -> None: ...  # audits
```

---

## 3. Shared stage grammar (all industries)

| `stage_key` | Goal | Typical VCIE intents |
|-------------|------|----------------------|
| `opening` | Chào, xin phép, xác định đúng người | `agent.greeting`, `agent.permission` |
| `discovery` | Nhu cầu, pain, ngân sách, thời điểm | `agent.discovery_need` |
| `pitch` | Giá trị SP / dịch vụ đúng nhu cầu | `agent.pitch`, `agent.price_quote` |
| `objection` | Xử lý từ chối theo catalog | `customer.objection`, `agent.objection_handle` |
| `close` | CTA / chốt / booking / COD confirm | `agent.close_ask`, `customer.buying_signal` |
| `outro` | Tóm tắt, cảm ơn, next step | `agent.confirm_info`, `agent.schedule` |

Weights default (overridable per SOP row): opening 0.12, discovery 0.18, pitch 0.20, objection 0.20, close 0.22, outro 0.08.

---

## 4. SOP ↔ Rulebook linkage pattern

Each industry pack ships:

1. `sop.definitions` template (platform-owned).
2. Seed rules in `rulebook.rules` namespaced `rule.{industry}.*`.
3. Bindings in `sop.rule_bindings`.
4. Coaching tip templates referencing `rule_id` (not free-text-only).

**Publish workflow**

1. Author edits draft SOP in Admin UI.
2. Diff + validation (every required checkpoint has ≥1 rule).
3. `qa_manager` approves → `status=active`, previous version `deprecated`.
4. Audit: `sop.publish` with before/after checksum.
5. In-flight calls keep the `rulebook_version` / `sop_id` frozen at analyze time.

---

## 5. Standard AnalysisResult usage for SOP scoring

```json
{
  "score": 81.0,
  "stage_scores": {
    "opening": 90,
    "discovery": 75,
    "pitch": 80,
    "objection": 70,
    "close": 85,
    "outro": 88
  },
  "violations": [],
  "evidence": [],
  "root_cause": {},
  "coaching": {},
  "revenue_leak": {}
}
```

`stage_scores[stage] = Σ (rule_results in stage × weights)` with Insufficient Evidence rules excluded from denominator and flagged in `violations` as `status: Insufficient Evidence` (severity `info`).

---

## 6. Fifty industry packs

Vertical groups: **Health & Beauty**, **Finance & Protection**, **Property & Mobility**, **Education**, **Retail & FMCG**, **Home & Tech**, **B2B & Services**, **Travel & Lifestyle**, **Public & Regulated-adjacent**.

For each industry below: code, risk tier, mandatory checkpoints (map to rules), top objections, revenue-leak patterns.

### 6.1 Detail packs (priority verticals)

#### 1. BĐS — `BDS` (regulated-adjacent, high)

| Checkpoint | Rule ID pattern | Evidence |
|------------|-----------------|----------|
| Xác định nhu cầu (quận, loại căn, ngân sách) | `rule.bds.discovery.need` | transcript |
| Nêu pháp lý / sổ (không khẳng định sai) | `rule.bds.legal.disclaimer` | transcript |
| Báo giá hoặc khoảng giá có đơn vị | `rule.bds.price.disclose` | transcript |
| Mời xem nhà / booking lịch | `rule.bds.close.booking` | transcript+crm |
| Không hứa lợi nhuận chắc chắn | `rule.bds.compliance.no_guaranteed_roi` | transcript |

Objections: `OBJ_PRICE`, `OBJ_LEGAL`, `OBJ_SPOUSE`, `OBJ_COMPARE`.  
Revenue leak: no booking after hard BS; no alternative inventory after price objection.

#### 2. Spa / Thẩm mỹ — `SPA` (medium)

Checkpoints: tư vấn liệu trình phù hợp; chống chỉ định hỏi-đáp; giá liệu trình; đặt lịch; chăm sóc sau liệu trình.  
Rules: `rule.spa.*`. Objections: price, time, trust, spouse. Leak: no upsell package after interest.

#### 3. Nha khoa — `DENTAL` (high)

Checkpoints: triệu chứng / nhu cầu; không chẩn đoán vượt thẩm quyền điện thoại; báo khoảng giá; đặt lịch khám; nhắc chuẩn bị.  
Rules: `rule.dental.*`. Compliance: no guaranteed treatment outcome.

#### 4. Bảo hiểm — `INS` (regulated, high)

Checkpoints: nhu cầu bảo vệ; giải thích quyền lợi không phóng đại; loại trừ cơ bản; phí / kỳ đóng; xác nhận đồng ý tư vấn; không ép mua.  
Rules: `rule.ins.*`. Objections: trust, price, spouse, need. Leak: no callback schedule after soft interest.

#### 5. Giáo dục / khóa học — `EDU` (medium)

Checkpoints: mục tiêu học; trình độ; lịch học; học phí + ưu đãi có điều kiện; chốt đăng ký / giữ chỗ.  
Rules: `rule.edu.*`.

#### 6. Ô tô / showroom — `AUTO` (high)

Checkpoints: nhu cầu dòng xe; ngân sách; lái thử; báo giá niêm yết + CTKM; trao đổi biển/phí (nếu policy cho phép).  
Rules: `rule.auto.*`.

#### 7. Mỹ phẩm — `COSMETIC` (medium)

Checkpoints: loại da / nhu cầu; thành phần caution; giá; combo; địa chỉ giao; xác nhận đồng ý nhận.  
Rules: `rule.cosmetic.*`.

#### 8. Gia dụng — `HOMEAPPL` (medium)

Checkpoints: nhu cầu không gian; công suất / size; bảo hành; giá; giao lắp.  
Rules: `rule.homeappl.*`.

#### 9. Thực phẩm / TPCN — `FOOD_SUPP` (high)

Checkpoints: nhu cầu sức khỏe; **không** claim chữa bệnh vượt label; giá; liều dùng theo nhãn; đồng ý nhận.  
Rules: `rule.food_supp.*`. Compliance heavy.

#### 10. Điện máy — `ELECTRONICS` (medium)

Checkpoints: model; thông số; bảo hành; giá / trả góp; giao hàng.  
Rules: `rule.electronics.*`.

### 6.2 Full catalog (50)

| # | Code | Name VI | Risk | Mandatory close artifact | Primary leak codes |
|---|------|---------|------|--------------------------|--------------------|
| 1 | `BDS` | Bất động sản | high | booking xem nhà | `LEAK_NO_BOOKING` |
| 2 | `SPA` | Spa / thẩm mỹ | medium | lịch liệu trình | `LEAK_NO_APPOINTMENT` |
| 3 | `DENTAL` | Nha khoa | high | lịch khám | `LEAK_NO_APPOINTMENT` |
| 4 | `INS` | Bảo hiểm | high | đồng ý tư vấn / hồ sơ | `LEAK_NO_CALLBACK` |
| 5 | `EDU` | Giáo dục | medium | đăng ký / giữ chỗ | `LEAK_NO_ENROLL` |
| 6 | `AUTO` | Ô tô | high | lịch lái thử | `LEAK_NO_TESTDRIVE` |
| 7 | `COSMETIC` | Mỹ phẩm | medium | địa chỉ + COD/CK | `LEAK_NO_ADDRESS` |
| 8 | `HOMEAPPL` | Gia dụng | medium | đơn giao lắp | `LEAK_NO_DELIVERY_CONFIRM` |
| 9 | `FOOD_SUPP` | TPCN / thực phẩm | high | đơn + xác nhận đồng ý | `LEAK_NO_CONSENT` |
| 10 | `ELECTRONICS` | Điện máy | medium | đơn model + BH | `LEAK_NO_MODEL_CONFIRM` |
| 11 | `FASHION` | Thời trang | low | size + địa chỉ | `LEAK_NO_SIZE` |
| 12 | `FURNITURE` | Nội thất | medium | khảo sát / đặt cọc | `LEAK_NO_SURVEY` |
| 13 | `TELCO` | Viễn thông SIM/gói | medium | xác nhận CCCD/eSIM flow | `LEAK_NO_KYC_NEXTSTEP` |
| 14 | `BANK_LOAN` | Vay tiêu dùng | high | nhu cầu + lịch tư vấn | `LEAK_NO_COMPLIANCE_SCRIPT` |
| 15 | `CREDIT_CARD` | Thẻ tín dụng | high | điều kiện phí / lãi | `LEAK_FEE_UNDISCLOSED` |
| 16 | `INVEST_APP` | App đầu tư | high | rủi ro disclaimer | `LEAK_NO_RISK_DISCLAIMER` |
| 17 | `TRAVEL` | Du lịch / tour | medium | ngày + số khách | `LEAK_NO_DATE_LOCK` |
| 18 | `HOTEL` | Khách sạn | medium | đêm lưu trú | `LEAK_NO_HOLD` |
| 19 | `AIRLINE` | Vé máy bay | medium | chặng + hành khách | `LEAK_PII_INCOMPLETE` |
| 20 | `LOGISTICS` | Chuyển phát | medium | COD / kích thước | `LEAK_NO_PICKUP` |
| 21 | `SOFTWARE_SAAS` | Phần mềm SaaS | medium | trial / demo | `LEAK_NO_DEMO` |
| 22 | `IT_SERVICE` | Dịch vụ IT | medium | khảo sát hiện trạng | `LEAK_NO_DISCOVERY` |
| 23 | `MARKETING_AGENCY` | Agency marketing | medium | brief + ngân sách | `LEAK_NO_BRIEF` |
| 24 | `PRINT_ADS` | In ấn / quảng cáo | low | file + số lượng | `LEAK_NO_SPECS` |
| 25 | `EVENT` | Tổ chức sự kiện | medium | ngày + quy mô | `LEAK_NO_DATE_LOCK` |
| 26 | `WEDDING` | Cưới hỏi | medium | gói + đặt cọc | `LEAK_NO_DEPOSIT_ASK` |
| 27 | `PET` | Thú cưng | low | dịch vụ / SP | `LEAK_NO_APPOINTMENT` |
| 28 | `VET` | Thú y | high | triệu chứng + lịch | `LEAK_NO_TRIAGE` |
| 29 | `PHARMA_OTC` | OTC nhà thuốc | high | chống chỉ định hỏi | `LEAK_MEDICAL_CLAIM` |
| 30 | `CLINIC` | Phòng khám Đa khoa | high | triệu chứng + lịch | `LEAK_NO_APPOINTMENT` |
| 31 | `GYM` | Gym / fitness | medium | gói + lịch tour | `LEAK_NO_TRIAL` |
| 32 | `YOGA` | Yoga / wellness | low | lịch class | `LEAK_NO_APPOINTMENT` |
| 33 | `CLEANING` | Vệ sinh nhà | low | khảo sát diện tích | `LEAK_NO_SURVEY` |
| 34 | `REPAIR` | Sửa chữa tại nhà | medium | chẩn đoán sơ bộ | `LEAK_NO_SLOT` |
| 35 | `SOLAR` | Điện mặt trời | high | khảo sát mái | `LEAK_NO_SURVEY` |
| 36 | `INSURANCE_CAR` | Bảo hiểm xe | high | thông tin xe + phí | `LEAK_FEE_UNDISCLOSED` |
| 37 | `INSURANCE_HEALTH` | BH sức khỏe | high | quyền lợi / loại trừ | `LEAK_NO_EXCLUSION` |
| 38 | `REALTY_RENT` | Cho thuê BĐS | medium | nhu cầu thuê + lịch xem | `LEAK_NO_BOOKING` |
| 39 | `JOB_RECRUIT` | Tuyển dụng / headhunt | medium | JD fit + lịch PV | `LEAK_NO_INTERVIEW` |
| 40 | `B2B_WHOLESALE` | Bán sỉ B2B | medium | SL + VAT / hợp đồng | `LEAK_NO_MOQ_CONFIRM` |
| 41 | `AGRI` | Nông sản / vật tư | medium | mùa vụ + giao | `LEAK_NO_DELIVERY_CONFIRM` |
| 42 | `CONSTRUCTION` | VLXD / xây dựng | medium | khối lượng ước tính | `LEAK_NO_SPECS` |
| 43 | `SECURITY` | Camera / an ninh | medium | khảo sát điểm | `LEAK_NO_SURVEY` |
| 44 | `SMART_HOME` | Nhà thông minh | medium | demo giải pháp | `LEAK_NO_DEMO` |
| 45 | `KID_TOY` | Đồ chơi trẻ em | low | độ tuổi + an toàn | `LEAK_NO_AGE_CHECK` |
| 46 | `MOM_BABY` | Mẹ & bé | medium | độ tuổi bé + SP | `LEAK_NO_AGE_CHECK` |
| 47 | `JEWELRY` | Trang sức | medium | chất liệu + bảo hành | `LEAK_NO_MATERIAL` |
| 48 | `LUXURY` | Hàng hiệu | high | authentic / nguồn | `LEAK_TRUST_GAP` |
| 49 | `NGO_DONATION` | Gây quỹ / từ thiện | high | minh bạch mục đích | `LEAK_NO_TRANSPARENCY` |
| 50 | `PUBLIC_UTIL` | Điện nước / tiện ích | medium | mã KH + lịch hẹn | `LEAK_PII_INCOMPLETE` |

---

## 7. Per-industry checkpoint matrix (compressed standard)

Every industry pack **must** implement these checkpoint families (codes stored in `sop.checkpoints`):

| Family | Code suffix | Description |
|--------|-------------|-------------|
| Identity | `CP_ID_PERSON` | Đúng người nghe / quyết định |
| Consent | `CP_CONSENT_CONTINUE` | Xin phép tư vấn |
| Need | `CP_NEED` | Nhu cầu tối thiểu theo ngành |
| Value | `CP_VALUE` | Giá trị / sp đúng need |
| Price | `CP_PRICE` | Giá / phí / điều kiện |
| Objection | `CP_OBJ_HANDLE` | Xử lý từ chối chính |
| Compliance | `CP_COMPLIANCE` | Claim / disclaimer theo risk_tier |
| Close | `CP_CLOSE` | CTA ngành (bảng §6.2) |
| Confirm | `CP_CONFIRM` | Xác nhận thông tin đơn / lịch |
| Outro | `CP_OUTRO` | Next step rõ |

Example binding for `FOOD_SUPP`:

```json
{
  "sop_id": "sop.food_supp.primary.v3",
  "bindings": [
    {"checkpoint": "CP_COMPLIANCE", "rule_id": "rule.food_supp.no_disease_claim", "required": true},
    {"checkpoint": "CP_PRICE", "rule_id": "rule.food_supp.price.disclose", "required": true},
    {"checkpoint": "CP_CLOSE", "rule_id": "rule.food_supp.close.consent_receive", "required": true}
  ]
}
```

---

## 8. Industry-specific objection priority

| Industry codes | Priority objections (ordered) |
|----------------|-------------------------------|
| BDS, REALTY_RENT, SOLAR | PRICE, LEGAL, SPOUSE, COMPARE |
| INS, INSURANCE_*, BANK_LOAN, CREDIT_CARD | TRUST, PRICE, NEED, SPOUSE |
| SPA, DENTAL, CLINIC, VET | PRICE, TIME, TRUST, SPOUSE |
| EDU, GYM, YOGA | TIME, PRICE, NEED |
| COSMETIC, FOOD_SUPP, PHARMA_OTC, MOM_BABY | TRUST, QUALITY, PRICE |
| AUTO, ELECTRONICS, HOMEAPPL | PRICE, COMPARE, QUALITY, DELIVERY |
| SOFTWARE_SAAS, IT_SERVICE, MARKETING_AGENCY | PRICE, NEED, TIME, COMPARE |
| TRAVEL, HOTEL, AIRLINE, EVENT, WEDDING | PRICE, TIME, SPOUSE |
| NGO_DONATION, PUBLIC_UTIL | TRUST, NEED |

Rule packs must include handlers for the top 4 per industry; others fall through to generic `rule.common.objection.*`.

---

## 9. Revenue leak model per SOP

```json
{
  "leak_code": "LEAK_NO_BOOKING",
  "when": {
    "all": [
      {"feature": "buying_signals.hard", "contains_any": ["BS_BOOK_SLOT", "BS_ASK_CONTRACT"]},
      {"feature": "agent.intents", "not_contains": "agent.schedule"}
    ]
  },
  "estimate": {
    "method": "tenant_avg_deal_value * probability_curve",
    "probability_default": 0.55
  },
  "on_missing_feature": "Insufficient Evidence"
}
```

Estimates use tenant CRM averages from DB (`tenant_economics.avg_deal_value`); never hard-coded currency constants in services.

---

## 10. APIs

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/industries` | authenticated | List 50 industries |
| `GET` | `/api/v1/industries/{code}/sop/active` | `qa_*` | Active SOP + bindings |
| `POST` | `/api/v1/sop` | `qa_manager` | Create draft |
| `PATCH` | `/api/v1/sop/{sop_id}` | `qa_manager` | Edit draft |
| `POST` | `/api/v1/sop/{sop_id}/publish` | `qa_manager` | Publish + audit |
| `GET` | `/api/v1/sop/{sop_id}/diff/{other}` | `qa_*` | Version diff |
| `POST` | `/api/v1/tenants/{id}/industry-packs/{code}/enable` | `admin` | Enable pack |

Response for analyze remains global `AnalysisResult` with `meta.sop_id` + `meta.industry_code`.

---

## 11. Seed & migration procedure

1. Migration `sop_001_industries.sql` inserts 50 rows.
2. Migration `sop_002_templates.sql` inserts platform SOP v1 per industry with checksum.
3. Migration `rulebook_0xx_industry_{code}.sql` inserts rules + bindings (batched).
4. CI job `validate_sop_bindings` fails if any required checkpoint lacks a rule.
5. Canary: enable packs on internal tenant → score 10k historical calls → compare distribution drift.

---

## 12. Multi-tenant customization

- Tenants clone platform SOP → new `sop_id` with `tenant_id` set.
- Custom rules must namespace `rule.tenant.{tenant_slug}.*`.
- Platform rule updates do **not** auto-mutate tenant clones; Admin shows “upstream diff available”.
- All customizations audited.

---

## 13. Acceptance criteria

1. All 50 industries present in `sop.industries` with risk_tier.
2. Each active SOP has 6 stages and ≥10 rule bindings.
3. Regulated packs (`INS`, `FOOD_SUPP`, `PHARMA_OTC`, `BANK_LOAN`, `CREDIT_CARD`, `INVEST_APP`, `NGO_DONATION`) include explicit compliance rules.
4. Publishing without bindings for required checkpoints is rejected.
5. Score path never reads industry if/else in application code — only SOP/Rulebook data.
6. AnalysisResult always populated with the seven mandatory fields.

---

## 14. Service layer

```python
class SopSelectionService:
    async def resolve(self, tenant_id: UUID, call: Call) -> SopDefinition:
        """CRM industry → tenant override → platform default; else error."""

class SopScoringService:
    async def score(self, features: VcieFeatures, sop: SopDefinition) -> AnalysisResult:
        """Delegates to RuleEvaluationService; attaches coaching & revenue_leak."""
```

DI-registered in composition root; repositories are the only DB access path.
