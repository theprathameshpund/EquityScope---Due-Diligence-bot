"""Structured JSON logging via structlog with run_id correlation.

Call `setup_logging()` once at process start. Use `bind_run_id(run_id)`
at the beginning of a report run; every log line emitted by any agent in
that context then carries the run_id.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import settings

_CONFIGURED = False


def setup_logging() -> None:
    """Configure structlog: JSON in production, pretty console in development."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor
    if settings.environment == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def bind_run_id(run_id: str) -> None:
    """Attach run_id to all subsequent log lines in this context."""
    structlog.contextvars.bind_contextvars(run_id=run_id)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog logger (configures logging on first use)."""
    setup_logging()
    return structlog.stdlib.get_logger(name)
