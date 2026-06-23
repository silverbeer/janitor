"""structlog configuration for Janitor.

Provides :func:`configure_logging` (called once at startup) and
:func:`get_logger` for module-level loggers.
"""

from __future__ import annotations

import logging
import sys

import structlog

__all__ = ["configure_logging", "get_logger"]

_configured = False


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    """Configure structlog and the stdlib logging bridge.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
        json: When True, render structured JSON instead of console output.
    """
    global _configured

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=numeric_level,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )

    # Route through stdlib logging so the output stream is resolved at write
    # time (important under pytest, which swaps sys.stderr between tests).
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, configuring defaults on first use."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)  # type: ignore[no-any-return]
