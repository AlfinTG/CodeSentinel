"""Health and readiness endpoints per API_CONTRACTS.md."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


class HealthLiveResponse(BaseModel):
    status: str = "ok"


class HealthReadyResponse(BaseModel):
    status: str = "ok"
    database: str = "connected"
    queue: str = "connected"


@router.get("/live", response_model=HealthLiveResponse, status_code=status.HTTP_200_OK)
async def check_liveness():
    """Process liveness check.

    Returns:
        200 {"status": "ok"} when the FastAPI process is alive.
    """
    return HealthLiveResponse(status="ok")


@router.get("/ready", response_model=HealthReadyResponse)
def check_readiness(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> HealthReadyResponse:
    """Readiness probe for required dependencies (Database and Task Queue).

    Per API_CONTRACTS.md:
    - Verifies actual database and queue connectivity.
    - Returns 200 with status='ok' when all required dependencies are available.
    - Returns 503 with status='unavailable' when a required dependency fails.
    - Never exposes credentials, secrets, or internal connection strings.
    """
    db_connected = False
    queue_connected = False

    try:
        # 1. Probe database connectivity
        db.execute(text("SELECT 1"))
        db_connected = True

        # 2. Probe queue backend
        if settings.QUEUE_BACKEND == "database":
            # Verify queue storage table is queryable
            db.execute(text("SELECT 1 FROM jobs LIMIT 1"))
            queue_connected = True
        elif settings.QUEUE_BACKEND == "background":
            queue_connected = True
        else:
            queue_connected = False
    except Exception as exc:
        logger.warning("Health readiness probe dependency check failed: %s", type(exc).__name__)

    is_ready = db_connected and queue_connected
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthReadyResponse(
            status="unavailable",
            database="connected" if db_connected else "disconnected",
            queue="connected" if queue_connected else "disconnected",
        )

    response.status_code = status.HTTP_200_OK
    return HealthReadyResponse(
        status="ok",
        database="connected",
        queue="connected",
    )
