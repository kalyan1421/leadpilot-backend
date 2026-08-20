"""Main FastAPI application for Voice Summary."""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.ratelimit import limiter
from app.api.attendance import router as attendance_router
from app.api.auth import router as auth_router
from app.api.call_log import router as call_log_router
from app.api.calls import router as calls_router
from app.api.calls import intel_router
from app.api.dashboard import router as dashboard_router
from app.api.follow_ups import router as follow_ups_router
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

# Rate limiting (slowapi): per-route @limiter.limit(...) decorators (see
# app/api/auth.py) need the limiter on app.state, plus a handler that turns an
# exceeded limit into a 429 instead of falling through to the generic 500 below.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add CORS middleware
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
# allow_credentials with a wildcard origin is an invalid combo (browsers ignore
# it) and is flagged by scanners. This API authenticates with bearer tokens in
# the Authorization header, not cookies, so credentialed CORS isn't needed when
# origins are wildcarded — only enable it when explicit origins are configured.
_allow_credentials = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers.
# calls_router/intel_router MUST come before dashboard_router: both mount
# under /api, and intel_router's static GET /leads/dedupe (and POST /leads)
# would otherwise be shadowed by dashboard_router's GET /leads/{lead_id} —
# FastAPI/Starlette resolves an overlapping path shape by registration
# order across the WHOLE app, not by per-route specificity or which router
# it came from. (intel_router no longer defines its own /leads/{contact_key}
# — that wildcard-vs-wildcard collision with dashboard's /leads/{lead_id} is
# now resolved by a single dispatching handler in dashboard.py's
# get_lead_detail, keyed on the caller's role, instead of two competing
# routes silently shadowing each other.)
app.include_router(auth_router)
app.include_router(calls_router)
app.include_router(intel_router)
app.include_router(dashboard_router)
app.include_router(team_router)
app.include_router(attendance_router)
app.include_router(follow_ups_router)
app.include_router(call_log_router)


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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Same response shape as FastAPI's default handler, but scrubs the raw
    submitted value for any field whose name mentions "password". Pydantic v2
    error dicts include an "input" key by default — for a too-short or
    too-long password on register/change-password/reset-password, that
    default behavior echoed the user's plaintext password back in the 422
    response body.

    Also drops "ctx" (or stringifies it) when it's not JSON-serializable —
    a `ValueError`-raising AfterValidator (e.g. the bcrypt 72-byte length
    check on Password/BcryptSafeString) produces a `ctx: {"error":
    ValueError(...)}` entry: a real exception OBJECT, not a string.
    json.dumps can't serialize that, so any such validation failure crashed
    with an unhandled 500 from INSIDE this error handler itself — the
    request that should have gotten a clean 422 instead. "msg" already
    carries the same message as a plain string, so ctx is redundant here
    anyway.
    """
    errors = []
    for err in exc.errors():
        err = dict(err)
        if any("password" in str(part).lower() for part in err.get("loc", ())):
            err.pop("input", None)
        err.pop("ctx", None)
        errors.append(err)
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler. Log the method+path and full traceback so a 500
    is diagnosable from the server log (the client still gets a generic message)."""
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )
