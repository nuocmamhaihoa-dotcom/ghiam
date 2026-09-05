"""Sync enterprise corpora into Memory Graph + knowledge/ files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memory_graph.graph import EnterpriseMemoryGraph

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "knowledge"

SEED: list[dict[str, Any]] = [
    {"id": "product:the_tin_dung", "type": "product", "label": "Thẻ tín dụng", "content": "Thẻ tín dụng lãi suất ưu đãi 12 tháng", "source": "catalog/products", "confidence": 0.95},
    {"id": "product:vay_tieu_dung", "type": "product", "label": "Vay tiêu dùng", "content": "Vay tiêu dùng hạn mức đến 500 triệu", "source": "catalog/products", "confidence": 0.95},
    {"id": "sop:mo_dau", "type": "sop", "label": "SOP mở đầu", "content": "SOP mở đầu: chào hỏi, xác minh danh tính, nêu mục đích cuộc gọi trong 30 giây", "source": "sop/opening", "confidence": 0.98},
    {"id": "sop:xu_ly_phan_doi", "type": "sop", "label": "SOP phản đối", "content": "SOP xử lý phản đối: lắng nghe, xác nhận cảm xúc, đưa bằng chứng, hỏi chốt nhẹ", "source": "sop/objection", "confidence": 0.98},
    {"id": "rulebook:no_guarantee", "type": "rulebook", "label": "Cấm cam kết duyệt", "content": "Cấm cam kết duyệt hồ sơ 100% hoặc lãi suất ngoài bảng giá công bố", "source": "rulebook/compliance", "confidence": 1.0},
    {"id": "rulebook:disclose_fee", "type": "rulebook", "label": "Công bố phí", "content": "Bắt buộc công bố phí thường niên và điều kiện ưu đãi trước khi chốt", "source": "rulebook/disclosure", "confidence": 1.0},
    {"id": "intent:can_tu_van", "type": "intent", "label": "Cần tư vấn", "content": "Khách cần tư vấn thêm trước khi quyết định", "source": "taxonomy/intent", "confidence": 0.9},
    {"id": "intent:so_sanh_gia", "type": "intent", "label": "So sánh giá", "content": "Khách đang so sánh giá với đối thủ", "source": "taxonomy/intent", "confidence": 0.9},
    {"id": "objection:dat", "type": "objection", "label": "Phản đối giá", "content": "Phản đối giá cao / lãi suất cao", "source": "taxonomy/objection", "confidence": 0.92},
    {"id": "objection:suy_nghi", "type": "objection", "label": "Để suy nghĩ", "content": "Phản đối để suy nghĩ thêm / gọi lại sau", "source": "taxonomy/objection", "confidence": 0.92},
    {"id": "objection:da_co_the", "type": "objection", "label": "Đã có thẻ", "content": "Khách đã có thẻ/sản phẩm tương tự", "source": "taxonomy/objection", "confidence": 0.9},
    {"id": "pricing:the_tin_dung_base", "type": "pricing", "label": "Giá thẻ tín dụng", "content": "Thẻ tín dụng: phí thường niên 500.000đ, ưu đãi miễn phí năm đầu nếu chi tiêu 20 triệu", "source": "pricing/card", "confidence": 0.97},
    {"id": "pricing:vay_lai_suat", "type": "pricing", "label": "Lãi suất vay", "content": "Vay tiêu dùng: lãi suất từ 1.5%/tháng tùy xếp hạng tín dụng, không cam kết trước thẩm định", "source": "pricing/loan", "confidence": 0.97},
    {"id": "policy:kyc", "type": "policy", "label": "KYC", "content": "Chính sách KYC: xác minh CMND/CCCD và số điện thoại trước khi tư vấn sản phẩm", "source": "policy/kyc", "confidence": 1.0},
    {"id": "policy:privacy", "type": "policy", "label": "Bảo mật", "content": "Chính sách bảo mật: không ghi âm chia sẻ ra ngoài hệ thống được phê duyệt", "source": "policy/privacy", "confidence": 1.0},
    {"id": "promotion:summer2026", "type": "promotion", "label": "KM hè 2026", "content": "Khuyến mãi hè 2026: hoàn 5% chi tiêu siêu thị tối đa 500.000đ/tháng", "source": "promo/summer2026", "confidence": 0.9},
    {"id": "golden_call:gc001", "type": "golden_call", "label": "Golden call GC001", "content": "Golden call: agent xác nhận cảm xúc khi khách nói đắt, so sánh phí năm đầu miễn, hỏi chốt thử thẻ phụ", "source": "calls/golden/gc001", "confidence": 0.93},
    {"id": "golden_call:gc002", "type": "golden_call", "label": "Golden call GC002", "content": "Golden call: xử lý để suy nghĩ bằng tóm tắt lợi ích + đề xuất gọi lại đúng giờ khách chọn", "source": "calls/golden/gc002", "confidence": 0.93},
    {"id": "customer_type:mass", "type": "customer_type", "label": "Mass affluent", "content": "Khách mass affluent: ưu tiên lợi ích phí và ưu đãi rõ ràng", "source": "taxonomy/customer", "confidence": 0.88},
    {"id": "customer_type:price_sensitive", "type": "customer_type", "label": "Nhạy giá", "content": "Khách nhạy giá: cần dẫn chứng bảng giá và so sánh công khai", "source": "taxonomy/customer", "confidence": 0.88},
    {"id": "root_cause:weak_discovery", "type": "root_cause", "label": "Discovery yếu", "content": "Root cause: discovery yếu dẫn đến không khớp nhu cầu và mất chốt", "source": "analytics/root_cause", "confidence": 0.85},
    {"id": "root_cause:overpromise", "type": "root_cause", "label": "Overpromise", "content": "Root cause: overpromise lãi suất gây compliance fail và revenue leak", "source": "analytics/root_cause", "confidence": 0.9},
    {"id": "coaching:listen_ack", "type": "coaching", "label": "Lắng nghe xác nhận", "content": "Coaching: lắng nghe phản đối 3 giây, nhắc lại nội dung khách nói trước khi trả lời", "source": "coaching/playbook", "confidence": 0.94},
    {"id": "coaching:evidence_close", "type": "coaching", "label": "Evidence close", "content": "Coaching: mỗi claim phải gắn citation từ pricing/policy rồi mới hỏi chốt", "source": "coaching/playbook", "confidence": 0.94},
    {"id": "compliance:no_fake_rate", "type": "compliance", "label": "Cấm lãi giả", "content": "Compliance: cấm nêu lãi suất giả định không có trong bảng giá hiệu lực", "source": "compliance/rules", "confidence": 1.0},
    {"id": "faq:phi_thuong_nien", "type": "faq", "label": "FAQ phí", "content": "FAQ: phí thường niên thẻ tín dụng là 500.000đ, miễn năm đầu theo điều kiện chi tiêu", "source": "faq/fees", "confidence": 0.96},
    {"id": "faq:thoi_gian_duyet", "type": "faq", "label": "FAQ duyệt", "content": "FAQ: thời gian duyệt hồ sơ thường 1-3 ngày làm việc, không cam kết duyệt chắc chắn", "source": "faq/sla", "confidence": 0.96},
    {"id": "revenue_leak:overpromise", "type": "revenue_leak", "label": "Leak overpromise", "content": "Revenue leak từ overpromise lãi suất / cam kết duyệt", "source": "analytics/revenue_leak", "confidence": 0.9},
]

LINKS: list[tuple[str, str, str]] = [
    ("product:the_tin_dung", "sop:mo_dau", "product_has_sop"),
    ("product:the_tin_dung", "sop:xu_ly_phan_doi", "product_has_sop"),
    ("product:vay_tieu_dung", "sop:mo_dau", "product_has_sop"),
    ("product:the_tin_dung", "objection:dat", "product_has_objection"),
    ("product:the_tin_dung", "objection:suy_nghi", "product_has_objection"),
    ("product:the_tin_dung", "objection:da_co_the", "product_has_objection"),
    ("product:vay_tieu_dung", "objection:dat", "product_has_objection"),
    ("objection:dat", "rulebook:disclose_fee", "objection_maps_rule"),
    ("objection:dat", "pricing:the_tin_dung_base", "objection_maps_rule"),
    ("objection:suy_nghi", "rulebook:no_guarantee", "objection_maps_rule"),
    ("customer_type:price_sensitive", "intent:so_sanh_gia", "customer_has_intent"),
    ("customer_type:mass", "intent:can_tu_van", "customer_has_intent"),
    ("golden_call:gc001", "coaching:listen_ack", "golden_call_has_coaching"),
    ("golden_call:gc002", "coaching:evidence_close", "golden_call_has_coaching"),
    ("root_cause:overpromise", "revenue_leak:overpromise", "root_cause_causes_revenue_leak"),
    ("product:the_tin_dung", "pricing:the_tin_dung_base", "product_has_pricing"),
    ("product:vay_tieu_dung", "pricing:vay_lai_suat", "product_has_pricing"),
    ("product:the_tin_dung", "policy:kyc", "product_has_policy"),
    ("product:the_tin_dung", "promotion:summer2026", "product_has_promotion"),
    ("rulebook:no_guarantee", "compliance:no_fake_rate", "rule_enforces_compliance"),
    ("intent:so_sanh_gia", "objection:dat", "intent_linked_objection"),
    ("coaching:evidence_close", "root_cause:overpromise", "coaching_addresses_root_cause"),
]


def _write_knowledge_files(items: list[dict[str, Any]]) -> None:
    KNOWLEDGE.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict[str, Any]]] = {
        "sop": [],
        "pricing": [],
        "policy": [],
        "faq": [],
        "golden_calls": [],
        "rulebook": [],
        "other": [],
    }
    for it in items:
        src = it["source"]
        row = {
            "id": it["id"],
            "type": it["type"],
            "label": it["label"],
            "content": it["content"],
            "source": src,
            "confidence": it["confidence"],
        }
        if src.startswith("sop/"):
            buckets["sop"].append(row)
        elif src.startswith("pricing/"):
            buckets["pricing"].append(row)
        elif src.startswith("policy/"):
            buckets["policy"].append(row)
        elif src.startswith("faq/"):
            buckets["faq"].append(row)
        elif src.startswith("calls/golden"):
            buckets["golden_calls"].append(row)
        elif src.startswith("rulebook/") or src.startswith("compliance/"):
            buckets["rulebook"].append(row)
        else:
            buckets["other"].append(row)
    for name, rows in buckets.items():
        path = KNOWLEDGE / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False))
                fh.write(chr(10))


def sync_all(graph: EnterpriseMemoryGraph | None = None, *, reset: bool = False) -> dict[str, Any]:
    g = graph or EnterpriseMemoryGraph()
    if reset:
        g.store.clear()
    for it in SEED:
        g.upsert_node(
            node_id=it["id"],
            node_type=it["type"],
            label=it["label"],
            content=it["content"],
            source=it["source"],
            confidence=it["confidence"],
        )
    for source_id, target_id, relation in LINKS:
        g.link(source_id=source_id, target_id=target_id, relation=relation, confidence=0.9)
    _write_knowledge_files(SEED)
    g.store.save()
    errors = g.store.validate_integrity()
    return {
        "synced_nodes": len(SEED),
        "synced_edges": len(g.store.list_edges()),
        "knowledge_dir": str(KNOWLEDGE),
        "integrity_errors": errors,
        "ok": not errors,
    }


def sync_corpus(kind: str, graph: EnterpriseMemoryGraph | None = None) -> dict[str, Any]:
    allowed = {"sop", "pricing", "policy", "faq", "golden_calls", "rulebook"}
    if kind not in allowed:
        raise ValueError(f"unsupported sync kind: {kind}")
    result = sync_all(graph)
    path = KNOWLEDGE / f"{kind}.jsonl"
    count = sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0
    return {"kind": kind, "records": count, **result}
