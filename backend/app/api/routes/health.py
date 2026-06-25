"""
Health check endpoint — verifies database connectivity.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import async_session

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Public health check verifying database connectivity only.
    Returns 200 if OK, 503 if database is down.
    No sensitive configuration details are exposed.
    """
    db_ok = True
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    status_code = 200 if db_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if db_ok else "degraded",
            "service": "strom-import-api",
        },
    )
