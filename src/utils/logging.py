"""Structured JSON logging configuration using structlog.

Call :func:`configure_logging` once at application startup (in ``main.py``).
Everywhere else, obtain a logger with :func:`get_logger`.

All output is written to stdout as newline-delimited JSON, ready for any
log-aggregation system (Datadog, Loki, CloudWatch, etc.).
"""

import logging
import sys
import zoneinfo
from datetime import datetime
from typing import Any

import structlog


def _make_timestamper(tz_name: str):
    """Return a structlog processor that stamps events with a timezone-aware ISO timestamp."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")

    def _stamp(logger: Any, method: str, event_dict: dict) -> dict:
        event_dict["timestamp"] = datetime.now(tz=tz).isoformat()
        return event_dict

    return _stamp


def configure_logging(level: str = "INFO", timezone: str = "UTC") -> None:
    """Configure structlog for JSON output to stdout.

    Should be called exactly once, before any logging takes place.

    Args:
        level: Standard Python log level name (``DEBUG``, ``INFO``,
               ``WARNING``, ``ERROR``). Case-insensitive.
        timezone: IANA timezone name for log timestamps (e.g. ``"America/New_York"``).
                  Defaults to ``"UTC"``.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configure the stdlib root logger so that structlog's LoggerFactory
    # delegates correctly and we capture third-party library logs too.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Pin httpx/httpcore to WARNING regardless of app log level so that
    # request bodies (which may contain credentials, e.g. Mosyle) are never
    # emitted to the log stream.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            # Inject log level and logger name into the event dict.
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            # ISO-8601 timestamp in the configured timezone.
            _make_timestamper(timezone),
            # Render exception info as a string rather than a Python object.
            structlog.processors.format_exc_info,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            # Final renderer: compact JSON, one object per line.
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Logger name (typically ``__name__`` of the calling module).
        **initial_values: Key-value pairs to bind to every log event emitted
            by this logger instance (e.g. ``provider="ninjaone"``).

    Returns:
        A :class:`structlog.stdlib.BoundLogger` instance.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger
