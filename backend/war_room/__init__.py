"""War Room AI — live ops command center."""
from __future__ import annotations

from typing import Any

__all__ = ["WarRoomEngine", "get_war_room_engine"]


def __getattr__(name: str) -> Any:
    if name in {"WarRoomEngine", "get_war_room_engine"}:
        from war_room.engine import WarRoomEngine, get_war_room_engine

        return {"WarRoomEngine": WarRoomEngine, "get_war_room_engine": get_war_room_engine}[name]
    raise AttributeError(name)
