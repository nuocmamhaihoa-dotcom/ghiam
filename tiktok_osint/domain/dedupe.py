from __future__ import annotations

from dataclasses import replace

from tiktok_osint.domain.models import ContactCandidate


def dedupe_contacts(items: list[ContactCandidate]) -> list[ContactCandidate]:
    """Keep the highest-confidence row per kind + normalized value and merge evidence."""
    best: dict[tuple[str, str], ContactCandidate] = {}
    evidence: dict[tuple[str, str], list[str]] = {}
    for item in items:
        key = (item.kind, item.normalized_value)
        bucket = evidence.setdefault(key, [])
        if item.evidence and item.evidence not in bucket:
            bucket.append(item.evidence)
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    merged: list[ContactCandidate] = []
    for key, item in best.items():
        merged.append(replace(item, evidence=" | ".join(evidence[key])[:500]))
    merged.sort(key=lambda candidate: (-candidate.confidence, candidate.kind, candidate.normalized_value))
    return merged


def annotate_cross_profile(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mark where the same public contact value was seen on other usernames."""
    owners: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        kind = row.get("contact_kind") or ""
        value = row.get("contact_value") or ""
        username = row.get("username") or ""
        if not kind or not value or not username:
            continue
        bucket = owners.setdefault((kind, value), [])
        if username not in bucket:
            bucket.append(username)
    annotated: list[dict[str, str]] = []
    for row in rows:
        kind = row.get("contact_kind") or ""
        value = row.get("contact_value") or ""
        username = row.get("username") or ""
        others = [name for name in owners.get((kind, value), []) if name != username]
        copied = dict(row)
        copied["also_seen_on"] = ",".join(others)
        annotated.append(copied)
    return annotated
