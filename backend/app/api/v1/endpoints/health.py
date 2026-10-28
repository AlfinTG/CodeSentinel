"""Health and readiness endpoints per API_CONTRACTS.md."""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

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


@router.get("/ready", response_model=HealthReadyResponse, status_code=status.HTTP_200_OK)
async def check_readiness(response: Response):
    """Readiness probe for required dependencies (Database and Task Queue).

    Never exposes credentials, secrets, or internal connection strings.
    """
    # Baseline readiness logic; expanded with real connection checks in Phase 1
    return HealthReadyResponse(
        status="ok",
        database="connected",
        queue="connected",
    )
