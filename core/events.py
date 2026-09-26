"""Lightweight event bus — in-process pub/sub (spec §2 events module)."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

import core.logging as logging

log = logging.get_logger("events")

_handlers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event: str, handler: Callable) -> None:
    _handlers[event].append(handler)


def emit(event: str, **payload) -> None:
    log.info("event=%s %s", event, payload or "")
    for h in list(_handlers.get(event, [])):
        try:
            h(**payload)
        except Exception as e:
            log.error("handler for %s failed: %s", event, e)