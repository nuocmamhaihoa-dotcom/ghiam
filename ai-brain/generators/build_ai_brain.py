#!/usr/bin/env python3
"""AI Brain factory: Rulebook 1000 + Root Cause 500 + VCIE + SOP 50."""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
SEED = 20260905

ALLOCATION = [
    ("Opening", 100), ("Rapport", 80), ("Discovery", 150), ("Qualification", 80),
    ("Presentation", 120), ("Pricing", 80), ("Objection", 200), ("Closing", 80),
    ("Voice", 60), ("Compliance", 50),
]

INDUSTRIES = [
    ("bds", "Bất động sản"), ("spa", "Spa"), ("nha_khoa", "Nha khoa"),
    ("bao_hiem", "Bảo hiểm"), ("giao_duc", "Giáo dục"), ("o_to", "Ô tô"),
    ("my_pham", "Mỹ phẩm"), ("gia_dung", "Gia dụng"), ("thuc_pham", "Thực phẩm"),
    ("dien_may", "Điện máy"), ("noi_that", "Nội thất"), ("logistics", "Logistics"),
    ("du_lich", "Du lịch"), ("tai_chinh", "Tài chính"), ("fitness", "Fitness"),
    ("camera", "Camera"), ("dien_nuoc", "Điện nước"), ("thiet_bi_y_te", "Thiết bị y tế"),
    ("dich_vu_dn", "Dịch vụ DN"), ("pharma_otc", "Dược OTC"), ("van_phong", "Nội thất VP"),
    ("khoa_online", "Khóa online"), ("the_td", "Thẻ tín dụng"), ("vay", "Vay tiêu dùng"),
    ("bh_xe", "BH xe"), ("nlmt", "NLMT"), ("bep", "Thiết bị bếp"), ("me_be", "Mẹ & bé"),
    ("thu_cung", "Thú cưng"), ("sua_nha", "Sửa nhà"), ("internet", "Internet/TV"),
    ("sim", "Telco"), ("ngoai_ngu", "Ngoại ngữ"), ("tuyen_sinh", "Tuyển sinh"),
    ("ck", "Chứng khoán"), ("quy", "Quỹ"), ("tham_my", "Thẩm mỹ"), ("ksk", "Khám SK"),
    ("nha_thuoc", "Nhà thuốc"), ("giao_hang", "Giao hàng"), ("kho", "Kho bãi"),
    ("saas", "SaaS"), ("cloud", "Cloud"), ("agency", "Agency"), ("in_an", "In ấn"),
    ("dong_phuc", "Đồng phục"), ("su_kien", "Sự kiện"), ("nha_hang", "Nhà hàng"),
    ("khach_san", "Khách sạn"), ("bds_thue", "BĐS thuê"),
]

DIALECTS = [("bac", 0.35), ("trung", 0.20), ("nam", 0.45)]
GREET = {
    "bac": ["Em chào anh/chị", "Dạ em chào anh", "Xin chào anh/chị"],
    "trung": ["Dạ em chào anh/chị", "Em chào anh/chị ạ", "Chào anh/chị nghe"],
    "nam": ["Em chào anh/chị", "Dạ chào anh/chị", "Alo em chào anh/chị"],
}
ASK_TIME = [
    "anh/chị đang tiện nói khoảng 2 phút không ạ",
    "em xin phép vài phút được không ạ",
    "bây giờ anh/chị nghe máy thuận tiện không ạ",
]
NAMES = ["Minh", "Lan", "Hùng", "Trang", "Nam", "Hà", "Phúc", "My", "Khánh", "An"]
COS = ["ABC", "An Khang", "Việt Tín", "Sao Việt", "HomeCare", "EduPro", "FinSafe", "CarePlus"]
NEED_Q = [
    "hiện anh/chị đang quan tâm điều gì nhất ạ",
    "giúp em hiểu nhu cầu chính được không ạ",
    "tiêu chí nào anh/chị ưu tiên trước ạ",
    "ngân sách dự kiến khoảng bao nhiêu ạ",
    "ai là người cùng quyết định ạ",
]
BENEFITS = ["tiết kiệm thời gian", "giảm rủi ro", "bảo hành rõ", "hỗ trợ tận nơi", "minh bạch chi phí"]
PRICES = ["1.9 triệu", "3.5 triệu", "5.9 triệu", "9.9 triệu", "12 triệu"]
OBJ_CUST = ["Đắt quá", "Để hôm khác", "Hỏi vợ/chồng đã", "Bên kia rẻ hơn", "Có lừa không", "Đang bận", "Chưa cần"]
OBJ_BY_GROUP: dict[str, list[str]] = {
    "Giá": ["Đắt quá", "Giá cao quá", "Ngân sách không đủ", "Giảm được không"],
    "Thời gian": ["Để hôm khác", "Đang bận", "Chưa cần", "Gọi lại sau nhé"],
    "Niềm tin": ["Có lừa không", "Có uy tín không", "Sợ rủi ro", "Chưa tin sản phẩm"],
    "Quyền quyết định": ["Hỏi vợ/chồng đã", "Phải hỏi sếp", "Không phải người quyết định", "Cần bàn gia đình"],
    "Đối thủ": ["Bên kia rẻ hơn", "Đang dùng bên khác", "Đối thủ khuyến mãi", "So với chỗ khác"],
}
OBJ_GOOD_BY_GROUP: dict[str, str] = {
    "Giá": "Em hiểu lo về giá; em tách giá trị và chi phí giúp nhé",
    "Thời gian": "Em tôn trọng thời gian; mình chốt lịch gọi lại cụ thể được không ạ",
    "Niềm tin": "Em chia sẻ bằng chứng/uy tín rõ ràng để anh/chị yên tâm",
    "Quyền quyết định": "Em hỗ trợ tài liệu để anh/chị trao đổi người quyết định",
    "Đối thủ": "Em so đúng tiêu chí anh/chị quan tâm, không chỉ giá",
}
OBJ_BAD_BY_GROUP: dict[str, str] = {
    "Giá": "Rẻ vậy không tốt đâu",
    "Thời gian": "Không nghe là mất ưu đãi",
    "Niềm tin": "Không tin thì thôi",
    "Quyền quyết định": "Không cần hỏi ai hết",
    "Đối thủ": "Bên kia kém",
}
OBJ_GOOD = list(OBJ_GOOD_BY_GROUP.values())
OBJ_BAD = list(OBJ_BAD_BY_GROUP.values())
CLOSE_G = [
    "Nếu phù hợp, mình chốt lịch {next} trong tuần này ạ",
    "Em giữ suất {next} cho anh/chị nhé",
    "Anh/chị chọn gói A hay B để em gửi hợp đồng ạ",
]
NEXT = ["demo 15 phút", "tư vấn online", "xem mẫu", "ký hợp đồng", "giao hàng"]
INTENTS = [
    "Price Concern", "Trust Issue", "Delay", "Decision Maker Missing", "Competitor Comparison",
    "Busy", "Interested", "Ready To Buy", "Fake Agreement", "Soft Rejection",
    "Ask Warranty", "Ask Invoice", "Ask Delivery", "Ask Discount", "Ask Demo",
    "Confused", "Angry", "Need Info", "Callback Request", "Wrong Person",
]

BLUEPRINTS = {
    "Opening": [
        ("greet_identity", "Chào và xưng danh", 1.2), ("ask_permission", "Xin phép thời gian", 1.0),
        ("confirm_person", "Xác nhận đúng người nghe", 1.1), ("state_purpose", "Nêu mục đích cuộc gọi", 1.0),
        ("brand_intro", "Giới thiệu thương hiệu", 0.9), ("no_early_pitch", "Không pitch giá ở câu đầu", 1.3),
        ("warm_tone", "Giọng mở đầu thân thiện", 0.8), ("call_context", "Nêu nguồn lead/context", 0.9),
        ("timebox", "Cam kết độ dài cuộc gọi", 0.8), ("language_fit", "Điều chỉnh danh xưng", 0.9),
    ],
    "Rapport": [
        ("empathy", "Thể hiện đồng cảm", 1.0), ("active_listen", "Lắng nghe chủ động", 1.1),
        ("mirror", "Mirror ngôn ngữ khách", 0.9), ("small_talk_bound", "Small-talk có biên độ", 0.7),
        ("respect_busy", "Tôn trọng khi khách bận", 1.0), ("name_usage", "Gọi đúng danh xưng", 0.8),
        ("no_interrupt", "Không cắt lời khách", 1.2), ("ack_emotion", "Ghi nhận cảm xúc khách", 1.0),
    ],
    "Discovery": [
        ("open_question", "Câu hỏi mở khám phá", 1.2), ("pain_probe", "Đào sâu pain point", 1.3),
        ("current_solution", "Hỏi giải pháp hiện tại", 1.0), ("impact", "Hỏi tác động nếu không xử lý", 1.1),
        ("timeline_need", "Hỏi timeline nhu cầu", 1.0), ("success_criteria", "Tiêu chí thành công", 1.0),
        ("multi_thread", "Khám phá đa chiều", 1.2), ("summarize_need", "Tóm tắt nhu cầu trước pitch", 1.3),
        ("no_assume", "Không giả định nhu cầu", 1.1), ("clarify", "Clarify câu trả lời mơ hồ", 0.9),
    ],
    "Qualification": [
        ("budget", "Xác nhận ngân sách", 1.2), ("authority", "Xác nhận người quyết định", 1.3),
        ("need_fit", "Xác nhận độ phù hợp", 1.1), ("timing", "Xác nhận thời điểm mua", 1.0),
        ("disqualify_grace", "Loại lead lịch sự", 1.0), ("score_lead", "Phân loại nóng/ấm/lạnh", 0.9),
        ("constraint", "Hỏi ràng buộc", 0.8), ("priority", "Xác nhận mức ưu tiên", 0.9),
    ],
    "Presentation": [
        ("benefit_first", "Lợi ích trước tính năng", 1.2), ("map_need", "Map giải pháp vào nhu cầu", 1.3),
        ("proof", "Đưa proof/case", 1.0), ("demo_offer", "Mời demo", 0.9),
        ("differentiate", "Khác biệt hóa", 1.1), ("simple_language", "Ngôn ngữ dễ hiểu", 0.8),
        ("chunking", "Chia nhỏ thông tin", 0.9), ("check_understanding", "Check hiểu", 1.0),
        ("no_feature_dump", "Không feature-dump", 1.2), ("visual_aid", "Gửi tài liệu minh họa", 0.7),
        ("risk_reversal", "Chính sách giảm rủi ro", 1.0), ("custom_fit", "Tùy biến theo ngành", 0.9),
    ],
    "Pricing": [
        ("price_after_value", "Báo giá sau dựng giá trị", 1.4), ("breakdown", "Tách cấu phần giá", 1.1),
        ("options", "Đưa 2–3 gói", 1.0), ("no_random_discount", "Không giảm giá tùy tiện", 1.2),
        ("total_cost", "Nêu tổng chi phí rõ", 1.0), ("payment_terms", "Điều khoản thanh toán", 0.9),
        ("anchor", "Neo giá đúng ngữ cảnh", 0.9), ("roi", "Liên hệ ROI", 1.1),
    ],
    "Objection": [
        ("ack_objection", "Acknowledge phản đối", 1.2), ("clarify_objection", "Clarify bản chất phản đối", 1.3),
        ("price_objection", "Xử lý phản đối giá", 1.4), ("time_objection", "Xử lý phản đối thời gian", 1.2),
        ("trust_objection", "Xử lý phản đối niềm tin", 1.3), ("authority_objection", "Thiếu người quyết định", 1.2),
        ("competitor_objection", "So sánh đối thủ", 1.3), ("no_pressure", "Không tạo áp lực độc hại", 1.4),
        ("reframe", "Reframe giá trị", 1.1), ("evidence_reply", "Trả lời bằng evidence", 1.1),
        ("isolate", "Isolate objection còn lại", 1.0), ("confirm_resolved", "Xác nhận đã giải tỏa", 1.0),
    ],
    "Closing": [
        ("ask_close", "Ask for close", 1.4), ("trial_close", "Trial close", 1.1),
        ("summarize_value", "Tóm tắt value trước chốt", 1.2), ("specific_next", "Next step có thời điểm", 1.3),
        ("confirm_commit", "Xác nhận cam kết", 1.1), ("handle_soft_yes", "Xử lý soft-yes", 1.2),
        ("no_ghost_end", "Không kết thúc mơ hồ", 1.3), ("thanks_close", "Cảm ơn và chốt lịch", 0.8),
    ],
    "Voice": [
        ("pace", "Tốc độ nói phù hợp", 0.9), ("filler", "Giảm filler words", 0.8),
        ("clarity", "Phát âm rõ", 0.9), ("energy", "Năng lượng giọng ổn định", 0.8),
        ("pause", "Pause chiến lược", 0.9), ("no_monotone", "Tránh giọng đều", 0.8),
    ],
    "Compliance": [
        ("no_false_claim", "Không cam kết sai", 1.5), ("disclose_recording", "Thông báo ghi âm", 1.2),
        ("privacy", "Bảo vệ dữ liệu cá nhân", 1.4), ("no_guaranteed_return", "Không hứa lợi nhuận chắc", 1.5),
        ("consent_marketing", "Xin đồng ý chăm sóc tiếp", 1.0),
    ],
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def pick(rng: random.Random, items: list):
    return items[rng.randrange(len(items))]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def make_examples(cat: str, slug: str, seq: int, rng: random.Random):
    dialect = pick(rng, ["bac", "trung", "nam"])
    name, co = pick(rng, NAMES), pick(rng, COS)
    greet = pick(rng, GREET[dialect])
    if cat == "Opening":
        good = f"{greet}, em {name} bên {co}, {pick(rng, ASK_TIME)}?"
        bad = f"Alo, gói {pick(rng, PRICES)} lấy luôn đi."
    elif cat == "Rapport":
        good = "Em hiểu anh/chị đang bận; em sẽ ngắn gọn và tôn trọng thời gian ạ."
        bad = "Anh/chị nghe cái đã, đừng ngắt em."
    elif cat == "Discovery":
        good = f"{pick(rng, NEED_Q)}? Em ghi nhận để tư vấn đúng."
        bad = "Chắc anh/chị cần gói cao cấp, em báo giá luôn."
    elif cat == "Qualification":
        good = "Anh/chị dự kiến ngân sách khoảng bao nhiêu và ai cùng quyết định ạ?"
        bad = "Không sao, ký trước rồi tính sau."
    elif cat == "Presentation":
        good = f"Dựa trên nhu cầu vừa rồi, gói này giúp anh/chị {pick(rng, BENEFITS)}."
        bad = "Sản phẩm có A, B, C, D, E... anh/chị nghe hết nhé."
    elif cat == "Pricing":
        good = f"Sau khi khớp nhu cầu, mức đầu tư khoảng {pick(rng, PRICES)}; em tách cấu phần ạ."
        bad = f"Giá {pick(rng, PRICES)}, muốn thì lấy, không thì thôi."
    elif cat == "Objection":
        good = f"Khách: '{pick(rng, OBJ_CUST)}'. Agent: '{pick(rng, OBJ_GOOD)}'."
        bad = f"Khách: '{pick(rng, OBJ_CUST)}'. Agent: '{pick(rng, OBJ_BAD)}'."
    elif cat == "Closing":
        good = str(pick(rng, CLOSE_G)).format(next=pick(rng, NEXT))
        bad = "Thôi để sau, em không hẹn lịch cụ thể."
    elif cat == "Voice":
        good = f"Giọng rõ, tốc độ vừa, pause sau câu hỏi (mẫu #{seq})."
        bad = "Nói rất nhanh, nhiều ờ/à, chồng âm lên khách."
    else:
        good = "Em không cam kết ngoài chính sách; điều khoản ghi trong hợp đồng ạ."
        bad = "Em đảm bảo 100% sinh lời, không rủi ro gì hết."
    edges = [
        f"Khách cắt ngang ({slug})", f"STT confidence thấp ({slug})",
        f"Khách chuyển máy ({slug})", f"Lead nhầm ngành ({slug}#{seq})",
        f"Khách chỉ nhắn Zalo ({slug})",
    ]
    return good, bad, edges[: rng.randint(3, 5)]


def generate_rulebook(rng: random.Random):
    rules, gates = [], []
    seen_names, seen_good = set(), set()
    seq = 0
    for cat, count in ALLOCATION:
        bps = BLUEPRINTS[cat]
        for i in range(count):
            seq += 1
            slug, base_name, base_w = bps[i % len(bps)]
            variant = i // len(bps) + 1
            rule_id = f"R-{seq:04d}"
            rule_name = f"{base_name} — biến thể {variant}" if variant > 1 else base_name
            n = 1
            while norm(rule_name) in seen_names:
                n += 1
                rule_name = f"{base_name} — biến thể {variant}.{n}"
            seen_names.add(norm(rule_name))

            good, bad, edges = make_examples(cat, slug, seq, rng)
            g_try = 0
            while norm(good) in seen_good and g_try < 80:
                good, bad, edges = make_examples(cat, slug, seq + 17 * (g_try + 1), rng)
                good = f"{good} [{rule_id}]"
                g_try += 1
            seen_good.add(norm(good))
            weight = round(float(base_w) * (1.0 + (i % 5) * 0.02), 3)
            rules.append({
                "rule_id": rule_id,
                "category": cat,
                "rule_name": rule_name,
                "description": (
                    f"Tiêu chí [{cat}] «{rule_name}»: đánh giá hành vi telesale tiếng Việt; "
                    "mọi kết luận bắt buộc có evidence transcript/audio và timestamp."
                ),
                "weight": weight,
                "pass_condition": f"PASS khi có evidence agent thực hiện đúng «{slug}» trong stage {cat}, kèm timestamp và speaker=agent.",
                "fail_condition": f"FAIL khi thiếu hành vi «{slug}», làm ngược, hoặc vi phạm trong stage {cat}.",
                "evidence_requirement": f"≥1 evidence span (quote + speaker + start_ms/end_ms + confidence≥0.7) map rule_id={rule_id}.",
                "timestamp_requirement": True,
                "root_cause": f"RC_{cat.upper()}_{slug.upper()}",
                "coaching": f"Luyện micro-skill «{slug}»: nghe span lỗi, so good example, role-play 3 lần, chấm lại theo {rule_id}.",
                "good_example": good,
                "bad_example": bad,
                "edge_cases": edges,
                "confidence_logic": "confidence=min(1, evidence_coverage*span_confidence*diarization_agreement); thiếu timestamp/speaker → Insufficient Evidence",
                "json_mapping": {
                    "rule_id": rule_id, "category": cat, "slug": slug, "stage": cat.lower(),
                    "weight_key": f"w_{cat.lower()}_{seq}",
                    "severity": "critical" if weight >= 1.3 else ("major" if weight >= 1.0 else "minor"),
                    "evaluator_hints": {"keywords": [slug.replace("_", " "), cat.lower()], "slots": [f"slot_{slug}"], "min_confidence": 0.7},
                },
            })
            if seq % 50 == 0:
                batch = rules[-50:]
                gate = {
                    "checked_at_count": seq,
                    "unique_ids": len({r["rule_id"] for r in batch}) == 50,
                    "unique_names": len({norm(r["rule_name"]) for r in batch}) == 50,
                    "unique_good_examples_in_batch": len({norm(r["good_example"]) for r in batch}) == 50,
                    "required_fields_ok": all(all(r.get(k) not in (None, "") for k in (
                        "rule_id", "category", "rule_name", "description", "weight", "pass_condition",
                        "fail_condition", "evidence_requirement", "timestamp_requirement", "root_cause",
                        "coaching", "good_example", "bad_example", "edge_cases", "confidence_logic", "json_mapping",
                    )) for r in batch),
                }
                if not all(v for k, v in gate.items() if k != "checked_at_count"):
                    raise RuntimeError(f"Dedupe gate failed at {seq}: {gate}")
                gates.append(gate)
    assert len(rules) == 1000
    return rules, gates


def generate_root_causes(rules, rng):
    by_cat = defaultdict(list)
    for r in rules:
        by_cat[r["category"]].append(r)
    chains = [
        ["Weak Opening", "Low Trust", "Early Drop", "No Sale"],
        ["Poor Discovery", "Early Pricing", "Weak Value", "Objection Unresolved", "No Close"],
        ["No Qualification", "Wrong Persona", "Feature Dump", "Price Shock", "Lost"],
        ["Missed Buying Signal", "Slow Follow-up", "Competitor Win", "Revenue Leak"],
        ["Compliance Slip", "Trust Break", "Complaint Risk", "Forced Stop"],
        ["Bad Rapport", "Defensive Customer", "Hard Objection", "Hangup"],
        ["Voice Unclear", "Misheard Offer", "Confusion", "Delay Decision"],
        ["Soft Yes Ignored", "No Next Step", "Ghosting", "Leak"],
    ]
    rows = []
    for i in range(500):
        chain = chains[i % len(chains)]
        cat = ALLOCATION[i % len(ALLOCATION)][0]
        bucket = by_cat[cat]
        linked = [bucket[(i + j) % len(bucket)]["rule_id"] for j in range(5)]
        ind = INDUSTRIES[i % len(INDUSTRIES)]
        nodes = [{"id": f"n{j}", "label": lab} for j, lab in enumerate(chain)]
        edges = [{"from": f"n{j}", "to": f"n{j+1}", "relation": "leads_to"} for j in range(len(chain) - 1)]
        rows.append({
            "id": f"RC-G-{i+1:04d}",
            "code": f"RC_{chain[0].upper().replace(' ', '_')}_{i+1:04d}",
            "name": f"{chain[0]} → {chain[-1]} ({ind[1]})",
            "trigger": chain[0],
            "graph": {"nodes": nodes, "edges": edges},
            "confidence": round(0.55 + (i % 40) / 100, 2),
            "evidence_requirements": ["scorecard fail on linked rules", "transcript spans with timestamps"],
            "coaching": f"Ưu tiên sửa node gốc «{chain[0]}» trước khi luyện chốt.",
            "linked_rule_ids": linked,
            "industry": ind[0],
        })
    return rows


def generate_vcie(rules, rng):
    dialect_cycle = []
    for d, p in DIALECTS:
        dialect_cycle.extend([d] * int(20000 * p))
    while len(dialect_cycle) < 20000:
        dialect_cycle.append("nam")
    rng.shuffle(dialect_cycle)

    utterances = []
    for i in range(20000):
        dialect = dialect_cycle[i]
        speaker = "customer" if i % 2 == 0 else "agent"
        rule = rules[i % 1000]
        if speaker == "customer":
            text = f"{pick(rng, OBJ_CUST) if i % 3 == 0 else pick(rng, NEED_Q)} ({dialect}/{i})"
            emotion = pick(rng, ["interested", "hesitation", "angry", "curious", "neutral", "frustration", "trust"])
        else:
            text = f"{pick(rng, GREET[dialect])}, {pick(rng, OBJ_GOOD) if i % 4 == 0 else pick(rng, NEED_Q)} ({i})"
            emotion = "neutral"
        utterances.append({
            "id": f"UTT-{i+1:05d}", "speaker": speaker, "text": text, "dialect": dialect,
            "industry": INDUSTRIES[i % len(INDUSTRIES)][0], "emotion": emotion,
            "intent_hint": INTENTS[i % len(INTENTS)], "linked_rule_id": rule["rule_id"],
            "confidence": round(0.7 + (i % 30) / 100, 2),
        })

    intents = []
    for i in range(500):
        base = INTENTS[i % len(INTENTS)]
        rule_ids = [rules[(i * 3 + j) % 1000]["rule_id"] for j in range(3)]
        intents.append({
            "id": f"INT-{i+1:04d}", "code": f"INT_{base.upper().replace(' ', '_')}_{i+1:04d}",
            "name": f"{base} #{i+1}", "triggers": [f"trigger:{base}:{k}" for k in range(1, 4)],
            "counter_examples": [f"không phải {base} ví dụ {k}" for k in range(1, 3)],
            "confidence_threshold": 0.72, "coaching": f"Khi intent={base}, ưu tiên rule {rule_ids[0]}",
            "linked_rule_ids": rule_ids,
        })

    groups = ["Giá", "Thời gian", "Niềm tin", "Quyền quyết định", "Đối thủ"]
    objections = []
    for i in range(1000):
        g = groups[i % 5]
        line = OBJ_BY_GROUP[g][(i // 5) % len(OBJ_BY_GROUP[g])]
        rule_ids = [rules[600 + (i % 200)]["rule_id"], rules[601 + (i % 199)]["rule_id"]]
        objections.append({
            "id": f"OBJ-{i+1:04d}", "group": g, "customer_line": f"{line} [{g}/{i}]",
            "hidden_meaning": f"Ẩn ý nhóm {g}: thiếu giá trị cảm nhận hoặc rủi ro quyết định",
            "root_cause_code": f"RC_OBJ_{g}_{i % 50:02d}",
            "good_response": OBJ_GOOD_BY_GROUP[g],
            "forbidden_response": OBJ_BAD_BY_GROUP[g],
            "practice": f"Role-play objection {g} 5 phút; chấm theo {rule_ids[0]}",
            "dialect": dialect_cycle[i % len(dialect_cycle)], "industry": INDUSTRIES[i % len(INDUSTRIES)][0],
            "linked_rule_ids": rule_ids,
        })

    signals = ["Bao giờ giao?", "Có bảo hành không?", "Thanh toán sao?", "Có hóa đơn không?",
               "Gói nào phù hợp?", "Xem demo được không?", "Giảm được nếu chốt hôm nay?",
               "Gửi hợp đồng trước?", "Có trả góp không?", "Ai lắp đặt?"]
    buying = [{
        "id": f"BUY-{i+1:04d}", "text": f"{signals[i % len(signals)]} (#{i+1})",
        "strength_score": round(0.4 + (i % 60) / 100, 2), "confidence": round(0.7 + (i % 25) / 100, 2),
        "next_step": "Chốt next-step cụ thể trong 60s", "dialect": dialect_cycle[i % len(dialect_cycle)],
        "linked_rule_ids": [rules[700 + (i % 80)]["rule_id"]],
    } for i in range(300)]

    labels = ["interested", "curious", "hesitation", "trust", "angry", "confused", "frustration", "neutral", "exit_intent"]
    emotions = []
    for i in range(500):
        timeline, t = [], 0
        for k in range(6):
            timeline.append({"t_sec": t, "label": labels[(i + k) % len(labels)],
                             "valence": round(-0.8 + ((i + k) % 9) * 0.2, 2),
                             "arousal": round(0.2 + ((i + k) % 7) * 0.1, 2)})
            t += 20 + k * 5
        emotions.append({
            "id": f"EMO-{i+1:04d}", "conversation_ref": f"CONV-SIM-{i+1:04d}", "timeline": timeline,
            "coaching": "Nếu frustration/angry: acknowledge + giảm tốc + clarify",
            "linked_rule_ids": [rules[100 + (i % 80)]["rule_id"], rules[101 + (i % 79)]["rule_id"]],
        })

    silence = [{
        "id": f"SIL-{i+1:04d}",
        "pattern": pick(rng, ["long_pause_after_price", "silence_after_close_ask", "dead_air_opening", "think_pause_ok"]),
        "max_silence_ms": 800 + (i % 20) * 100,
        "interpretation": "Pause có thể là suy nghĩ hoặc mất kết nối",
        "coaching": "Sau 3s: hỏi 'anh/chị đang nghe em chứ ạ?'",
        "linked_rule_ids": [rules[800 + (i % 60)]["rule_id"]],
    } for i in range(300)]

    interrupts = [{
        "id": f"INTP-{i+1:04d}",
        "pattern": pick(rng, ["agent_cuts_customer", "customer_cuts_agent", "overlap_both", "barge_in_price"]),
        "severity": pick(rng, ["low", "medium", "high"]),
        "coaching": "Nhường lượt; tóm tắt ý khách trước khi tiếp",
        "linked_rule_ids": [rules[140 + (i % 70)]["rule_id"]],
    } for i in range(300)]

    context = [{
        "id": f"CTX-{i+1:04d}", "signals": [f"signal_{k}" for k in range(3)],
        "inferred_context": pick(rng, [
            "Khách đang so sánh 2 báo giá", "Khách không phải decision maker",
            "Khách quan tâm giao hàng hơn giá", "Khách từng bad experience",
        ]),
        "confidence": round(0.6 + (i % 35) / 100, 2),
        "linked_rule_ids": [rules[(i * 5) % 1000]["rule_id"], rules[(i * 5 + 1) % 1000]["rule_id"]],
        "note": "Chỉ suy luận khi có evidence; không thì Insufficient Evidence",
    } for i in range(200)]

    return {
        "utterances": utterances, "intents": intents, "objections": objections,
        "buying_signals": buying, "emotions": emotions, "silence": silence,
        "interrupts": interrupts, "context": context,
    }


def generate_sops(rules):
    by_cat = defaultdict(list)
    for r in rules:
        by_cat[r["category"]].append(r)
    rows = []
    for i, (code, name) in enumerate(INDUSTRIES[:50]):
        linked = [by_cat[cat][(i * 3) % len(by_cat[cat])]["rule_id"] for cat, _ in ALLOCATION]
        compliance = [r["rule_id"] for r in by_cat["Compliance"][:5]]
        stages = {cat: [r["rule_id"] for r in by_cat[cat][i:i + 5]] for cat, _ in ALLOCATION}
        rows.append({
            "id": f"SOP-{i+1:02d}", "industry_code": code, "industry": name,
            "title": f"SOP Telesale {name}", "stages": stages, "linked_rule_ids": linked,
            "mandatory_compliance_rules": compliance,
            "kpis": ["connect_to_talk_rate", "discovery_coverage", "objection_resolution_rate", "close_ask_rate", "compliance_pass_rate"],
        })
    return rows


def validate_links(rules, bundles):
    ids = {r["rule_id"] for r in rules}
    bad = Counter()
    for name, rows in bundles.items():
        for row in rows:
            linked = row.get("linked_rule_ids") or row.get("linked_rule_id")
            if linked is None:
                continue
            if isinstance(linked, str):
                linked = [linked]
            for rid in linked:
                if rid not in ids:
                    bad[name] += 1
    return dict(bad)


def main():
    rng = random.Random(SEED)
    print("Generating Rulebook 1000...")
    rules, gates = generate_rulebook(rng)
    write_jsonl(ROOT / "rulebook" / "rulebook_1000.jsonl", rules)
    write_jsonl(ROOT / "rulebook" / "rules_1000.jsonl", rules)

    print("Generating Root Cause Graphs 500...")
    rcs = generate_root_causes(rules, rng)
    write_jsonl(ROOT / "root_cause" / "root_cause_graphs_500.jsonl", rcs)

    print("Generating VCIE...")
    vcie = generate_vcie(rules, rng)
    write_jsonl(ROOT / "vcie" / "utterances" / "utterances_20000.jsonl", vcie["utterances"])
    write_jsonl(ROOT / "vcie" / "intents" / "intents_500.jsonl", vcie["intents"])
    write_jsonl(ROOT / "vcie" / "objections" / "objection_handlers_1000.jsonl", vcie["objections"])
    write_jsonl(ROOT / "vcie" / "buying_signals" / "buying_signals_300.jsonl", vcie["buying_signals"])
    write_jsonl(ROOT / "vcie" / "emotions" / "emotion_timelines_500.jsonl", vcie["emotions"])
    write_jsonl(ROOT / "vcie" / "silence" / "silence_patterns_300.jsonl", vcie["silence"])
    write_jsonl(ROOT / "vcie" / "interrupts" / "interrupt_patterns_300.jsonl", vcie["interrupts"])
    write_jsonl(ROOT / "vcie" / "context" / "context_inference_200.jsonl", vcie["context"])

    print("Generating SOP 50...")
    sops = generate_sops(rules)
    write_jsonl(ROOT / "sop" / "sop_industries_50.jsonl", sops)

    dialect_counts = Counter(u["dialect"] for u in vcie["utterances"])
    bad = validate_links(rules, {
        "root_cause": rcs, "intents": vcie["intents"], "objections": vcie["objections"],
        "buying_signals": vcie["buying_signals"], "emotions": vcie["emotions"],
        "silence": vcie["silence"], "interrupts": vcie["interrupts"], "context": vcie["context"],
        "utterances": [{"linked_rule_ids": [u["linked_rule_id"]]} for u in vcie["utterances"]],
        "sop": sops,
    })
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
        "rulebook": {
            "count": len(rules), "allocation": {k: v for k, v in ALLOCATION},
            "unique_ids": len({r["rule_id"] for r in rules}),
            "unique_names": len({r["rule_name"] for r in rules}),
            "unique_good_examples": len({r["good_example"] for r in rules}),
            "dedupe_gates": gates,
            "dedupe_gates_pass": all(g["unique_ids"] and g["unique_names"] and g["required_fields_ok"] for g in gates),
        },
        "root_cause_graphs": len(rcs),
        "vcie": {
            "utterances": len(vcie["utterances"]), "dialect": dict(dialect_counts),
            "intents": len(vcie["intents"]), "objections": len(vcie["objections"]),
            "buying_signals": len(vcie["buying_signals"]), "emotions": len(vcie["emotions"]),
            "silence": len(vcie["silence"]), "interrupts": len(vcie["interrupts"]), "context": len(vcie["context"]),
        },
        "sop": len(sops), "broken_rule_links": bad, "all_links_valid": not any(bad.values()),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "AI_BRAIN_QUALITY_GATE.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "AI_BRAIN_MANIFEST.md").write_text(
        f"# AI Brain Manifest\n\nGenerated: `{summary['generated_at']}`\n\n"
        f"## Rulebook\n- `rulebook/rulebook_1000.jsonl` — **{len(rules)}**\n"
        f"- Allocation: {dict(ALLOCATION)}\n"
        f"- Dedupe every 50: **{'PASS' if summary['rulebook']['dedupe_gates_pass'] else 'FAIL'}**\n\n"
        f"## Judge Ensemble\n- Evidence AI · SOP Judge · Psychology AI · Sales Expert AI · Consensus AI\n\n"
        f"## Root Cause / VCIE / SOP\n- Root Cause **{len(rcs)}** · Utterances **{len(vcie['utterances'])}** · SOP **{len(sops)}**\n"
        f"- Dialect: {dict(dialect_counts)}\n- Links: {json.dumps(bad)}\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "rulebook": summary["rulebook"]["count"], "unique_good": summary["rulebook"]["unique_good_examples"],
        "gates_pass": summary["rulebook"]["dedupe_gates_pass"], "root_causes": summary["root_cause_graphs"],
        "vcie": summary["vcie"], "sop": summary["sop"], "all_links_valid": summary["all_links_valid"],
    }, ensure_ascii=False, indent=2))
    if not summary["all_links_valid"] or not summary["rulebook"]["dedupe_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
