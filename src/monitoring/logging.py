"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SECRET_PATTERNS = [
    re.compile(r"(key=)[^&\s\"']+", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r"(AIza)[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(gsk_)[0-9A-Za-z]{10,}"),
    re.compile(r"(sk-)[0-9A-Za-z]{10,}"),
    re.compile(r"(ghp_|github_pat_)[0-9A-Za-z_]{10,}"),
]
_SECRET_KEYS = {"api_key", "authorization", "token", "password", "secret", "x-goog-api-key"}


def redact(value: str) -> str:
    for pat in _SECRET_PATTERNS:
        value = pat.sub(lambda m: m.group(1) + "***", value)
    return value


def _redact_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for k, v in list(event_dict.items()):
        if k.lower() in _SECRET_KEYS:
            event_dict[k] = "***"
        elif isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
