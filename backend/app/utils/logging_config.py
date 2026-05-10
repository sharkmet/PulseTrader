"""
Loguru structured logging setup.

Intercepts all stdlib logging.Logger calls and routes them through loguru,
so uvicorn, SQLAlchemy, APScheduler, and our own modules all emit in the
same structured format.

Call setup_loguru() once at app startup before any logging happens.
"""
from __future__ import annotations

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Redirect stdlib logging → loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_loguru(level: str = "INFO", json: bool = False) -> None:
    """
    Configure loguru as the global log sink.

    level: minimum log level (INFO in prod, DEBUG in dev)
    json:  emit newline-delimited JSON (useful for log aggregators)
    """
    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(0)

    # Silence noisy libraries at WARNING
    for noisy in ("uvicorn.access", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.remove()

    if json:
        logger.add(
            sys.stdout,
            level=level,
            serialize=True,        # NDJSON output
            backtrace=False,
            diagnose=False,
        )
    else:
        logger.add(
            sys.stdout,
            level=level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
            backtrace=True,
            diagnose=False,        # don't expose locals in production
        )
