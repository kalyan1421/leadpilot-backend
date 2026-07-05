"""Main FastAPI application for Voice Summary."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.attendance import router as attendance_router
from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.calls import intel_router
from app.api.dashboard import router as dashboard_router
from app.api.team import router as team_router
from app.config import settings
from app.database import engine
from app.models import Base

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Voice Summary API",
    description="API for managing audio call information with transcripts and S3 audio files",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers.
# dashboard_router MUST come before intel_router: both mount under /api, and
# intel_router's GET /leads/{contact_key} would otherwise greedily match
# GET /leads/board (treating "board" as a contact key) — same pitfall as the
# existing /leads/dedupe-before-/leads/{contact_key} ordering below.
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(calls_router)
app.include_router(intel_router)
app.include_router(team_router)
app.include_router(attendance_router)


@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    logger.info("Starting Voice Summary API...")

    # Create database tables
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise

    # Durable pipeline recovery: re-dispatch any call left mid-flight by a crash/restart.
    try:
        from app.api.calls import recover_stuck_jobs
        n = recover_stuck_jobs()
        if n:
            logger.info(f"Recovered {n} stuck pipeline job(s) after restart")
    except Exception as e:
        logger.warning(f"Pipeline job recovery skipped: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on application shutdown."""
    logger.info("Shutting down Voice Summary API...")


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Voice Summary API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "Voice Summary API"}


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )
