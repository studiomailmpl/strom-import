"""
STRØM Import API — FastAPI entry point.

Production-grade configuration:
- CORS with env-driven origins
- Request ID middleware for tracing
- Global exception handler with structured logging
- Rate limiting via slowapi on expensive endpoints
- Security headers
"""

import logging
import uuid as uuid_mod
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.api.routes import imports, products, shopify, health, brands, images, seo, drive
from app.api.routes.settings import router as settings_router

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("strom-import")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks with database connectivity validation."""
    logger.info(
        "STRØM Import API starting — debug=%s, db=%s",
        settings.debug,
        "localhost" if "localhost" in settings.database_url else "remote",
    )

    # ── Log image pipeline health ──
    from app.services.image_service import log_pipeline_health
    log_pipeline_health()

    # ── Validate database connectivity at startup ──
    from sqlalchemy import text
    from app.core.database import async_session
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection verified ✓")
    except Exception as db_err:
        logger.critical(
            "DATABASE CONNECTION FAILED at startup: %s — "
            "the API will start but ALL database operations will fail. "
            "Check DATABASE_URL and ensure the database is reachable.",
            db_err,
        )
        if not settings.debug:
            # In production, fail fast — don't start a broken API
            raise RuntimeError(f"Cannot connect to database: {db_err}") from db_err

    yield
    logger.info("STRØM Import API shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Attach rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — in debug mode allow common dev origins
_dev_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins if settings.debug else settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_and_security_headers(request: Request, call_next):
    """Add request tracing ID and security headers to every response."""
    request_id = str(uuid_mod.uuid4())[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not settings.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "Unhandled error [request_id=%s, path=%s]: %s",
        request_id, request.url.path, exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Der opstod en intern fejl"},
    )

# Routes
app.include_router(health.router, tags=["health"])
app.include_router(
    imports.router,
    prefix=f"/api/{settings.api_version}/imports",
    tags=["imports"],
)
app.include_router(
    products.router,
    prefix=f"/api/{settings.api_version}/products",
    tags=["products"],
)
app.include_router(
    shopify.router,
    prefix=f"/api/{settings.api_version}/shopify",
    tags=["shopify"],
)
app.include_router(settings_router, prefix=f"/api/{settings.api_version}", tags=["settings"])
app.include_router(
    brands.router,
    prefix=f"/api/{settings.api_version}",
    tags=["brands"],
)
app.include_router(
    images.router,
    prefix=f"/api/{settings.api_version}",
    tags=["images"],
)
app.include_router(
    seo.router,
    prefix=f"/api/{settings.api_version}",
    tags=["seo"],
)
app.include_router(
    drive.router,
    prefix=f"/api/{settings.api_version}",
    tags=["drive"],
)
