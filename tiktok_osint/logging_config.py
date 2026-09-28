from __future__ import annotations

import logging

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _CONFIGURED = True


def log_event(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, "%s %s", event, details)
