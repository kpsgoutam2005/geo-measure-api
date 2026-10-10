"""FastAPI application entry point.  Run with:  uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as files_router
from app.config import settings
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description=(
        "Upload a Shapefile (.zip) or KML file and get the area of every polygon and the "
        "length of every line, measured in a suitable projected (metric) coordinate system."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(files_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
