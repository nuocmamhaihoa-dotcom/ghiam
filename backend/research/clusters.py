"""Cluster discovery engine."""
from __future__ import annotations

from typing import Any

from research.lexicon import jaccard, normalize
from self_learning.types import Cluster, Evidence, PatternHit, new_id

CLUSTER_SEEDS = {
    "delay_consider": ("Delay / Consider", "Khách trì hoãn quyết định — cần follow-up mềm."),
    "pay_tonight": ("Pay Tonight Intent", "Khách sẽ chuyển khoản tối — buying signal mạnh."),
    "ghost_month": ("Ghost Month Delay", "Khách lấy lý do tháng cô hồn để hoãn."),
    "price_pushback": ("Price Pushback", "Khách phản đối giá — value stacking."),
    "compare_other": ("Competitor Compare", "Khách so sánh đối thủ."),
}


class ClusterEngine:
    def cluster(
        self,
        patterns: list[PatternHit] | list[dict[str, Any]],
        *,
        min_size: int = 2,
    ) -> list[Cluster]:
        items: list[tuple[str, str, list[Evidence], float]] = []
        for p in patterns:
            if isinstance(p, PatternHit):
                items.append((p.normalized, p.text, list(p.evidence), p.confidence))
            else:
                items.append((
                    normalize(str(p.get("normalized") or p.get("text") or "")),
                    str(p.get("text") or ""),
                    [],
                    float(p.get("confidence") or 0.7),
                ))

        used = [False] * len(items)
        clusters: list[Cluster] = []
        for i, (norm_i, text_i, ev_i, conf_i) in enumerate(items):
            if used[i] or not norm_i:
                continue
            members = [text_i]
            evidence = list(ev_i)
            confs = [conf_i]
            used[i] = True
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                norm_j, text_j, ev_j, conf_j = items[j]
                if jaccard(norm_i, norm_j) >= 0.34:
                    used[j] = True
                    members.append(text_j)
                    evidence.extend(ev_j)
                    confs.append(conf_j)
            if len(set(members)) < min_size and len(items) >= min_size:
                continue
            seed = self._seed(norm_i)
            name, meaning = CLUSTER_SEEDS.get(
                seed,
                (f"Cluster: {members[0][:40]}", "Nhóm câu tương đồng cần QA đặt tên."),
            )
            clusters.append(
                Cluster(
                    cluster_id=new_id("clu"),
                    name=name,
                    variants=sorted(set(members), key=len),
                    hidden_meaning=meaning,
                    confidence=round(sum(confs) / max(1, len(confs)), 4),
                    size=len(set(members)),
                    evidence=evidence[:20],
                )
            )
        return clusters

    def _seed(self, norm: str) -> str:
        if any(x in norm for x in ("chuyen khoan toi", "chuyen toi", "toi nay", "toi em", "chuyen khoan")):
            return "pay_tonight"
        if any(x in norm for x in ("co hon", "thang bay", "tam linh", "via")):
            return "ghost_month"
        if any(x in norm for x in ("coi", "xem", "nhin", "can nhac", "suy nghi", "de em")):
            return "delay_consider"
        if any(x in norm for x in ("gia", "dat", "re", "cao")):
            return "price_pushback"
        if any(x in norm for x in ("ben khac", "doi thu", "so voi")):
            return "compare_other"
        return "generic"

    def aggregate_corpus(self, phrases: list[str]) -> list[Cluster]:
        fake = [
            PatternHit(
                pattern_id=new_id("pat"),
                kind="phrase",
                text=p,
                normalized=normalize(p),
                novelty=0.7,
                confidence=0.8,
            )
            for p in phrases
        ]
        clusters = self.cluster(fake, min_size=2)
        if clusters:
            return clusters

        # Seed fallback ensures near-synonym families still form a cluster for QA.
        if len(phrases) < 2:
            return []
        seed = self._seed(normalize(phrases[0]))
        name, meaning = CLUSTER_SEEDS.get(
            seed,
            (f"Cluster: {phrases[0][:40]}", "Nhóm câu tương đồng cần QA đặt tên."),
        )
        return [
            Cluster(
                cluster_id=new_id("clu"),
                name=name,
                variants=sorted(set(phrases), key=len),
                hidden_meaning=meaning,
                confidence=0.72,
                size=len(set(phrases)),
                evidence=[],
            )
        ]
