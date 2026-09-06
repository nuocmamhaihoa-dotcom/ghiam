"""Customer Lifetime Value (CLTV) Engine package."""
from __future__ import annotations

from typing import Any

__all__ = ["CLTVEngine", "get_cltv_engine"]


def __getattr__(name: str) -> Any:
    if name in {"CLTVEngine", "get_cltv_engine"}:
        from cltv.engine import CLTVEngine, get_cltv_engine

        return {"CLTVEngine": CLTVEngine, "get_cltv_engine": get_cltv_engine}[name]
    raise AttributeError(name)
