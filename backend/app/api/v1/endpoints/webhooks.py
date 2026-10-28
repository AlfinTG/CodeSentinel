"""GitHub Webhook API Endpoint per API_CONTRACTS.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.webhook import process_github_delivery

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


class WebhookResponse(BaseModel):
    accepted: bool
    duplicate: bool = False
    run_id: str | None = None
    reason: str | None = None


@router.post("/github", response_model=WebhookResponse, status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Authenticate raw webhook bytes via HMAC-SHA256 and transactionally queue durable run.

    - Verifies raw-body signature before parsing JSON.
    - Uses X-GitHub-Delivery for idempotent deduplication.
    - Never executes repository code or checks synchronously.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")

    # Read EXACT raw bytes before any JSON parsing for signature verification
    raw_body = await request.body()

    headers = {
        "x-hub-signature-256": request.headers.get("x-hub-signature-256", ""),
        "x-github-event": request.headers.get("x-github-event", ""),
        "x-github-delivery": request.headers.get("x-github-delivery", ""),
    }

    result = process_github_delivery(db, raw_body, headers)

    if not result.accepted and result.status_code != 202:
        raise HTTPException(
            status_code=result.status_code,
            detail={
                "error": {
                    "code": result.error_code or "webhook_error",
                    "message": result.reason or "Webhook processing failed",
                    "request_id": request_id,
                }
            },
        )

    response.status_code = result.status_code
    return WebhookResponse(
        accepted=result.accepted,
        duplicate=result.duplicate,
        run_id=result.run_id,
        reason=result.reason,
    )
