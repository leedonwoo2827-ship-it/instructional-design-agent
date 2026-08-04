from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes_api import router as api_router
from .routes_ui import router as ui_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    app = FastAPI(title="voicewright", version="0.1.0", description="Local Korean TTS (Supertonic)")

    web_dir = Path(__file__).resolve().parent.parent / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")

    app.include_router(ui_router, tags=["ui"])
    app.include_router(api_router, prefix="/api", tags=["api"])

    return app
