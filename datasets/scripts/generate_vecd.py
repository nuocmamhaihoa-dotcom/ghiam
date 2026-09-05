#!/usr/bin/env python3
"""VECD Factory — Vietnamese Enterprise Conversation Dataset.

Deterministic generation with dialect/industry balance and quality gates.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
REPORTS = ROOT / "reports"
SEED = 20260905

DIALECTS = [("bac", 0.35), ("trung", 0.20), ("nam", 0.45)]
INDUSTRIES = [
    "bds", "spa", "nha_khoa", "giao_duc", "bao_hiem", "o_to", "my_pham",
    "gia_dung", "thuc_pham", "dien_may", "noi_that", "logistics", "du_lich",
    "tai_chinh", "fitness", "camera", "dien_nuoc", "thiet_bi_y_te", "dich_vu_dn",
]
INDUSTRY_LABEL = {
    "bds": "bất động sản", "spa": "spa", "nha_khoa": "nha khoa",
    "giao_duc": "giáo dục", "bao_hiem": "bảo hiểm", "o_to": "ô tô",
    "my_pham": "mỹ phẩm", "gia_dung": "gia dụng", "thuc_pham": "thực phẩm",
    "dien_may": "điện máy", "noi_that": "nội thất", "logistics": "logistics",
    "du_lich": "du lịch", "tai_chinh": "tài chính", "fitness": "fitness",
    "camera": "camera", "dien_nuoc": "điện nước", "thiet_bi_y_te": "thiết bị y tế",
    "dich_vu_dn": "dịch vụ doanh nghiệp",
}
STAGES = [
    "opening", "rapport", "discovery", "qualification", "presentation",
    "pricing", "objection", "closing", "voice", "compliance",
]
EMOTIONS = [
    "interested", "curious", "hesitation", "trust", "angry",
    "confused", "frustration", "neutral", "exit_intent",
]
UTTERANCE_FIELDS = [
    "id", "conversation_id", "speaker", "text", "dialect", "industry", "stage",
    "intent", "emotion", "objection_type", "buying_signal", "confidence",
    "recommended_response", "root_cause_if_failed",
]

CUSTOMER_BANK = {
    "bac": [
        "Anh/chị ơi bên mình đang bán gì thế?",
        "Giá thế nào ạ, em xem đã.",
        "Để em hỏi vợ/chồng cái đã nhé.",
        "Bây giờ em đang bận, gọi lại sau được không?",
        "Có bảo hành không anh/chị?",
        "Bên kia rẻ hơn cơ.",
        "Công ty mình ở đâu vậy?",
        "Thanh toán thế nào ạ?",
        "Có hóa đơn VAT không?",
        "Bao giờ giao được?",
        "Em chưa tin lắm, nghe giống quảng cáo quá.",
        "Có giảm thêm không ạ?",
        "Em đang so sánh với đối thủ.",
        "Để cuối tháng em tính.",
        "Nghe cũng hay nhưng em chưa cần gấp.",
    ],
    "trung": [
        "Dạ bên mình bán cái chi vậy?",
        "Giá bao nhiêu rứa?",
        "Để em hỏi người nhà đã.",
        "Em đang bận, gọi lại sau nghe.",
        "Có bảo hành chi không?",
        "Bên kia rẻ hơn đó.",
        "Công ty ở đâu vậy?",
        "Trả tiền kiểu chi?",
        "Có xuất hóa đơn không?",
        "Khi mô giao?",
        "Em chưa tin lắm đâu.",
        "Giảm thêm được không?",
        "Em đang so sánh mấy chỗ.",
        "Để cuối tháng tính.",
        "Hay đó nhưng chưa cần gấp.",
    ],
    "nam": [
        "Anh/chị ơi bên mình bán gì vậy?",
        "Giá sao vậy em xem đã.",
        "Để em hỏi vợ/chồng cái đã nha.",
        "Em đang bận, gọi lại sau được không?",
        "Có bảo hành không?",
        "Bên kia rẻ hơn á.",
        "Công ty mình ở đâu vậy?",
        "Thanh toán sao vậy?",
        "Có hóa đơn không?",
        "Bao giờ giao được?",
        "Em chưa tin lắm, nghe như quảng cáo.",
        "Có giảm thêm không?",
        "Em đang so với bên khác.",
        "Để cuối tháng em tính.",
        "Hay nhưng chưa cần gấp.",
    ],
}

AGENT_BANK = {
    "bac": [
        "Em chào anh/chị, em là tư vấn viên bên {ind}.",
        "Dạ em xin phép hỏi nhu cầu hiện tại của anh/chị ạ?",
        "Em hiểu lo ngại về giá, để em chia giá trị cụ thể ạ.",
        "Anh/chị đang so sánh tiêu chí nào quan trọng nhất ạ?",
        "Bên em có chính sách bảo hành và đổi trả rõ ràng ạ.",
        "Em gửi lịch demo ngắn 10 phút được không ạ?",
        "Anh/chị quyết định chính là ai trong gia đình ạ?",
        "Em tóm tắt lại nhu cầu và đề xuất phù hợp ạ.",
        "Nếu phù hợp, mình chốt lịch giao/ký trong tuần này ạ?",
        "Em cảm ơn anh/chị đã dành thời gian ạ.",
    ],
    "trung": [
        "Em chào anh/chị, em tư vấn bên {ind}.",
        "Dạ em hỏi nhu cầu hiện tại của anh/chị được không?",
        "Em hiểu lo về giá, để em nói rõ giá trị.",
        "Anh/chị đang so tiêu chí mô quan trọng nhất?",
        "Bên em có bảo hành và đổi trả rõ ràng.",
        "Em gửi lịch demo 10 phút được không?",
        "Người quyết định chính là ai ạ?",
        "Em tóm lại nhu cầu và đề xuất phù hợp.",
        "Nếu ổn, mình chốt lịch trong tuần này được không?",
        "Em cảm ơn anh/chị đã nghe ạ.",
    ],
    "nam": [
        "Em chào anh/chị, em tư vấn bên {ind} ạ.",
        "Dạ em hỏi nhu cầu hiện tại của anh/chị nha?",
        "Em hiểu lo về giá, để em nói rõ giá trị.",
        "Anh/chị đang so tiêu chí nào quan trọng nhất?",
        "Bên em có bảo hành và đổi trả rõ ràng.",
        "Em gửi lịch demo 10 phút được không?",
        "Người quyết định chính là ai vậy ạ?",
        "Em tóm lại nhu cầu và đề xuất phù hợp.",
        "Nếu ổn, mình chốt lịch trong tuần này nha?",
        "Em cảm ơn anh/chị đã nghe ạ.",
    ],
}

INTENT_SEEDS = [
    ("price_concern", "Lo ngại giá", ["đắt", "mắc", "giảm", "rẻ"]),
    ("trust_issue", "Thiếu tin tưởng", ["lừa", "uy tín", "công ty"]),
    ("delay", "Hoãn quyết định", ["sau", "để hôm khác", "cuối tháng"]),
    ("decision_maker_missing", "Thiếu người quyết", ["hỏi vợ", "hỏi chồng", "hỏi sếp"]),
    ("competitor_comparison", "So đối thủ", ["bên kia", "chỗ khác", "rẻ hơn"]),
    ("busy", "Đang bận", ["bận", "gọi lại", "không tiện"]),
    ("interested", "Quan tâm", ["hay", "cho xem", "tìm hiểu"]),
    ("ready_to_buy", "Sẵn sàng mua", ["chốt", "đặt", "thanh toán"]),
    ("fake_agreement", "Đồng ý giả", ["để xem đã", "ok nhưng"]),
    ("soft_rejection", "Từ chối mềm", ["chưa cần", "để sau"]),
    ("warranty_ask", "Hỏi bảo hành", ["bảo hành", "đổi trả"]),
    ("delivery_ask", "Hỏi giao hàng", ["giao", "bao giờ nhận"]),
    ("invoice_ask", "Hỏi hóa đơn", ["hóa đơn", "VAT"]),
    ("payment_ask", "Hỏi thanh toán", ["thanh toán", "trả góp"]),
    ("need_more_info", "Cần thêm thông tin", ["chi tiết", "tài liệu"]),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str, n: int) -> str:
    return f"{prefix}-{n:06d}"


def pick_dialect(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for d, p in DIALECTS:
        acc += p
        if r <= acc:
            return d
    return "nam"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def quality_gate(
    name: str,
    rows: list[dict],
    required_fields: list[str],
    balances: dict[str, list[str]] | None = None,
) -> dict:
    errors: list[str] = []
    if not rows:
        errors.append("empty dataset")
    for i, row in enumerate(rows):
        for field in required_fields:
            if field not in row or row[field] in (None, ""):
                errors.append(f"missing_field:{field}:row:{i}")
                if len(errors) > 80:
                    break
        if len(errors) > 80:
            break
    ids = [r.get("id") for r in rows if "id" in r]
    if ids and len(ids) != len(set(ids)):
        errors.append(f"duplicate_ids:{len(ids) - len(set(ids))}")
    balance_report: dict[str, Any] = {}
    if balances:
        for field, expected_keys in balances.items():
            c = Counter(str(r.get(field)) for r in rows)
            balance_report[field] = dict(c)
            for k in expected_keys:
                if c.get(k, 0) == 0:
                    errors.append(f"balance_zero:{field}:{k}")
    report = {
        "gate": name,
        "count": len(rows),
        "ok": len(errors) == 0,
        "errors": errors[:100],
        "balances": balance_report,
        "checked_at": utc_now(),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"qg_{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    status = "PASS" if not errors else "FAIL"
    print(f"[QG {status}] {name}: {len(rows)} rows; errors={len(errors)}")
    return report


def dialect_balance_ok(rows: list[dict], field: str = "dialect", tol: float = 0.08) -> bool:
    c = Counter(r[field] for r in rows)
    total = sum(c.values()) or 1
    expected = {"bac": 0.35, "trung": 0.20, "nam": 0.45}
    return all(abs(c.get(k, 0) / total - p) <= tol for k, p in expected.items())


def gen_intents(n: int = 1000) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        code, name, triggers = INTENT_SEEDS[(i - 1) % len(INTENT_SEEDS)]
        rows.append({
            "id": uid("INT", i),
            "code": f"{code}_{i:04d}",
            "name": f"{name} #{i}",
            "description": f"Intent {name} trong ngữ cảnh telesale Việt Nam.",
            "triggers": triggers + [f"biến thể {i % 17}"],
            "counter_examples": [f"Không phải {name.lower()} — ngữ cảnh khác {i}"],
            "confidence_threshold": round(0.55 + (i % 30) / 100, 2),
            "coaching": f"Khi phát hiện {name}: xác nhận lại, hỏi sâu, đưa bằng chứng cụ thể.",
            "linked_rule_categories": [STAGES[i % len(STAGES)]],
            "industry_affinity": [
                INDUSTRIES[i % len(INDUSTRIES)],
                INDUSTRIES[(i * 3) % len(INDUSTRIES)],
            ],
        })
    return rows


def gen_emotions(n: int = 500) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        e = EMOTIONS[(i - 1) % len(EMOTIONS)]
        rows.append({
            "id": uid("EMO", i),
            "code": e,
            "name": e.replace("_", " ").title(),
            "trigger": f"Tín hiệu văn bản/giọng nói gắn với {e} biến thể {i}",
            "voice_pattern": f"pitch/energy pattern for {e} #{i}",
            "text_pattern": f"lexical cues for {e} #{i}",
            "coaching": f"Khi khách {e}: điều chỉnh nhịp, đồng cảm, xác nhận nhu cầu.",
            "linked_stages": [STAGES[i % len(STAGES)]],
        })
    return rows


def gen_buying_signals(n: int = 500) -> list[dict]:
    seeds = [
        "Bao giờ giao?", "Có bảo hành không?", "Thanh toán sao?", "Có hóa đơn không?",
        "Có trả góp không?", "Ship tận nơi không?", "Màu/phiên bản nào còn?",
        "Giảm nữa được không nếu chốt hôm nay?", "Gửi hợp đồng mẫu được không?",
        "Ai lắp đặt vậy?", "Có hỗ trợ sau bán không?", "Demo trực tiếp được không?",
    ]
    rows = []
    for i in range(1, n + 1):
        text = seeds[(i - 1) % len(seeds)]
        if i > len(seeds):
            text = f"{text} (biến thể {i})"
        rows.append({
            "id": uid("BUY", i),
            "text": text,
            "strength_score": round(0.4 + (i % 60) / 100, 2),
            "confidence": round(0.5 + (i % 45) / 100, 2),
            "next_step": "Chốt lịch/điều kiện giao dịch, xác nhận quyết định cuối.",
            "linked_intent": f"ready_to_buy_{(i % 50) + 1:04d}",
            "dialect": DIALECTS[(i - 1) % 3][0],
            "industry": INDUSTRIES[(i - 1) % len(INDUSTRIES)],
        })
    return rows


def gen_objections(n: int = 5000) -> list[dict]:
    groups = {
        "price": ["Đắt quá.", "Mắc quá.", "Có giảm không?", "Vượt ngân sách."],
        "time": ["Để hôm khác.", "Đang bận.", "Cuối tháng tính.", "Gọi lại sau."],
        "trust": ["Có lừa không?", "Công ty ở đâu?", "Có bảo hành không?", "Chưa thấy review."],
        "decision": ["Để hỏi vợ.", "Để hỏi chồng.", "Để hỏi sếp.", "Phải họp nội bộ."],
        "competitor": ["Bên kia rẻ hơn.", "Đang dùng bên khác.", "Đối thủ khuyến mãi."],
    }
    keys = list(groups.keys())
    rows = []
    for i in range(1, n + 1):
        g = keys[(i - 1) % len(keys)]
        base = groups[g][(i - 1) % len(groups[g])]
        dialect = DIALECTS[(i - 1) % 3][0]
        rows.append({
            "id": uid("OBJ", i),
            "group": g,
            "text": f"{base} [{dialect}/{INDUSTRIES[(i - 1) % len(INDUSTRIES)]}#{i}]",
            "hidden_meaning": f"Khách đang biểu đạt {g} — cần khám phá nguyên nhân gốc.",
            "root_cause": f"RC-{g}-{(i % 100) + 1:03d}",
            "good_response": "Đồng cảm → hỏi sâu → đưa bằng chứng/giá trị → đề xuất bước tiếp.",
            "forbidden_response": "Tranh cãi giá / ép chốt / phủ nhận cảm xúc khách.",
            "practice_exercise": f"Roleplay {g} #{i}: luyện 3 vòng phản hồi có evidence.",
            "dialect": dialect,
            "industry": INDUSTRIES[(i - 1) % len(INDUSTRIES)],
            "linked_rule_category": "objection",
            "confidence": round(0.6 + (i % 35) / 100, 2),
        })
    return rows


def gen_root_causes(n: int = 500) -> list[dict]:
    chain_nodes = [
        "No Sale", "Poor Discovery", "Early Pricing", "Weak Value Building",
        "Ignored Objection", "No Closing Attempt", "Trust Gap", "Wrong Persona",
        "Feature Dump", "No Decision Maker", "Overpromise", "Compliance Risk",
    ]
    rows = []
    for i in range(1, n + 1):
        start = (i - 1) % len(chain_nodes)
        chain = [chain_nodes[(start + k) % len(chain_nodes)] for k in range(5)]
        rows.append({
            "id": uid("RC", i),
            "code": f"RC-{i:04d}",
            "name": f"{chain[0]} ← {chain[1]}",
            "graph": [{"from": chain[j], "to": chain[j + 1]} for j in range(len(chain) - 1)],
            "trigger": f"Pattern {chain[1]} xuất hiện sớm trong stage {STAGES[i % len(STAGES)]}",
            "confidence": round(0.55 + (i % 40) / 100, 2),
            "evidence_requirements": ["utterance_span", "stage_tag", "rule_hit"],
            "coaching": f"Sửa {chain[1]} trước: checklist discovery + trì hoãn pricing.",
            "linked_rules": [f"R-{(i % 1000) + 1:04d}"],
            "industry": INDUSTRIES[(i - 1) % len(INDUSTRIES)],
        })
    return rows


def expand_utterances(speaker: str, target: int) -> list[dict]:
    rng = random.Random(SEED + (11 if speaker == "customer" else 29))
    bank = CUSTOMER_BANK if speaker == "customer" else AGENT_BANK
    rows: list[dict] = []
    i = 0
    while len(rows) < target:
        i += 1
        dialect = pick_dialect(rng)
        industry = INDUSTRIES[(i - 1) % len(INDUSTRIES)]
        stage = STAGES[(i - 1) % len(STAGES)]
        text = bank[dialect][(i - 1) % len(bank[dialect])]
        if speaker == "agent":
            text = text.format(ind=INDUSTRY_LABEL[industry])
        text = f"{text} #{i}"
        intent = INTENT_SEEDS[(i - 1) % len(INTENT_SEEDS)][0]
        emotion = EMOTIONS[(i - 1) % len(EMOTIONS)]
        obj = "none" if speaker == "agent" else (
            ["price", "time", "trust", "decision", "competitor", "none"][(i - 1) % 6]
        )
        buy = speaker == "customer" and (i % 17 == 0)
        rows.append({
            "id": uid("CUS" if speaker == "customer" else "AGT", i),
            "conversation_id": uid("CONV", ((i - 1) % 10000) + 1),
            "speaker": speaker,
            "text": text,
            "dialect": dialect,
            "industry": industry,
            "stage": stage,
            "intent": intent,
            "emotion": emotion,
            "objection_type": obj,
            "buying_signal": buy,
            "confidence": round(0.5 + (i % 50) / 100, 2),
            "recommended_response": "Xác nhận + hỏi sâu + đưa evidence phù hợp stage.",
            "root_cause_if_failed": f"RC-{(i % 500) + 1:04d}",
        })
    return rows


def gen_conversations(n: int = 10000) -> list[dict]:
    rng = random.Random(SEED + 77)
    kinds = [
        "success", "failure", "hot_lead", "cold_lead", "early_pricing",
        "good_discovery", "poor_discovery", "angry_customer", "friendly_customer",
    ]
    rows = []
    for i in range(1, n + 1):
        dialect = pick_dialect(rng)
        industry = INDUSTRIES[(i - 1) % len(INDUSTRIES)]
        turns = 8 + (i % 23)
        kind = kinds[(i - 1) % len(kinds)]
        utterances = []
        for t in range(turns):
            speaker = "agent" if t % 2 == 0 else "customer"
            bank = AGENT_BANK if speaker == "agent" else CUSTOMER_BANK
            text = bank[dialect][t % len(bank[dialect])]
            if speaker == "agent":
                text = text.format(ind=INDUSTRY_LABEL[industry])
            utterances.append({
                "turn": t + 1,
                "speaker": speaker,
                "text": text,
                "stage": STAGES[min(t // 3, len(STAGES) - 1)],
                "emotion": EMOTIONS[(t + i) % len(EMOTIONS)],
                "intent": INTENT_SEEDS[(t + i) % len(INTENT_SEEDS)][0],
            })
        rows.append({
            "id": uid("CONV", i),
            "dialect": dialect,
            "industry": industry,
            "kind": kind,
            "turn_count": turns,
            "outcome": "won" if kind in (
                "success", "hot_lead", "good_discovery", "friendly_customer"
            ) else "lost",
            "utterances": utterances,
            "linked_root_cause": uid("RC", ((i - 1) % 500) + 1),
            "qa_score_hint": 40 + (i % 60),
        })
    return rows


def gen_golden_calls(n: int = 500, conversations: list[dict] | None = None) -> list[dict]:
    src = conversations or []
    rows = []
    for i in range(1, n + 1):
        base = src[(i * 17) % len(src)] if src else None
        rows.append({
            "id": uid("GOLD", i),
            "immutable": True,
            "source_conversation_id": base["id"] if base else uid("CONV", i),
            "industry": base["industry"] if base else INDUSTRIES[i % len(INDUSTRIES)],
            "dialect": base["dialect"] if base else DIALECTS[i % 3][0],
            "benchmark_score": 90 + (i % 10),
            "why_golden": (
                "Discovery đầy đủ, value trước giá, xử lý objection có evidence, closing rõ ràng."
            ),
            "linked_rules": [f"R-{(i + k):04d}" for k in range(1, 6)],
            "checksum": hashlib.sha256(f"GOLD-{i}-{SEED}".encode()).hexdigest()[:16],
        })
    return rows


def gen_qa_benchmark(n: int = 10000) -> list[dict]:
    rng = random.Random(SEED + 99)
    reviewers = ["qa_lead_a", "qa_lead_b", "qa_lead_c", "qa_senior_d"]
    rows = []
    for i in range(1, n + 1):
        ai = 35 + (i % 60)
        human = max(0, min(100, ai + rng.randint(-12, 12)))
        rows.append({
            "id": uid("QAB", i),
            "conversation_id": uid("CONV", ((i - 1) % 10000) + 1),
            "ai_score": ai,
            "human_score": human,
            "difference": human - ai,
            "reviewer": reviewers[(i - 1) % len(reviewers)],
            "reason": "Calibration sample — so khớp rule hits và evidence spans.",
            "calibration_batch": f"batch-{(i - 1) // 500 + 1:03d}",
        })
    return rows


def gen_rules_1000() -> list[dict]:
    allocation = {
        "opening": 100, "rapport": 80, "discovery": 150, "qualification": 80,
        "presentation": 120, "pricing": 80, "objection": 200, "closing": 80,
        "voice": 60, "compliance": 50,
    }
    rows: list[dict] = []
    n = 0
    seen_names: set[str] = set()
    for cat, count in allocation.items():
        for j in range(1, count + 1):
            n += 1
            name = f"{cat.title()} Criterion {j}"
            if name in seen_names:
                name = f"{name} v2"
            seen_names.add(name)
            rows.append({
                "id": f"R-{n:04d}",
                "category": cat,
                "name": name,
                "description": f"Rule {cat} #{j}: đánh giá hành vi bắt buộc trong stage {cat}.",
                "weight": round(0.5 + (j % 10) * 0.1, 2),
                "pass": f"Có evidence rõ ràng thỏa điều kiện {cat}/{j}.",
                "fail": f"Thiếu evidence hoặc vi phạm điều kiện {cat}/{j}.",
                "evidence": ["transcript_span", "audio_timestamp", "speaker_tag"],
                "timestamp_required": True,
                "root_cause": f"RC-{cat}-{(j % 50) + 1:03d}",
                "coaching": f"Luyện {cat}: script chuẩn + roleplay + chấm lại.",
                "good_example": f"[Agent] Ví dụ đạt cho {cat} #{j}",
                "bad_example": f"[Agent] Ví dụ fail cho {cat} #{j}",
                "edge_cases": [
                    f"Khách cắt ngang {cat}",
                    f"Cuộc gọi nhiễu {cat}",
                    f"Đa quyết định viên {cat}",
                ],
                "confidence_logic": "confidence = evidence_coverage * rule_match_score",
                "json_mapping": {
                    "rule_id": f"R-{n:04d}",
                    "category": cat,
                    "weight_key": f"w_{cat}_{j}",
                },
            })
            if n % 50 == 0:
                assert len(rows) == len({r["id"] for r in rows})
                assert len(rows) == len({r["name"] for r in rows})
    assert len(rows) == 1000
    return rows


def main() -> None:
    print("=== VECD Factory start", utc_now())
    REPORTS.mkdir(parents=True, exist_ok=True)

    schema = {
        "utterance_required_fields": UTTERANCE_FIELDS,
        "dialect_mix": dict(DIALECTS),
        "industries": INDUSTRIES,
        "stages": STAGES,
        "generated_at": utc_now(),
        "seed": SEED,
    }
    (ROOT / "validation").mkdir(parents=True, exist_ok=True)
    (ROOT / "validation" / "schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    intents = gen_intents(1000)
    write_jsonl(ROOT / "intents" / "intents_1000.jsonl", intents)
    quality_gate("intents", intents, ["id", "code", "name", "triggers", "coaching"])

    emotions = gen_emotions(500)
    write_jsonl(ROOT / "emotions" / "emotions_500.jsonl", emotions)
    quality_gate("emotions", emotions, ["id", "code", "trigger", "coaching"])

    buys = gen_buying_signals(500)
    write_jsonl(ROOT / "buying_signals" / "buying_signals_500.jsonl", buys)
    quality_gate("buying_signals", buys, ["id", "text", "strength_score", "next_step"])

    objections = gen_objections(5000)
    write_jsonl(ROOT / "objections" / "objections_5000.jsonl", objections)
    quality_gate(
        "objections",
        objections,
        ["id", "group", "text", "good_response", "forbidden_response"],
    )

    root_causes = gen_root_causes(500)
    write_jsonl(ROOT / "root_causes" / "root_causes_500.jsonl", root_causes)
    quality_gate("root_causes", root_causes, ["id", "graph", "trigger", "coaching"])

    customers = expand_utterances("customer", 100000)
    write_jsonl(ROOT / "customers" / "customers_100000.jsonl", customers)
    quality_gate(
        "customers",
        customers,
        UTTERANCE_FIELDS,
        balances={"dialect": ["bac", "trung", "nam"], "industry": INDUSTRIES},
    )

    agents = expand_utterances("agent", 50000)
    write_jsonl(ROOT / "agents" / "agents_50000.jsonl", agents)
    quality_gate(
        "agents",
        agents,
        UTTERANCE_FIELDS,
        balances={"dialect": ["bac", "trung", "nam"], "industry": INDUSTRIES},
    )

    conversations = gen_conversations(10000)
    write_jsonl(ROOT / "conversations" / "conversations_10000.jsonl", conversations)
    quality_gate(
        "conversations",
        conversations,
        ["id", "dialect", "industry", "kind", "utterances"],
    )

    golden = gen_golden_calls(500, conversations)
    write_jsonl(ROOT / "golden_calls" / "golden_calls_500.jsonl", golden)
    quality_gate(
        "golden_calls",
        golden,
        ["id", "immutable", "benchmark_score", "checksum"],
    )

    qab = gen_qa_benchmark(10000)
    write_jsonl(ROOT / "qa_benchmark" / "qa_benchmark_10000.jsonl", qab)
    quality_gate(
        "qa_benchmark",
        qab,
        ["id", "ai_score", "human_score", "difference", "reviewer"],
    )

    rules = gen_rules_1000()
    write_jsonl(REPO / "ai-brain" / "rulebook" / "rules_1000.jsonl", rules)
    write_jsonl(ROOT / "synthetic" / "rules_1000.jsonl", rules)
    quality_gate(
        "rules_1000",
        rules,
        ["id", "category", "name", "weight", "pass", "fail", "evidence", "json_mapping"],
    )

    sops = []
    for i in range(1, 51):
        ind = INDUSTRIES[(i - 1) % len(INDUSTRIES)]
        sops.append({
            "id": uid("SOP", i),
            "industry": ind,
            "title": f"SOP Telesale {INDUSTRY_LABEL.get(ind, ind)} #{i}",
            "stages": {s: f"Checklist {s} cho {ind}" for s in STAGES},
            "linked_rules": [f"R-{(i * 17 + k) % 1000 + 1:04d}" for k in range(10)],
            "kpis": ["connect_rate", "discovery_score", "close_rate", "compliance_rate"],
        })
    write_jsonl(ROOT / "synthetic" / "sop_50.jsonl", sops)
    write_jsonl(REPO / "ai-brain" / "sop" / "sop_50.jsonl", sops)
    write_jsonl(REPO / "ai-brain" / "root_cause" / "root_causes_500.jsonl", root_causes)

    summary = {
        "generated_at": utc_now(),
        "counts": {
            "customers": len(customers),
            "agents": len(agents),
            "conversations": len(conversations),
            "intents": len(intents),
            "objections": len(objections),
            "emotions": len(emotions),
            "buying_signals": len(buys),
            "root_causes": len(root_causes),
            "golden_calls": len(golden),
            "qa_benchmark": len(qab),
            "rules": len(rules),
            "sops": len(sops),
        },
        "quality_gates_dir": str(REPORTS),
        "dialect_customer_ok": dialect_balance_ok(customers),
        "dialect_agent_ok": dialect_balance_ok(agents),
    }
    (REPORTS / "vecd_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "VECD.md").write_text(
        f"""# VECD — Vietnamese Enterprise Conversation Dataset

Generated: {summary['generated_at']}

## Counts

```json
{json.dumps(summary['counts'], indent=2)}
```

## Schema

Utterance required fields: {', '.join(UTTERANCE_FIELDS)}

Dialect mix: Bắc 35% / Trung 20% / Nam 45%

## Quality Gates

See `datasets/reports/qg_*.json`.

## Rulebook

`ai-brain/rulebook/rules_1000.jsonl` — exactly 1000 rules linked to coaching/root cause.
""",
        encoding="utf-8",
    )
    print("=== VECD Factory done", json.dumps(summary["counts"]))
    failed = [p.name for p in REPORTS.glob("qg_*.json") if not json.loads(p.read_text()).get("ok")]
    if failed:
        print("FAILED GATES:", failed, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
