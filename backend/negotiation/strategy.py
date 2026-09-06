"""Strategy options + dynamic strategy graph (not a static tree)."""
from __future__ import annotations

from typing import Any

from negotiation.predictor import detect_emotion, detect_objection_family
from negotiation.types import (
    STRATEGY_KINDS,
    NegotiationStrategy,
    StrategyGraph,
    StrategyGraphNode,
    new_id,
    now_iso,
)

_LABELS = {
    "empathy": "Chiến lược đồng cảm",
    "value": "Chiến lược giá trị",
    "comparison": "Chiến lược so sánh",
    "urgency": "Chiến lược khẩn cấp",
    "clarify": "Chiến lược làm rõ nhu cầu",
}

_SCRIPTS: dict[str, dict[str, tuple[str, str]]] = {
    "price": {
        "empathy": (
            "Em hiểu anh/chị đang cân nhắc ngân sách. Phần nào đang khiến gói này cảm thấy cao nhất ạ?",
            "Rẻ thế này không mua là tiếc đó anh/chị.",
        ),
        "value": (
            "Nếu nhìn theo tháng, chi phí thực tế khoảng {monthly}. Đổi lại anh/chị nhận {benefit}.",
            "Đắt thì đắt, nhưng bên em không giảm đâu.",
        ),
        "comparison": (
            "So với giải pháp A, gói này tiết kiệm hơn khoảng {delta}% trong 12 tháng vì {reason}.",
            "Bên kia kém, đừng nghe họ.",
        ),
        "urgency": (
            "Ưu đãi hiện tại còn đến {deadline}. Em giữ suất này giúp anh/chị trong hôm nay được không?",
            "Mai hết khuyến mãi, mua ngay kẻo lỡ!",
        ),
        "clarify": (
            "Để em tư vấn đúng ngân sách: anh/chị đang để khoảng bao nhiêu và tiêu chí ưu tiên nhất là gì?",
            "Anh/chị có bao nhiêu tiền?",
        ),
    },
    "timing": {
        "empathy": (
            "Em hiểu thời điểm chưa thuận. Phần nào đang khiến anh/chị muốn để sau ạ?",
            "Chần chừ là mất cơ hội đó.",
        ),
        "value": (
            "Nếu bắt đầu tuần này, anh/chị sẽ nhận {benefit} sớm hơn khoảng {days} ngày.",
            "Cứ để sau đi, không sao đâu.",
        ),
        "comparison": (
            "Nhiều khách cũng định để sau, nhưng khi so chi phí trì hoãn thì chọn chốt sớm hơn.",
            "Ai cũng để sau hết.",
        ),
        "urgency": (
            "Suất hỗ trợ setup miễn phí chỉ còn đến {deadline}. Em giữ giúp anh/chị nhé?",
            "Phải quyết ngay lập tức!",
        ),
        "clarify": (
            "Anh/chị đang chờ điều kiện gì để sẵn sàng? Em sắp xếp lộ trình phù hợp.",
            "Bao giờ anh/chị mới mua?",
        ),
    },
    "default": {
        "empathy": (
            "Em ghi nhận lo ngại của anh/chị. Em muốn hiểu rõ hơn để hỗ trợ đúng nhu cầu.",
            "Anh/chị nghĩ vậy là sai.",
        ),
        "value": (
            "Điểm khác biệt chính là {benefit} — giúp anh/chị đạt {outcome}.",
            "Sản phẩm bên em là nhất.",
        ),
        "comparison": (
            "Nếu so trên tiêu chí {criteria}, phương án này phù hợp hơn vì {reason}.",
            "Đối thủ toàn kém.",
        ),
        "urgency": (
            "Hiện còn {slots} suất ưu đãi. Anh/chị muốn em giữ một suất trong hôm nay không ạ?",
            "Mua ngay không thì hết!",
        ),
        "clarify": (
            "Anh/chị đang ưu tiên tiêu chí nào nhất: hiệu quả, ngân sách, hay thời gian?",
            "Anh/chị muốn gì?",
        ),
    },
}


def _fill(template: str, context: dict[str, Any]) -> str:
    data = {
        "monthly": context.get("monthly", "x00.000đ"),
        "benefit": context.get("benefit", "hiệu quả dài hạn"),
        "delta": context.get("delta", "15"),
        "reason": context.get("reason", "chi phí vận hành thấp hơn"),
        "deadline": context.get("deadline", "cuối ngày hôm nay"),
        "days": context.get("days", "14"),
        "outcome": context.get("outcome", "mục tiêu của anh/chị"),
        "criteria": context.get("criteria", "giá trị/đồng"),
        "slots": context.get("slots", "3"),
    }
    try:
        return template.format(**data)
    except (KeyError, ValueError):
        return template


def _scores(kind: str, family: str, emotion: str, buy_p: float, exit_r: float) -> tuple[float, float]:
    win, risk = 0.45, 0.35
    if kind == "empathy":
        win += 0.18 if emotion in {"anxious", "frustrated"} else 0.08
        risk -= 0.08
    elif kind == "value":
        win += 0.16 if family == "price" else 0.1
        risk += 0.02
    elif kind == "comparison":
        win += 0.14 if family == "competitor" else 0.08
        risk += 0.05
    elif kind == "urgency":
        win += 0.12 if buy_p >= 0.45 else 0.02
        risk += 0.18 if emotion in {"resistant", "frustrated"} else 0.08
    elif kind == "clarify":
        win += 0.12 if family in {"need", "general"} else 0.06
        risk -= 0.05
    win += buy_p * 0.15
    risk += exit_r * 0.2
    return round(max(0.05, min(0.95, win)), 4), round(max(0.05, min(0.95, risk)), 4)


def generate_strategies(
    customer_utterance: str,
    *,
    prediction: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> list[NegotiationStrategy]:
    context = context or {}
    prediction = prediction or {}
    family = detect_objection_family(customer_utterance)
    emotion = detect_emotion(customer_utterance)
    buy_p = float(prediction.get("buy_probability", 0.4))
    exit_r = float(prediction.get("exit_risk", 0.3))
    pack = _SCRIPTS.get(family, _SCRIPTS["default"])

    out: list[NegotiationStrategy] = []
    for kind in STRATEGY_KINDS:
        rec, forbid = pack.get(kind) or _SCRIPTS["default"][kind]
        win, risk = _scores(kind, family, emotion, buy_p, exit_r)
        out.append(
            NegotiationStrategy(
                strategy_id=new_id("stg"),
                kind=kind,
                label=_LABELS[kind],
                win_probability=win,
                risk_score=risk,
                recommended_script=_fill(rec, context),
                forbidden_script=_fill(forbid, context),
                rationale=f"family={family}; emotion={emotion}; fit={kind}",
                evidence=[
                    f"customer_signal={family}",
                    f"emotion={emotion}",
                    f"buy_probability={buy_p}",
                    f"exit_risk={exit_r}",
                ],
                next_best_action=_fill(rec, context),
            )
        )
    out.sort(key=lambda s: (s.win_probability - s.risk_score * 0.5), reverse=True)
    return out


def build_strategy_graph(
    strategies: list[NegotiationStrategy],
    prediction: dict[str, Any],
    *,
    customer_utterance: str = "",
) -> StrategyGraph:
    root_id = new_id("node")
    graph_id = new_id("graph")
    family = detect_objection_family(customer_utterance)
    buy_p = float(prediction.get("buy_probability", 0.4))
    exit_r = float(prediction.get("exit_risk", 0.3))
    root = StrategyGraphNode(
        node_id=root_id,
        label=f"state:{family}",
        state="current_objection",
        strategies=[s.strategy_id for s in strategies],
        win_probability=buy_p,
        exit_risk=exit_r,
    )
    nodes = [root]
    evolution: list[dict[str, Any]] = [
        {"ts": now_iso(), "event": "graph_initialized", "root": root_id, "strategy_count": len(strategies)}
    ]

    for s in strategies:
        child_id = new_id("node")
        s.graph_node_id = child_id
        child = StrategyGraphNode(
            node_id=child_id,
            label=s.label,
            state=f"strategy:{s.kind}",
            strategies=[s.strategy_id],
            win_probability=s.win_probability,
            exit_risk=s.risk_score,
        )
        accept_id, object_id, exit_id = new_id("node"), new_id("node"), new_id("node")
        accept = StrategyGraphNode(
            node_id=accept_id,
            label="customer_accepts",
            state="progress",
            win_probability=min(0.95, s.win_probability + 0.15),
            exit_risk=max(0.05, s.risk_score - 0.15),
        )
        obj = StrategyGraphNode(
            node_id=object_id,
            label="customer_reobjects",
            state="loop",
            win_probability=max(0.1, s.win_probability - 0.1),
            exit_risk=min(0.9, s.risk_score + 0.1),
        )
        ex = StrategyGraphNode(
            node_id=exit_id,
            label="customer_exits",
            state="exit",
            win_probability=0.05,
            exit_risk=0.9,
        )
        child.edges = [
            {"to": accept_id, "condition": "accept", "weight": s.win_probability},
            {
                "to": object_id,
                "condition": "reobject",
                "weight": max(0.05, 1 - s.win_probability - s.risk_score * 0.3),
            },
            {"to": exit_id, "condition": "exit", "weight": s.risk_score},
        ]
        root.edges.append({"to": child_id, "condition": f"choose:{s.kind}", "weight": s.win_probability})
        nodes.extend([child, accept, obj, ex])
        evolution.append(
            {
                "ts": now_iso(),
                "event": "strategy_branch",
                "strategy_id": s.strategy_id,
                "kind": s.kind,
                "node_id": child_id,
            }
        )

    return StrategyGraph(graph_id=graph_id, root_node_id=root_id, nodes=nodes, evolution=evolution)


def pick_best_strategy(strategies: list[NegotiationStrategy]) -> NegotiationStrategy:
    return max(strategies, key=lambda s: s.win_probability - 0.45 * s.risk_score)


def strategy_consistency(strategies: list[NegotiationStrategy]) -> float:
    if len(strategies) < 3:
        return 0.0
    kinds = {s.kind for s in strategies}
    diversity = len(kinds) / max(1, len(STRATEGY_KINDS))
    utilities = [s.win_probability - 0.45 * s.risk_score for s in strategies]
    ordered = utilities == sorted(utilities, reverse=True)
    scripts_ok = all(
        s.recommended_script and s.forbidden_script and s.recommended_script != s.forbidden_script
        for s in strategies
    )
    return round(min(1.0, 0.4 * diversity + (0.3 if ordered else 0.1) + (0.3 if scripts_ok else 0.0)), 4)
