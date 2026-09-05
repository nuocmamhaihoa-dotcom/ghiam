# Sprint Reports — AI QA TELESALE ENTERPRISE

## Sprint 1 — Schema / Scripts / Validation
- Status: **DONE**
- Deliverables: `datasets/validation/schema.json`, `datasets/scripts/generate_vecd.py`, quality gates
- Quality Gate: PASS (schema + generator compile)

## Sprint 2 — Customer Dataset
- Status: **DONE**
- Deliverables: `datasets/customers/customers_100000.jsonl` (100,000)
- Dialect balance: Bắc/Trung/Nam theo target
- Quality Gate: PASS (`qg_customers.json`)

## Sprint 3 — Agent Dataset
- Status: **DONE**
- Deliverables: `datasets/agents/agents_50000.jsonl` (50,000)
- Quality Gate: PASS (`qg_agents.json`)

## Sprint 4 — Intent / Emotion / Buying Signals
- Status: **DONE**
- Deliverables:
  - intents 1,000
  - emotions 500
  - buying_signals 500
- Quality Gate: PASS

## Sprint 5 — Objection / Root Cause
- Status: **DONE**
- Deliverables:
  - objections 5,000
  - root_causes 500 (+ graphs)
- Quality Gate: PASS

## Sprint 6 — Conversation Library
- Status: **DONE**
- Deliverables: conversations 10,000 (8–30 turns)
- Quality Gate: PASS

## Sprint 7 — QA Benchmark
- Status: **DONE**
- Deliverables: qa_benchmark 10,000 (AI vs Human scores)
- Quality Gate: PASS

## Sprint 8 — Golden Calls
- Status: **DONE**
- Deliverables: golden_calls 500 (immutable + checksum)
- Quality Gate: PASS

## Sprint 9 — Rulebook / SOP / Platform Wiring
- Status: **DONE**
- Deliverables:
  - rules_1000.jsonl (exactly 1000, deduped every 50)
  - sop_50.jsonl
  - FastAPI backend Clean Architecture
  - enterprise-web Next.js console
  - docs 01–15 including Root Cause + Coaching engines
- Quality Gate: PASS (`qg_rules_1000.json`)

## Risks
- Production ASR/diarization providers chưa gắn credential thật → ensemble trả `Insufficient Evidence` khi thiếu evidence (đúng Hiến pháp).
- Dataset synthetic cần human calibration trước khi dùng làm ground truth pháp lý.

## Technical Debt
- Đồng bộ naming `ai-brain` vs `ai-brain` paths trong seed scripts.
- Thêm CI job chạy `generate_vecd.py --dry-run` + pytest backend trên mỗi PR.

## Next Sprint Plan
1. Wire Whisper + diarization adapters (provider interfaces only; rules vẫn từ DB).
2. Import VECD vào PostgreSQL qua `export_postgres`.
3. QA Calibration UI trên human/AI score pairs.
4. Load test scoring pipeline 1M calls (queue workers).
