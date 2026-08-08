"""
api/app.py
FastAPI application factory for GreenScheduler.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from config.loader import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook."""
    settings = get_settings()

    # Warm up the scheduling engine on startup
    from scheduler.engine import SchedulingEngine

    app.state.engine = SchedulingEngine.from_settings(settings)

    yield

    # Cleanup on shutdown (nothing needed for now)


def create_app() -> FastAPI:
    app = FastAPI(
        title="GreenScheduler",
        description=(
            "Environmentally-aware AI infrastructure scheduler that jointly "
            "optimises carbon intensity, water stress, renewable availability, "
            "workload deadlines, and community priority."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api/v1")

    # Root endpoint
    @app.get("/")
    def root():
        return {
            "name": "GreenScheduler",
            "status": "running",
            "message": "Environmentally-aware AI infrastructure scheduler",
            "docs": "/docs",
        }

    return app


app = create_app()
