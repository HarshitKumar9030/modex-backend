"""
Modex Backend - FastAPI Application Entry Point.

A chat-based unified file processing tool powered by Gemini 2.5 Flash.
Supports PDF, image, and audio operations through natural language.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import settings
from core.database import init_db, get_db, close_db
from core.data_retention import cleanup_expired_data
from api.routes import conversations, files
from api.routes import beta as beta_routes
from models.api_models import HealthResponse

# -- Logging ---------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("modex")

# -- Scheduled cleanup ------------------------------------------------------

scheduler = AsyncIOScheduler()


async def _run_cleanup():
    """Background job: purge expired data per retention policy."""
    try:
        db = get_db()
        await cleanup_expired_data(db)
    except Exception as e:
        logger.error(f"Cleanup job failed: {e}")


# -- Lifespan ---------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Modex backend...")
    await init_db()
    logger.info("MongoDB initialized")

    # Schedule cleanup every 30 minutes
    scheduler.add_job(_run_cleanup, "interval", minutes=30, id="data_cleanup")
    scheduler.start()
    logger.info(f"Data retention: {settings.DATA_RETENTION_HOURS}h - cleanup every 30min")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await close_db()
    logger.info("Modex backend stopped")


# -- App --------------------------------------------------------------------

app = FastAPI(
    title="Modex API",
    description=(
        "Chat-based unified file processing tool. "
        "Upload PDFs, images, or audio files and describe what you want done in natural language. "
        "Powered by Gemini 2.5 Flash."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Routes -----------------------------------------------------------------

app.include_router(conversations.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(beta_routes.router, prefix="/api/v1")


@app.get("/api/v1/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    return HealthResponse(
        status="ok",
        version="1.0.0",
        data_retention_hours=settings.DATA_RETENTION_HOURS,
    )
