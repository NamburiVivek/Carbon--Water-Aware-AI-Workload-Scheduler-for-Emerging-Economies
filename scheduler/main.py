"""
scheduler/main.py
GreenScheduler entry point.

Starts the FastAPI server and background scheduling loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from config.loader import get_settings


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    if fmt == "json":
        try:
            import structlog  # type: ignore
            structlog.configure(
                processors=[
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.dev.ConsoleRenderer(),
                ],
                logger_factory=structlog.PrintLoggerFactory(),
            )
            return
        except ImportError:
            pass  # fall through to standard logging

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    settings = get_settings()
    setup_logging(settings.logging.level, settings.logging.format)
    logger = logging.getLogger(__name__)

    logger.info(
        "Starting GreenScheduler on %s:%d",
        settings.server.host,
        settings.server.port,
    )

    uvicorn.run(
        "api.app:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.reload,
        log_level=settings.logging.level.lower(),
    )


if __name__ == "__main__":
    main()
