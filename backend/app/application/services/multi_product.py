"""Multi Product Intelligence — cross-sell / upsell / bundle suggestions."""

from __future__ import annotations

from typing import Any


_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "CORE",
        "name": "Gói Core",
        "complements": ["CARE", "BUNDLE_PLUS"],
        "upsell_to": "PRO",
        "triggers": ("cơ bản", "gói thường", "dùng thử"),
    },
    {
        "sku": "PRO",
        "name": "Gói Pro",
        "complements": ["CARE", "TRAINING"],
        "upsell_to": "ENTERPRISE",
        "triggers": ("nâng cấp", "pro", "nhiều tính năng"),
    },
    {
        "sku": "CARE",
        "name": "Gói Chăm sóc",
        "complements": ["CORE", "PRO"],
        "upsell_to": None,
        "triggers": ("bảo hành", "bảo trì", "sau bán"),
    },
    {
        "sku": "BUNDLE_PLUS",
        "name": "Bundle Plus",
        "complements": ["TRAINING"],
        "upsell_to": None,
        "triggers": ("combo", "trọn gói", "tiết kiệm"),
    },
    {
        "sku": "TRAINING",
        "name": "Đào tạo",
        "complements": ["PRO", "ENTERPRISE"],
        "upsell_to": None,
        "triggers": ("đào tạo", "onboarding", "huấn luyện"),
    },
    {
        "sku": "ENTERPRISE",
        "name": "Enterprise",
        "complements": ["TRAINING", "CARE"],
        "upsell_to": None,
        "triggers": ("doanh nghiệp", "quy mô lớn", "tích hợp"),
    },
]


class MultiProductIntelligence:
    def suggest(self, turns: list[dict[str, Any]], *, current_sku: str | None = None) -> dict[str, Any]:
        text = " ".join(str(t.get("text") or "") for t in turns).lower()
        if not text.strip():
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no transcript to infer product intent.",
            }
        matched = []
        for product in _CATALOG:
            hits = [c for c in product["triggers"] if c in text]
            if hits or product["sku"] == current_sku:
                matched.append({**product, "matched_triggers": hits})
        if not matched and current_sku:
            matched = [p for p in _CATALOG if p["sku"] == current_sku]
        if not matched:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no product triggers matched.",
                "suggestions": [],
            }

        primary = matched[0]
        by_sku = {p["sku"]: p for p in _CATALOG}
        cross = [by_sku[s]["name"] for s in primary.get("complements", []) if s in by_sku]
        upsell = by_sku.get(primary.get("upsell_to") or "")
        return {
            "status": "ok",
            "primary_product": primary["name"],
            "cross_sell": cross,
            "upsell": upsell["name"] if upsell else None,
            "bundle": cross[:2] + ([primary["name"]] if primary else []),
            "evidence_triggers": primary.get("matched_triggers") or [],
            "next_best_product": (upsell or by_sku.get((primary.get("complements") or [None])[0]) or {}).get("name"),
        }
