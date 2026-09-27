from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

from tiktok_osint.logging_config import log_event

T = TypeVar("T")


def retry_sqlite_write(
    operation: Callable[[], T],
    *,
    event: str,
    logger: logging.Logger,
    attempts: int = 3,
    base_delay_sec: float = 0.05,
) -> T:
    """Retry only transient database operational errors, never validation failures."""
    if attempts < 1:
        raise ValueError("attempts phải >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except OperationalError as exc:
            if attempt >= attempts:
                log_event(logger, logging.ERROR, f"{event}.failed", attempt=attempt, error=exc)
                raise
            delay = base_delay_sec * (2 ** (attempt - 1))
            log_event(logger, logging.WARNING, f"{event}.retry", attempt=attempt, delay=delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")
