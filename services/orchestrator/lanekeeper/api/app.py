"""FastAPI application: `uvicorn lanekeeper.api.app:app` (or `lanekeeper serve`)."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import demo, media, public, staff
from .common import install_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="LaneKeeper OS orchestrator", version="2.0.0-alpha",
                  description="Simulation-first municipal bike-lane operations API. No real robot control exists in this build.")
    origins = [o.strip() for o in os.environ.get("LK_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type", "x-lk-timestamp", "x-lk-signature"])
    install_error_handlers(app)
    app.include_router(public.router)
    app.include_router(staff.router)
    app.include_router(media.router)
    if demo.enabled():
        app.include_router(demo.router)
    return app


app = create_app()
