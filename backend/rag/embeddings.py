"""Deterministic hash embeddings + lexical helpers."""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
_STOP = {
    "là", "bao", "nhiêu", "của", "và", "hoặc", "cho", "với", "các", "những",
    "the", "a", "an", "of", "to", "in", "on", "for", "what", "how", "is",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "") if len(t) > 1]


def content_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _STOP and not t.isdigit()}


def lexical_overlap(query: str, text: str) -> float:
    q = content_tokens(query)
    if not q:
        return 0.0
    d = content_tokens(text)
    if not d:
        return 0.0
    inter = len(q & d)
    return inter / float(len(q))


def embed(text: str, *, dim: int = 128) -> list[float]:
    vec = [0.0] * dim
    toks = tokenize(text)
    if not toks:
        return vec
    for tok in toks:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, 32, 4):
            idx = int.from_bytes(digest[i : i + 4], "little") % dim
            sign = 1.0 if digest[i] % 2 == 0 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def overlap_stats(query: str, text: str) -> tuple[float, int, set[str]]:
    q = content_tokens(query)
    if not q:
        return 0.0, 0, set()
    d = content_tokens(text)
    inter = q & d
    return (len(inter) / float(len(q)), len(inter), inter)
