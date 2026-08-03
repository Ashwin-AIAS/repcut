"""structlog configuration: JSON to stdout, level driven by settings.

The engine never writes to stdout directly; every message goes through a bound
structlog logger so it stays machine-parseable.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from repcut.config import LogLevel


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure structlog for JSON output at ``level``.

    Idempotent: calling it again reconfigures cleanly, which matters because the
    app lifespan runs once per process but tests may build the app repeatedly.
    """
    numeric_level = logging.getLevelNamesMapping()[level]

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level, force=True)

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for ``name``."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
