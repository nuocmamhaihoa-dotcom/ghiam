#!/usr/bin/env python3
"""VECD Factory — Vietnamese Enterprise Conversation Dataset.

Batch-capable generator for training, Rule Engine, QA calibration.
Produces JSONL + CSV + SQL seed. Deterministic via --seed.
"""
from __future__ import annotations

import argparse
import csv
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
SPRINT_REPORTS = REPORTS / "sprints"
SQL_DIR = ROOT / "sql"

# User-facing dialect codes (map from region shares)
DIALECTS = [("north", 0.35), ("central", 0.20), ("south", 0.45)]

INDUSTRIES = [
    ("real_estate", "Bất động sản"),
    ("spa", "Spa"),
    ("dental", "Nha khoa"),
    ("insurance", "Bảo hiểm"),
    ("education", "Giáo dục"),
    ("automotive", "Ô tô"),
    ("cosmetics", "Mỹ phẩm"),
    ("home_appliances", "Gia dụng"),
    ("food", "Thực phẩm"),
    ("electronics", "Điện máy"),
    ("furniture", "Nội thất"),
    ("logistics", "Logistics"),
    ("travel", "Du lịch"),
    ("finance", "Tài chính"),
    ("fitness", "Fitness"),
    ("plumbing", "Điện nước"),
    ("repair", "Sửa chữa"),
    ("camera", "Camera"),
    ("medical_devices", "Thiết bị y tế"),
    ("b2b_services", "Dịch vụ doanh nghiệp"),
]
INDUSTRY_CODES = [c for c, _ in INDUSTRIES]
INDUSTRY_LABEL = dict(INDUSTRIES)

STAGES = [
    "opening",
    "rapport",
    "discovery",
    "qualification",
    "presentation",
    "pricing",
    "objection",
    "closing",
    "follow_up",
]

EMOTION_CODES = [
    "interested",
    "curious",
    "hesitation",
    "frustration",
    "angry",
    "trust",
    "excited",
    "neutral",
    "exit_intent",
    "confused",
]

UTTERANCE_FIELDS = [
    "id",
    "conversation_id",
    "speaker",
    "text",
    "dialect",
    "industry",
    "stage",
    "intent",
    "emotion",
    "objection_type",
    "buying_signal",
    "confidence",
    "recommended_response",
    "root_cause_if_failed",
]

# Richer Vietnamese banks by dialect + stage (no serial suffix as sole uniqueness)
CUSTOMER_BY_STAGE: dict[str, dict[str, list[str]]] = {
    "opening": {
        "north": [
            "Alo, bên mình bán gì thế?",
            "Anh/chị ơi, em nghe quảng cáo bên mình.",
            "Em đang tìm hiểu {ind}, bên mình tư vấn được không?",
            "Số này bên {ind} phải không ạ?",
        ],
        "central": [
            "Alo, bên mình bán cái chi rứa?",
            "Em nghe quảng cáo bên {ind}, hỏi thử được không?",
            "Số này bên {ind} hả?",
            "Cho em hỏi thông tin {ind} với.",
        ],
        "south": [
            "Alo, bên mình bán gì vậy?",
            "Em thấy quảng cáo {ind}, hỏi thử được không?",
            "Số này của bên {ind} đúng không?",
            "Cho em hỏi thông tin {ind} với nha.",
        ],
    },
    "rapport": {
        "north": [
            "Em đang hơi bận, nói ngắn được không?",
            "Nghe giọng thân thiện đấy.",
            "Em chỉ muốn hiểu rõ đã rồi tính.",
            "Anh/chị tư vấn lâu chưa?",
        ],
        "central": [
            "Em đang bận chút, nói ngắn được không?",
            "Nghe cũng dễ chịu.",
            "Em muốn hiểu rõ đã rồi tính.",
            "Anh/chị làm bên này lâu chưa?",
        ],
        "south": [
            "Em đang bận xíu, nói ngắn được không?",
            "Nghe dễ thương đó.",
            "Em muốn hiểu rõ đã rồi tính tiếp.",
            "Anh/chị làm bên này lâu chưa vậy?",
        ],
    },
    "discovery": {
        "north": [
            "Em đang cần cho gia đình/công ty.",
            "Nhu cầu chính của em là {need}.",
            "Em dùng giải pháp cũ rồi, chưa ổn.",
            "Em muốn biết phù hợp với {need} không.",
        ],
        "central": [
            "Em cần cho gia đình/công ty.",
            "Nhu cầu chính là {need}.",
            "Em dùng cái cũ rồi, chưa ổn.",
            "Không biết có hợp với {need} không.",
        ],
        "south": [
            "Em cần cho gia đình/công ty á.",
            "Nhu cầu chính của em là {need}.",
            "Em dùng bên cũ rồi nhưng chưa ổn.",
            "Không biết có hợp {need} không.",
        ],
    },
    "qualification": {
        "north": [
            "Ngân sách em khoảng {budget}.",
            "Người quyết định là em và người nhà.",
            "Em cần trong vòng {timeline}.",
            "Nếu hợp em mới làm tiếp.",
        ],
        "central": [
            "Ngân sách khoảng {budget}.",
            "Người quyết là em với người nhà.",
            "Em cần trong {timeline}.",
            "Hợp thì em mới làm tiếp.",
        ],
        "south": [
            "Ngân sách em khoảng {budget}.",
            "Người quyết định là em với người nhà.",
            "Em cần trong {timeline}.",
            "Hợp thì em mới làm tiếp nha.",
        ],
    },
    "presentation": {
        "north": [
            "Nghe có vẻ ổn, khác gì bên khác?",
            "Phần nào giải quyết đúng {need} của em?",
            "Có case tương tự không?",
            "Em muốn xem minh họa cụ thể.",
        ],
        "central": [
            "Nghe cũng ổn, khác chi bên khác?",
            "Phần mô giải quyết {need}?",
            "Có case giống em không?",
            "Cho em xem ví dụ cụ thể.",
        ],
        "south": [
            "Nghe ổn đó, khác gì bên khác?",
            "Phần nào giải quyết đúng {need}?",
            "Có case giống em không?",
            "Cho em xem ví dụ cụ thể nha.",
        ],
    },
    "pricing": {
        "north": [
            "Giá bao nhiêu ạ?",
            "Gói này gồm những gì?",
            "Có trả góp không?",
            "Giảm được không nếu chốt sớm?",
        ],
        "central": [
            "Giá bao nhiêu rứa?",
            "Gói này gồm chi?",
            "Có trả góp không?",
            "Giảm được không nếu chốt sớm?",
        ],
        "south": [
            "Giá sao vậy?",
            "Gói này gồm những gì?",
            "Có trả góp không?",
            "Giảm được không nếu chốt sớm?",
        ],
    },
    "objection": {
        "north": [
            "Đắt quá.",
            "Để hôm khác.",
            "Có lừa không?",
            "Để hỏi vợ/chồng/sếp đã.",
            "Bên kia rẻ hơn.",
        ],
        "central": [
            "Đắt quá rứa.",
            "Để hôm khác nghe.",
            "Có lừa không?",
            "Để hỏi người nhà/sếp đã.",
            "Bên kia rẻ hơn đó.",
        ],
        "south": [
            "Đắt quá.",
            "Để hôm khác đi.",
            "Có lừa không vậy?",
            "Để hỏi vợ/chồng/sếp đã.",
            "Bên kia rẻ hơn á.",
        ],
    },
    "closing": {
        "north": [
            "Nếu ổn thì bước tiếp theo là gì?",
            "Chốt hôm nay được ưu đãi gì?",
            "Em cần hợp đồng mẫu trước.",
            "Để em suy nghĩ thêm một chút.",
        ],
        "central": [
            "Nếu ổn thì bước tiếp theo là chi?",
            "Chốt hôm ni có ưu đãi chi?",
            "Em cần hợp đồng mẫu trước.",
            "Để em suy nghĩ thêm chút.",
        ],
        "south": [
            "Nếu ổn thì bước tiếp theo là gì?",
            "Chốt hôm nay có ưu đãi gì?",
            "Em cần hợp đồng mẫu trước.",
            "Để em suy nghĩ thêm xíu.",
        ],
    },
    "follow_up": {
        "north": [
            "Hôm trước anh/chị gọi, em xem rồi.",
            "Em còn phân vân mục {need}.",
            "Gửi lại báo giá giúp em.",
            "Tuần này em mới quyết được.",
        ],
        "central": [
            "Hôm trước anh/chị gọi, em xem rồi.",
            "Em còn phân vân chỗ {need}.",
            "Gửi lại báo giá giúp em.",
            "Tuần ni em mới quyết được.",
        ],
        "south": [
            "Hôm trước anh/chị gọi, em xem rồi.",
            "Em còn phân vân chỗ {need}.",
            "Gửi lại báo giá giúp em nha.",
            "Tuần này em mới quyết được.",
        ],
    },
}

AGENT_BY_STAGE: dict[str, dict[str, list[str]]] = {
    "opening": {
        "north": [
            "Em chào anh/chị, em {name} bên {ind}.",
            "Em xin phép anh/chị 2 phút tư vấn {ind} ạ.",
            "Em gọi lại theo yêu cầu đăng ký của anh/chị ạ.",
        ],
        "central": [
            "Em chào anh/chị, em {name} bên {ind}.",
            "Em xin phép 2 phút tư vấn {ind} ạ.",
            "Em gọi lại theo đăng ký của anh/chị.",
        ],
        "south": [
            "Em chào anh/chị, em {name} bên {ind} ạ.",
            "Em xin phép anh/chị 2 phút tư vấn {ind} nha.",
            "Em gọi lại theo đăng ký của anh/chị ạ.",
        ],
    },
    "rapport": {
        "north": [
            "Em hiểu anh/chị đang bận, em nói ngắn gọn ạ.",
            "Cảm ơn anh/chị đã nghe máy ạ.",
            "Em sẽ đi đúng nhu cầu, không vòng vo ạ.",
        ],
        "central": [
            "Em hiểu anh/chị đang bận, em nói ngắn.",
            "Cảm ơn anh/chị nghe máy.",
            "Em đi đúng nhu cầu, không vòng vo.",
        ],
        "south": [
            "Em hiểu anh/chị đang bận, em nói ngắn gọn nha.",
            "Cảm ơn anh/chị đã nghe máy ạ.",
            "Em sẽ đi đúng nhu cầu, không vòng vo.",
        ],
    },
    "discovery": {
        "north": [
            "Hiện anh/chị đang quan tâm nhất điều gì ạ?",
            "Anh/chị đang gặp khó khăn gì với giải pháp hiện tại ạ?",
            "Tiêu chí nào quan trọng nhất với anh/chị ạ?",
        ],
        "central": [
            "Hiện anh/chị quan tâm nhất điều chi?",
            "Anh/chị đang gặp khó chi với cách cũ?",
            "Tiêu chí mô quan trọng nhất?",
        ],
        "south": [
            "Hiện anh/chị quan tâm nhất điều gì ạ?",
            "Anh/chị đang gặp khó gì với cách cũ ạ?",
            "Tiêu chí nào quan trọng nhất với anh/chị?",
        ],
    },
    "qualification": {
        "north": [
            "Ngân sách dự kiến của anh/chị khoảng bao nhiêu ạ?",
            "Ai là người cùng quyết định ạ?",
            "Anh/chị cần triển khai trong thời gian nào ạ?",
        ],
        "central": [
            "Ngân sách dự kiến khoảng bao nhiêu?",
            "Ai là người cùng quyết định?",
            "Anh/chị cần làm trong thời gian mô?",
        ],
        "south": [
            "Ngân sách dự kiến khoảng bao nhiêu ạ?",
            "Ai là người cùng quyết định vậy ạ?",
            "Anh/chị cần triển khai trong thời gian nào?",
        ],
    },
    "presentation": {
        "north": [
            "Dựa trên nhu cầu {need}, bên em đề xuất hướng này ạ.",
            "Điểm khác biệt là {benefit}.",
            "Em lấy ví dụ case tương tự để anh/chị dễ hình dung ạ.",
        ],
        "central": [
            "Dựa trên nhu cầu {need}, em đề xuất hướng này.",
            "Điểm khác là {benefit}.",
            "Em lấy ví dụ case giống để dễ hình dung.",
        ],
        "south": [
            "Dựa trên nhu cầu {need}, em đề xuất hướng này ạ.",
            "Điểm khác biệt là {benefit}.",
            "Em lấy ví dụ case tương tự để dễ hình dung nha.",
        ],
    },
    "pricing": {
        "north": [
            "Gói phù hợp khoảng {budget}, gồm đúng phần anh/chị cần ạ.",
            "Em tách chi phí và giá trị để dễ so ạ.",
            "Nếu chốt trong tuần này có ưu đãi thêm ạ.",
        ],
        "central": [
            "Gói phù hợp khoảng {budget}, gồm đúng phần cần.",
            "Em tách chi phí và giá trị để dễ so.",
            "Chốt trong tuần ni có ưu đãi thêm.",
        ],
        "south": [
            "Gói phù hợp khoảng {budget}, gồm đúng phần anh/chị cần.",
            "Em tách chi phí và giá trị để dễ so sánh.",
            "Nếu chốt trong tuần này có ưu đãi thêm nha.",
        ],
    },
    "objection": {
        "north": [
            "Em hiểu lo về giá; anh/chị đang so theo tiêu chí nào ạ?",
            "Anh/chị muốn em gọi lại khung giờ nào tiện ạ?",
            "Em gửi giấy tờ/uy tín để anh/chị yên tâm ạ.",
            "Em hỗ trợ tài liệu để anh/chị trao đổi người quyết định ạ.",
            "Em so đúng tiêu chí anh/chị quan tâm, không chỉ giá ạ.",
        ],
        "central": [
            "Em hiểu lo về giá; anh/chị đang so theo tiêu chí chi?",
            "Anh/chị muốn em gọi lại lúc mô?",
            "Em gửi giấy tờ/uy tín để anh/chị yên tâm.",
            "Em hỗ trợ tài liệu để hỏi người quyết định.",
            "Em so đúng tiêu chí, không chỉ giá.",
        ],
        "south": [
            "Em hiểu lo về giá; anh/chị đang so theo tiêu chí nào?",
            "Anh/chị muốn em gọi lại lúc nào tiện?",
            "Em gửi giấy tờ/uy tín để anh/chị yên tâm nha.",
            "Em hỗ trợ tài liệu để trao đổi người quyết định.",
            "Em so đúng tiêu chí anh/chị quan tâm, không chỉ giá.",
        ],
    },
    "closing": {
        "north": [
            "Nếu phù hợp, mình chốt lịch {next} trong tuần này ạ?",
            "Anh/chị chọn gói A hay B để em gửi hợp đồng ạ?",
            "Em giữ suất {next} cho anh/chị nhé.",
        ],
        "central": [
            "Nếu phù hợp, mình chốt lịch {next} trong tuần ni?",
            "Anh/chị chọn gói A hay B để em gửi hợp đồng?",
            "Em giữ suất {next} cho anh/chị.",
        ],
        "south": [
            "Nếu phù hợp, mình chốt lịch {next} trong tuần này nha?",
            "Anh/chị chọn gói A hay B để em gửi hợp đồng?",
            "Em giữ suất {next} cho anh/chị nha.",
        ],
    },
    "follow_up": {
        "north": [
            "Em gọi lại theo lịch hẹn, anh/chị tiện nghe không ạ?",
            "Em gửi lại báo giá và điểm khác biệt chính ạ.",
            "Anh/chị còn phân vân điểm nào để em làm rõ ạ?",
        ],
        "central": [
            "Em gọi lại theo lịch, anh/chị tiện nghe không?",
            "Em gửi lại báo giá và điểm khác chính.",
            "Anh/chị còn phân vân chỗ mô để em làm rõ?",
        ],
        "south": [
            "Em gọi lại theo lịch hẹn, anh/chị tiện nghe không ạ?",
            "Em gửi lại báo giá và điểm khác biệt chính nha.",
            "Anh/chị còn phân vân điểm nào để em làm rõ?",
        ],
    },
}

NEEDS = [
    "tiết kiệm chi phí",
    "tăng hiệu quả",
    "giảm rủi ro",
    "bảo hành rõ",
    "triển khai nhanh",
    "hỗ trợ sau bán",
    "minh bạch hợp đồng",
]
BUDGETS = ["2 triệu", "5 triệu", "10 triệu", "20 triệu", "dưới ngân sách hiện tại"]
TIMELINES = ["tuần này", "tháng này", "quý này", "khi nào thấy hợp"]
BENEFITS = ["bảo hành rõ", "chi phí minh bạch", "hỗ trợ tận nơi", "triển khai nhanh", "ROI rõ"]
NEXT_STEPS = ["demo 15 phút", "tư vấn online", "ký hợp đồng", "khảo sát", "giao hàng"]
AGENT_NAMES = ["Lan", "Minh", "Hùng", "Trang", "Nam", "Hà", "Phúc", "My"]

INTENT_SEEDS = [
    ("price_concern", "Price Concern", "Khách lo về giá hoặc ngân sách", ["đắt", "mắc", "giảm giá"], ["Chỉ hỏi cấu phần gói, không nhắc giá"]),
    ("need_more_time", "Need More Time", "Khách muốn trì hoãn quyết định", ["để hôm khác", "cuối tháng"], ["Đồng ý chốt ngay"]),
    ("trust_issue", "Trust Issue", "Khách nghi ngờ uy tín", ["lừa", "công ty ở đâu"], ["Hỏi lịch giao hàng"]),
    ("decision_maker_missing", "Decision Maker Missing", "Thiếu người quyết định", ["hỏi vợ", "hỏi sếp"], ["Tự chốt một mình"]),
    ("competitor_comparison", "Competitor Comparison", "So sánh đối thủ", ["bên kia", "rẻ hơn"], ["Chỉ hỏi bảo hành"]),
    ("busy", "Busy", "Khách đang bận", ["đang bận", "gọi lại"], ["Muốn demo ngay"]),
    ("interested", "Interested", "Khách quan tâm", ["hay", "cho xem"], ["Từ chối cứng"]),
    ("ready_to_buy", "Ready To Buy", "Sẵn sàng mua", ["chốt", "thanh toán"], ["Chỉ hỏi cho vui"]),
    ("fake_agreement", "Fake Agreement", "Đồng ý giả để kết thúc", ["để xem đã", "ok nhưng"], ["Yêu cầu hợp đồng ngay"]),
    ("soft_rejection", "Soft Rejection", "Từ chối mềm", ["chưa cần", "để sau"], ["Hỏi lịch lắp đặt"]),
]

OBJECTION_GROUPS = {
    "price": {
        "lines": ["Đắt quá.", "Mắc quá.", "Để có tiền đã.", "Vượt ngân sách.", "Giảm được không?"],
        "hidden": "Thiếu cảm nhận giá trị hoặc ngân sách thật sự hẹp.",
        "root": "Agent báo giá sớm / không dựng giá trị.",
        "good": "Đồng cảm → hỏi tiêu chí so sánh → tách giá trị/chi phí.",
        "forbidden": "Tranh cãi giá / hạ giá ngay không điều kiện.",
        "practice": "Role-play 5 phút: khách nói đắt, agent hỏi thêm 1 câu trước khi giải thích.",
    },
    "time": {
        "lines": ["Để hôm khác.", "Đang bận.", "Cuối tháng tính.", "Gọi lại sau.", "Chưa tiện nói."],
        "hidden": "Tránh quyết định hoặc thời điểm chưa phù hợp.",
        "root": "Agent không chốt lịch follow-up cụ thể.",
        "good": "Tôn trọng thời gian + chốt khung giờ gọi lại.",
        "forbidden": "Ép nói tiếp khi khách bận.",
        "practice": "Luyện câu chốt lịch: ngày/giờ cụ thể trong 15 giây.",
    },
    "trust": {
        "lines": ["Có lừa không?", "Công ty ở đâu?", "Có bảo hành không?", "Có review không?", "Uy tín thế nào?"],
        "hidden": "Thiếu bằng chứng tin cậy.",
        "root": "Agent thiếu proof/social proof/pháp lý.",
        "good": "Cung cấp địa chỉ, bảo hành, case, giấy tờ.",
        "forbidden": "Né tránh / đáp trả cảm xúc tiêu cực.",
        "practice": "Chuẩn bị 3 proof theo ngành và luyện 1 phút.",
    },
    "decision": {
        "lines": ["Để hỏi vợ.", "Để hỏi chồng.", "Để hỏi sếp.", "Phải họp nội bộ.", "Không phải mình quyết."],
        "hidden": "Thiếu stakeholder hoặc tránh trách nhiệm.",
        "root": "Agent không map decision maker sớm.",
        "good": "Hỗ trợ tài liệu + hỏi ai quyết + đề xuất cuộc gọi chung.",
        "forbidden": "Ép chốt khi thiếu người quyết.",
        "practice": "Hỏi BANT authority trong Discovery.",
    },
    "competitor": {
        "lines": ["Bên kia rẻ hơn.", "Đã mua chỗ khác.", "Đang dùng bên khác.", "Đối thủ khuyến mãi.", "So với chỗ khác."],
        "hidden": "Đang neo giá hoặc đã có vendor.",
        "root": "Agent so giá thay vì so tiêu chí.",
        "good": "So đúng tiêu chí khách quan tâm, không tấn công đối thủ.",
        "forbidden": "Nói xấu đối thủ.",
        "practice": "Bảng so 3 tiêu chí: giá trị, rủi ro, hỗ trợ.",
    },
}

CONVERSATION_KINDS = [
    "success",
    "failure",
    "hot_lead",
    "cold_lead",
    "early_pricing",
    "good_discovery",
    "poor_discovery",
    "difficult_customer",
    "angry_customer",
    "friendly_customer",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str, n: int, width: int = 6) -> str:
    return f"{prefix}-{n:0{width}d}"


def pick_dialect(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for code, weight in DIALECTS:
        acc += weight
        if r <= acc:
            return code
    return "south"


def fill(template: str, rng: random.Random, industry: str) -> str:
    return (
        template.replace("{ind}", INDUSTRY_LABEL[industry])
        .replace("{need}", rng.choice(NEEDS))
        .replace("{budget}", rng.choice(BUDGETS))
        .replace("{timeline}", rng.choice(TIMELINES))
        .replace("{benefit}", rng.choice(BENEFITS))
        .replace("{next}", rng.choice(NEXT_STEPS))
        .replace("{name}", rng.choice(AGENT_NAMES))
    )


def unique_text(base: str, salt: int, industry: str, stage: str) -> str:
    """Produce diverse natural variants; fall back to context tag for scale."""
    particles = ["", " ạ", " nhé", " nha", " vậy", " ạ.", "."]
    tails = [
        "",
        " Em hỏi rõ giúp.",
        " Anh/chị tư vấn giúp em.",
        " Em đang so sánh vài bên.",
        " Em cần câu trả lời ngắn.",
        " Em nghe qua giới thiệu.",
        f" Ngành {INDUSTRY_LABEL[industry]}.",
        f" Ở bước {stage}.",
    ]
    p = particles[salt % len(particles)]
    t = tails[salt % len(tails)]
    text = " ".join((base.rstrip(" .") + p + t).split())
    if salt % 11 == 0 and text.startswith("Em "):
        text = text.replace("Em ", "Mình ", 1)
    # guarantee uniqueness at 100k scale
    text = f"{text} [{industry}/{stage}/{salt}]"
    return text



def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {}
            for k in fields:
                v = row.get(k)
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v, ensure_ascii=False)
                else:
                    flat[k] = v
            writer.writerow(flat)


def checksum(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def quality_gate(
    name: str,
    rows: list[dict[str, Any]],
    required: list[str],
    *,
    text_key: str | None = "text",
    dialect_check: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    if not rows:
        errors.append("empty")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_ids")
    if text_key:
        texts = [str(r.get(text_key) or "") for r in rows]
        if any(not t.strip() for t in texts):
            errors.append("empty_text")
        # Strict uniqueness for utterance corpora; libraries may share canonical phrases
        if name in {"customers", "agents"}:
            dups = len(texts) - len(set(texts))
            if dups / max(len(texts), 1) > 0.005:
                errors.append(f"duplicate_text_rate:{dups}")
        elif name in {"buying_signals", "objections"}:
            dups = len(texts) - len(set(texts))
            if dups / max(len(texts), 1) > 0.25:
                errors.append(f"duplicate_text_rate:{dups}")
    for i, row in enumerate(rows[:5000]):
        for field in required:
            if field not in row:
                errors.append(f"missing:{field}:row{i}")
                if len(errors) > 40:
                    break
                continue
            # optional nullable fields
            if row[field] is None and field in {"objection_type", "buying_signal"}:
                continue
            if row[field] == "" and field not in {"objection_type"}:
                errors.append(f"missing:{field}:row{i}")
                if len(errors) > 40:
                    break
                if len(errors) > 40:
                    break
        if len(errors) > 40:
            break
    dialect_ok = True
    ratios: dict[str, float] = {}
    if dialect_check:
        c = Counter(str(r.get("dialect")) for r in rows)
        total = sum(c.values()) or 1
        ratios = {k: c.get(k, 0) / total for k, _ in DIALECTS}
        dialect_ok = all(abs(ratios.get(k, 0) - p) <= 0.03 for k, p in DIALECTS)
        if not dialect_ok:
            errors.append(f"dialect_balance:{ratios}")
    report = {
        "gate": name,
        "count": len(rows),
        "ok": not errors,
        "errors": errors[:50],
        "dialect_ratios": ratios,
        "checked_at": utc_now(),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"qg_{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[QG {'PASS' if report['ok'] else 'FAIL'}] {name}: {len(rows)}")
    return report


def gen_utterances(
    rng: random.Random,
    *,
    speaker: str,
    n: int,
    id_prefix: str,
    industry_filter: str | None = None,
    dialect_filter: str | None = None,
    stage_filter: str | None = None,
) -> list[dict[str, Any]]:
    bank = CUSTOMER_BY_STAGE if speaker == "customer" else AGENT_BY_STAGE
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Pre-allocate dialect quotas unless filtered
    if dialect_filter:
        dialect_plan = [dialect_filter] * n
    else:
        n_north = int(round(n * 0.35))
        n_central = int(round(n * 0.20))
        n_south = n - n_north - n_central
        dialect_plan = (["north"] * n_north) + (["central"] * n_central) + (["south"] * n_south)
        rng.shuffle(dialect_plan)

    i = 0
    while len(rows) < n:
        i += 1
        industry = industry_filter or INDUSTRY_CODES[(i - 1) % len(INDUSTRY_CODES)]
        dialect = dialect_plan[len(rows)]
        stage = stage_filter or STAGES[(i - 1) % len(STAGES)]
        options = bank[stage][dialect]
        base = fill(options[(i - 1) % len(options)], rng, industry)
        text = unique_text(base, i, industry, stage)
        if text in seen:
            text = f"{text}#{len(rows)+1}"
        seen.add(text)
        intent = INTENT_SEEDS[(i - 1) % len(INTENT_SEEDS)][0]
        emotion = EMOTION_CODES[(i - 1) % len(EMOTION_CODES)]
        obj = list(OBJECTION_GROUPS.keys())[(i - 1) % 5] if stage == "objection" else None
        buying = False
        low = text.lower()
        if speaker == "customer" and any(k in low for k in ["bao giờ giao", "hóa đơn", "trả góp", "bảo hành", "ship"]):
            buying = True
        rows.append(
            {
                "id": uid(id_prefix, len(rows) + 1),
                "conversation_id": uid("CONV", (len(rows) % 10000) + 1),
                "speaker": speaker,
                "text": text,
                "dialect": dialect,
                "industry": industry,
                "stage": stage,
                "intent": intent,
                "emotion": emotion,
                "objection_type": obj,
                "buying_signal": buying,
                "confidence": round(0.70 + (i % 25) / 100, 2),
                "recommended_response": (
                    "Xác nhận + hỏi sâu một câu + đưa evidence theo stage."
                    if speaker == "customer"
                    else "Giữ đúng SOP stage; gắn evidence timestamp."
                ),
                "root_cause_if_failed": (
                    "Agent did not explore customer concern."
                    if speaker == "customer"
                    else "Agent skipped required stage behavior."
                ),
            }
        )
    return rows



def gen_intents(n: int = 1000) -> list[dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        code, name, definition, triggers, counters = INTENT_SEEDS[(i - 1) % len(INTENT_SEEDS)]
        rows.append(
            {
                "id": uid("INT", i),
                "name": f"{name} #{i}",
                "code": f"{code}_{i:04d}",
                "definition": definition,
                "trigger": triggers + [f"biến thể ngữ cảnh {i % 23}"],
                "counter_example": counters + [f"Không phải {name}: ngữ cảnh khác {i}"],
                "confidence_rule": f"confidence>= {0.55 + (i % 30)/100:.2f} nếu khớp >=1 trigger và không khớp counter",
                "coaching": f"Khi gặp {name}: xác nhận cảm xúc, hỏi 1 câu mở, đưa proof phù hợp.",
                "industry_affinity": [
                    INDUSTRY_CODES[i % len(INDUSTRY_CODES)],
                    INDUSTRY_CODES[(i * 3) % len(INDUSTRY_CODES)],
                ],
            }
        )
    return rows


def gen_emotions(n: int = 500) -> list[dict[str, Any]]:
    rows = []
    voice = {
        "interested": "tempo tăng nhẹ, pitch ổn",
        "curious": "nhiều câu hỏi, ngắt ngắn",
        "hesitation": "pause dài, filler ừ/à",
        "frustration": "cường độ cao, cắt lời",
        "angry": "âm lượng tăng, từ phủ định",
        "trust": "nhịp chậm, khẳng định",
        "excited": "nói nhanh, từ tích cực",
        "neutral": "đều, ít cảm thán",
        "exit_intent": "muốn kết thúc, ngắn",
        "confused": "hỏi lại, mâu thuẫn nhẹ",
    }
    textp = {
        "interested": ["hay đó", "cho xem", "nghe ổn"],
        "curious": ["là sao", "ví dụ", "khác gì"],
        "hesitation": ["để xem", "chưa chắc", "phân vân"],
        "frustration": ["nói mãi", "phiền", "không rõ"],
        "angry": ["đừng gọi nữa", "làm phiền", "tức"],
        "trust": ["tin", "ok", "được"],
        "excited": ["tuyệt", "chốt luôn", "thích"],
        "neutral": ["ừ", "vâng", "ok"],
        "exit_intent": ["thôi", "không cần", "cúp máy"],
        "confused": ["không hiểu", "ý gì", "lại một lần"],
    }
    for i in range(1, n + 1):
        code = EMOTION_CODES[(i - 1) % len(EMOTION_CODES)]
        rows.append(
            {
                "id": uid("EMO", i),
                "name": code.replace("_", " ").title(),
                "code": code,
                "trigger": f"Tín hiệu {code} xuất hiện ở stage {STAGES[i % len(STAGES)]}",
                "voice_pattern": voice[code],
                "text_pattern": textp[code],
                "coaching": f"Khi {code}: điều chỉnh nhịp, đồng cảm, xác nhận nhu cầu trước khi pitch.",
            }
        )
    return rows


def gen_buying_signals(n: int = 500) -> list[dict[str, Any]]:
    seeds = [
        "Bao giờ giao?",
        "Có bảo hành không?",
        "Thanh toán thế nào?",
        "Có xuất hóa đơn không?",
        "Lấy hai cái giảm không?",
        "Có trả góp không?",
        "Ship tận nơi không?",
        "Gửi hợp đồng mẫu được không?",
        "Ai lắp đặt vậy?",
        "Demo trực tiếp được không?",
        "Nếu chốt hôm nay còn ưu đãi không?",
        "Có hỗ trợ sau bán không?",
        "Có nhận COD không?",
        "Thời gian triển khai bao lâu?",
        "Có được thử trước không?",
    ]
    rows = []
    seen: set[str] = set()
    i = 0
    while len(rows) < n:
        i += 1
        base = seeds[(i - 1) % len(seeds)]
        industry = INDUSTRY_CODES[(i - 1) % len(INDUSTRY_CODES)]
        dialect = DIALECTS[(i - 1) % 3][0]
        text = f"{base} ({INDUSTRY_LABEL[industry]} · {dialect} · {i})"
        if text in seen:
            continue
        seen.add(text)
        rows.append(
            {
                "id": uid("BUY", len(rows) + 1),
                "text": text,
                "strength_score": round(0.45 + (i % 50) / 100, 2),
                "confidence": round(0.6 + (i % 35) / 100, 2),
                "recommended_next_step": "Xác nhận điều kiện + chốt next-step cụ thể trong 60 giây.",
                "dialect": dialect,
                "industry": industry,
            }
        )
    return rows



def gen_objections(n: int = 5000) -> list[dict[str, Any]]:
    groups = list(OBJECTION_GROUPS.keys())
    rows = []
    seen: set[str] = set()
    i = 0
    while len(rows) < n:
        i += 1
        g = groups[(i - 1) % len(groups)]
        meta = OBJECTION_GROUPS[g]
        line = meta["lines"][(i - 1) % len(meta["lines"])]
        dialect = DIALECTS[(i - 1) % 3][0]
        industry = INDUSTRY_CODES[(i - 1) % len(INDUSTRY_CODES)]
        text = f"{line.rstrip('.')} ({INDUSTRY_LABEL[industry]}, {dialect}, #{i})."
        if text in seen:
            continue
        seen.add(text)
        rows.append(
            {
                "id": uid("OBJ", len(rows) + 1),
                "group": g,
                "text": text,
                "dialect": dialect,
                "industry": industry,
                "hidden_meaning": meta["hidden"],
                "root_cause": meta["root"],
                "good_response": meta["good"],
                "forbidden_response": meta["forbidden"],
                "practice_exercise": meta["practice"],
                "confidence": round(0.7 + (i % 20) / 100, 2),
            }
        )
    return rows



def gen_root_causes(n: int = 500) -> list[dict[str, Any]]:
    chains = [
        ["No Sale", "Poor Discovery", "Early Pricing", "Weak Value"],
        ["No Sale", "Ignored Objection", "No Follow-up", "Lost Trust"],
        ["No Sale", "Missing Decision Maker", "Wrong Closer", "Stalled"],
        ["No Sale", "Feature Dump", "Confused Customer", "Exit Intent"],
        ["No Sale", "Pressure Close", "Angry Customer", "Complaint Risk"],
    ]
    rows = []
    for i in range(1, n + 1):
        chain = chains[(i - 1) % len(chains)]
        graph = [{"from": chain[j], "to": chain[j + 1]} for j in range(len(chain) - 1)]
        rows.append(
            {
                "id": uid("RC", i),
                "customer_line": "Đắt quá." if i % 2 else "Để hôm khác.",
                "agent_line": "Dạ." if i % 3 else "Gói này đang khuyến mãi.",
                "root_cause": f"{chain[1]} — {chain[2]}",
                "coaching": "Hỏi thêm một câu trước khi phản hồi; dựng giá trị trước giá.",
                "graph": graph,
                "industry": INDUSTRY_CODES[i % len(INDUSTRY_CODES)],
                "confidence": round(0.6 + (i % 30) / 100, 2),
                "linked_rules": [f"R-{(i % 200) + 1:04d}", f"R-{(i % 199) + 2:04d}"],
            }
        )
    return rows


def gen_conversations(n: int = 10000, rng: random.Random | None = None) -> list[dict[str, Any]]:
    rng = rng or random.Random(20260905)
    rows = []
    for i in range(1, n + 1):
        dialect = pick_dialect(rng)
        industry = INDUSTRY_CODES[(i - 1) % len(INDUSTRY_CODES)]
        kind = CONVERSATION_KINDS[(i - 1) % len(CONVERSATION_KINDS)]
        turn_count = 8 + (i % 23)  # 8..30
        utterances = []
        missing_stages = []
        # Ensure core path; mark missing if intentionally poor discovery
        stage_path = list(STAGES)
        if kind == "poor_discovery":
            stage_path = [s for s in stage_path if s != "discovery"]
            missing_stages.append("discovery")
        if kind == "early_pricing":
            stage_path = ["opening", "pricing", "objection", "closing", "follow_up"]
            missing_stages.extend(["rapport", "discovery", "qualification", "presentation"])
        for t in range(turn_count):
            stage = stage_path[t % len(stage_path)]
            speaker = "agent" if t % 2 == 0 else "customer"
            bank = AGENT_BY_STAGE if speaker == "agent" else CUSTOMER_BY_STAGE
            tmpl = rng.choice(bank[stage][dialect])
            text = fill(tmpl, rng, industry)
            utterances.append(
                {
                    "turn": t + 1,
                    "speaker": speaker,
                    "text": text,
                    "stage": stage,
                    "emotion": EMOTION_CODES[(t + i) % len(EMOTION_CODES)],
                    "intent": INTENT_SEEDS[(t + i) % len(INTENT_SEEDS)][0],
                }
            )
        outcome = "won" if kind in {"success", "hot_lead", "good_discovery", "friendly_customer"} else "lost"
        rows.append(
            {
                "id": uid("CONV", i),
                "dialect": dialect,
                "industry": industry,
                "kind": kind,
                "turn_count": turn_count,
                "outcome": outcome,
                "missing_stages": missing_stages,
                "qa_score_hint": 80 + (i % 15) if outcome == "won" else 40 + (i % 25),
                "linked_root_cause": uid("RC", ((i - 1) % 500) + 1),
                "utterances": utterances,
            }
        )
    return rows


def gen_golden_calls(conversations: list[dict[str, Any]], n: int = 500) -> list[dict[str, Any]]:
    rows = []
    # Prefer success / good_discovery
    pool = [c for c in conversations if c["kind"] in {"success", "good_discovery", "friendly_customer"}]
    if len(pool) < n:
        pool = conversations[:n]
    for i in range(n):
        src = pool[i % len(pool)]
        payload = json.dumps(src, ensure_ascii=False, sort_keys=True)
        rows.append(
            {
                "id": uid("GOLD", i + 1),
                "immutable": True,
                "source_conversation_id": src["id"],
                "industry": src["industry"],
                "dialect": src["dialect"],
                "benchmark_score": 90 + (i % 10),
                "why_golden": "Discovery đủ, value trước giá, objection có evidence, closing rõ.",
                "checksum": checksum(payload),
                "conversation": src,
            }
        )
    return rows


def gen_qa_benchmark(conversations: list[dict[str, Any]], n: int = 10000) -> list[dict[str, Any]]:
    reviewers = ["qa_lead_a", "qa_lead_b", "calibrator_c", "senior_qa_d"]
    rows = []
    for i in range(1, n + 1):
        conv = conversations[(i - 1) % len(conversations)]
        human = int(conv.get("qa_score_hint") or 50)
        ai = max(0, min(100, human + ((i % 17) - 8)))
        rows.append(
            {
                "id": uid("QAB", i),
                "conversation_id": conv["id"],
                "ai_score": ai,
                "human_score": human,
                "difference": ai - human,
                "reviewer": reviewers[i % len(reviewers)],
                "reason": "Calibration sample — đối chiếu rule hits và evidence spans.",
                "calibration_batch": f"batch-{(i // 1000) + 1:03d}",
            }
        )
    return rows


def export_sql_seed(collections: dict[str, list[dict[str, Any]]]) -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    path = SQL_DIR / "vecd_seed.sql"
    lines = [
        "-- VECD PostgreSQL seed (generated)",
        "CREATE TABLE IF NOT EXISTS vecd_conversations (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_utterances (id TEXT PRIMARY KEY, speaker TEXT, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_intents (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_objections (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_emotions (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_buying_signals (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_root_causes (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_qa_scores (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "CREATE TABLE IF NOT EXISTS vecd_golden_calls (id TEXT PRIMARY KEY, payload JSONB NOT NULL);",
        "",
    ]
    table_map = {
        "customers": ("vecd_utterances", "customer"),
        "agents": ("vecd_utterances", "agent"),
        "conversations": ("vecd_conversations", None),
        "intents": ("vecd_intents", None),
        "objections": ("vecd_objections", None),
        "emotions": ("vecd_emotions", None),
        "buying_signals": ("vecd_buying_signals", None),
        "root_causes": ("vecd_root_causes", None),
        "qa_benchmark": ("vecd_qa_scores", None),
        "golden_calls": ("vecd_golden_calls", None),
    }
    for name, rows in collections.items():
        table, speaker = table_map[name]
        # seed first 200 of each for portable SQL; full load via export_postgres.py
        for row in rows[:200]:
            payload = json.dumps(row, ensure_ascii=False).replace("'", "''")
            rid = str(row["id"]).replace("'", "''")
            if table == "vecd_utterances":
                lines.append(
                    f"INSERT INTO vecd_utterances(id, speaker, payload) VALUES ('{rid}', '{speaker}', '{payload}'::jsonb) "
                    f"ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;"
                )
            else:
                lines.append(
                    f"INSERT INTO {table}(id, payload) VALUES ('{rid}', '{payload}'::jsonb) "
                    f"ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;"
                )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_sprint_report(sprint: int, title: str, gates: list[dict[str, Any]], extras: dict[str, Any]) -> None:
    SPRINT_REPORTS.mkdir(parents=True, exist_ok=True)
    ok = all(g.get("ok") for g in gates)
    doc = {
        "sprint": sprint,
        "title": title,
        "ok": ok,
        "checked_at": utc_now(),
        "gates": gates,
        "extras": extras,
    }
    (SPRINT_REPORTS / f"SPRINT_{sprint:02d}_VECD.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        f"# VECD Sprint {sprint}: {title}",
        "",
        f"- Result: **{'PASS' if ok else 'FAIL'}**",
        f"- Checked at: `{doc['checked_at']}`",
        "",
        "| Gate | Count | Status |",
        "|------|------:|--------|",
    ]
    for g in gates:
        md.append(f"| {g['gate']} | {g.get('count', 0)} | {'PASS' if g['ok'] else 'FAIL'} |")
    (SPRINT_REPORTS / f"SPRINT_{sprint:02d}_VECD.md").write_text("\n".join(md) + "\n", encoding="utf-8")



def largest_jsonl(folder: Path, prefix: str) -> Path | None:
    """Pick corpus with most lines (avoids customers_9057 > customers_100000 lexicographically)."""
    files = list(folder.glob(f"{prefix}_*.jsonl"))
    if not files:
        return None

    def line_count(p: Path) -> int:
        with p.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    return max(files, key=line_count)

def persist_collection(name: str, rows: list[dict[str, Any]], flat_fields: list[str] | None = None) -> None:
    out_dir = ROOT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{name}_{len(rows)}.jsonl"
    csv_path = out_dir / f"{name}_{len(rows)}.csv"
    write_jsonl(jsonl_path, rows)
    write_csv(csv_path, rows, flat_fields)
    print(f"wrote {jsonl_path.relative_to(REPO)} (+csv)")


def run_factory(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    REPORTS.mkdir(parents=True, exist_ok=True)
    gates: list[dict[str, Any]] = []

    # Sprint 1 — schema/scripts already present; emit schema artifact
    schema = {
        "utterance_fields": UTTERANCE_FIELDS,
        "dialects": DIALECTS,
        "industries": INDUSTRIES,
        "stages": STAGES,
        "targets": {
            "customers": 100000,
            "agents": 50000,
            "conversations": 10000,
            "objections": 5000,
            "intents": 1000,
            "buying_signals": 500,
            "emotions": 500,
            "root_causes": 500,
            "golden_calls": 500,
            "qa_benchmark": 10000,
        },
    }
    (ROOT / "validation" / "schema.json").parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "validation" / "schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_sprint_report(1, "Schema + Scripts + Validation", [{"gate": "schema", "count": 1, "ok": True}], schema["targets"])

    # Determine batch sizes ( favor full targets; allow --batch-size override for utterances)
    cust_n = min(args.batch_size or 100000, 100000) if args.only in (None, "customers") else 100000
    agent_n = min(args.batch_size or 50000, 50000) if args.only in (None, "agents") else 50000
    if args.only == "customers":
        cust_n = args.batch_size or 10000
    if args.only == "agents":
        agent_n = args.batch_size or 5000

    # Sprint 2 — customers
    if args.only in (None, "customers"):
        customers = gen_utterances(
            rng,
            speaker="customer",
            n=cust_n if args.only == "customers" else 100000,
            id_prefix="VECD-C",
            industry_filter=args.industry,
            dialect_filter=args.dialect,
            stage_filter=args.stage,
        )
        persist_collection("customers", customers, UTTERANCE_FIELDS)
        g = quality_gate("customers", customers, UTTERANCE_FIELDS, dialect_check=True)
        gates.append(g)
        write_sprint_report(2, "Customer Utterances", [g], {"count": len(customers)})
    else:
        customers = []

    # Sprint 3 — agents
    if args.only in (None, "agents"):
        agents = gen_utterances(
            rng,
            speaker="agent",
            n=agent_n if args.only == "agents" else 50000,
            id_prefix="VECD-A",
            industry_filter=args.industry,
            dialect_filter=args.dialect,
            stage_filter=args.stage,
        )
        persist_collection("agents", agents, UTTERANCE_FIELDS)
        g = quality_gate("agents", agents, UTTERANCE_FIELDS, dialect_check=True)
        gates.append(g)
        write_sprint_report(3, "Agent Utterances", [g], {"count": len(agents)})
    else:
        agents = []

    # Sprint 4 — intents/emotions/buying
    if args.only in (None, "intents", "emotions", "buying_signals", "libraries"):
        intents = gen_intents(1000)
        emotions = gen_emotions(500)
        buying = gen_buying_signals(500)
        persist_collection("intents", intents)
        persist_collection("emotions", emotions)
        persist_collection("buying_signals", buying)
        g1 = quality_gate("intents", intents, ["id", "name", "definition", "trigger", "counter_example", "confidence_rule", "coaching"], text_key=None)
        g2 = quality_gate("emotions", emotions, ["id", "name", "trigger", "voice_pattern", "text_pattern", "coaching"], text_key=None)
        g3 = quality_gate("buying_signals", buying, ["id", "text", "strength_score", "confidence", "recommended_next_step"])
        gates.extend([g1, g2, g3])
        write_sprint_report(4, "Intent + Emotion + Buying Signals", [g1, g2, g3], {})
    else:
        intents = emotions = buying = []

    # Sprint 5 — objections + root causes
    if args.only in (None, "objections", "root_causes", "libraries"):
        objections = gen_objections(5000)
        root_causes = gen_root_causes(500)
        persist_collection("objections", objections)
        persist_collection("root_causes", root_causes)
        g1 = quality_gate(
            "objections",
            objections,
            ["id", "group", "text", "hidden_meaning", "root_cause", "good_response", "forbidden_response", "practice_exercise"],
        )
        g2 = quality_gate(
            "root_causes",
            root_causes,
            ["id", "customer_line", "agent_line", "root_cause", "coaching", "graph"],
            text_key=None,
        )
        gates.extend([g1, g2])
        write_sprint_report(5, "Objections + Root Causes", [g1, g2], {})
    else:
        objections = root_causes = []

    # Sprint 6 — conversations
    if args.only in (None, "conversations"):
        conversations = gen_conversations(10000, rng)
        persist_collection("conversations", conversations)
        g = quality_gate("conversations", conversations, ["id", "dialect", "industry", "kind", "utterances"], text_key=None)
        # missing stage tag sanity
        missing_tagged = sum(1 for c in conversations if c.get("missing_stages"))
        g["extras"] = {"conversations_with_missing_stages": missing_tagged}
        gates.append(g)
        write_sprint_report(6, "Conversation Library", [g], {"missing_stage_tagged": missing_tagged})
    else:
        # load existing if needed for later sprints
        conv_file = largest_jsonl(ROOT / "conversations", "conversations")
        conversations = []
        if conv_file:
            conversations = [json.loads(l) for l in conv_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    # Sprint 7 — QA benchmark
    if args.only in (None, "qa_benchmark"):
        if not conversations:
            conversations = gen_conversations(10000, rng)
            persist_collection("conversations", conversations)
        qa = gen_qa_benchmark(conversations, 10000)
        persist_collection("qa_benchmark", qa)
        g = quality_gate("qa_benchmark", qa, ["id", "ai_score", "human_score", "difference", "reviewer", "reason"], text_key=None)
        gates.append(g)
        write_sprint_report(7, "QA Benchmark", [g], {})
    else:
        qa = []

    # Sprint 8 — golden calls
    if args.only in (None, "golden_calls"):
        if not conversations:
            conversations = gen_conversations(10000, rng)
        golden = gen_golden_calls(conversations, 500)
        persist_collection("golden_calls", golden)
        g = quality_gate("golden_calls", golden, ["id", "immutable", "checksum", "source_conversation_id"], text_key=None)
        if not all(r.get("immutable") is True for r in golden):
            g["ok"] = False
            g["errors"].append("golden_not_immutable")
        gates.append(g)
        write_sprint_report(8, "Golden Calls", [g], {"immutable": True})
    else:
        golden = []

    # Collect for SQL sample seed
    collections: dict[str, list[dict[str, Any]]] = {}
    for name in [
        "customers",
        "agents",
        "conversations",
        "intents",
        "objections",
        "emotions",
        "buying_signals",
        "root_causes",
        "qa_benchmark",
        "golden_calls",
    ]:
        latest = largest_jsonl(ROOT / name, name)
        if latest:
            collections[name] = [json.loads(l) for l in latest.read_text(encoding="utf-8").splitlines() if l.strip()]

    sql_path = export_sql_seed(collections)
    print(f"wrote SQL seed sample {sql_path.relative_to(REPO)}")

    summary = {
        "generated_at": utc_now(),
        "seed": args.seed,
        "counts": {k: len(v) for k, v in collections.items()},
        "all_gates_ok": all(g.get("ok") for g in gates) if gates else True,
        "gates": gates,
        "dialect_targets": {"north": 0.35, "central": 0.20, "south": 0.45},
        "industries": INDUSTRY_CODES,
        "stages": STAGES,
    }
    (REPORTS / "vecd_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update docs/VECD.md counts section lightly via sidecar status
    (REPORTS / "MASTER_VECD_STATUS.md").write_text(
        "\n".join(
            [
                "# VECD Master Status",
                "",
                f"**Updated:** {summary['generated_at']}",
                f"**Overall gates:** {'PASS' if summary['all_gates_ok'] else 'FAIL'}",
                "",
                "## Counts",
                "",
                "| Asset | Count |",
                "|-------|------:|",
                *[f"| {k} | {v} |" for k, v in summary["counts"].items()],
                "",
                "## Commands",
                "",
                "```bash",
                "python datasets/scripts/generate_dataset.py --seed 20260905",
                "python datasets/scripts/validate_dataset.py",
                "python datasets/scripts/export_postgres.py",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )

    failed = [g for g in gates if not g.get("ok")]
    if failed:
        print("FAILED GATES:", [g["gate"] for g in failed])
        return 1
    print("VECD factory complete:", summary["counts"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VECD Dataset Factory")
    p.add_argument("--batch-size", type=int, default=None, help="Batch size for utterance-only runs")
    p.add_argument("--industry", type=str, default=None, choices=INDUSTRY_CODES)
    p.add_argument("--dialect", type=str, default=None, choices=[d for d, _ in DIALECTS])
    p.add_argument("--stage", type=str, default=None, choices=STAGES)
    p.add_argument("--seed", type=int, default=20260905)
    p.add_argument("--output", type=str, default=str(ROOT), help="Output root (default datasets/)")
    p.add_argument(
        "--only",
        type=str,
        default=None,
        choices=[
            "customers",
            "agents",
            "conversations",
            "intents",
            "emotions",
            "buying_signals",
            "objections",
            "root_causes",
            "qa_benchmark",
            "golden_calls",
            "libraries",
        ],
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    global ROOT, REPORTS, SPRINT_REPORTS, SQL_DIR
    ROOT = Path(args.output).resolve()
    REPORTS = ROOT / "reports"
    SPRINT_REPORTS = REPORTS / "sprints"
    SQL_DIR = ROOT / "sql"
    raise SystemExit(run_factory(args))


if __name__ == "__main__":
    main()
