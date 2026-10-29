"""Authentication and identity endpoints per API_CONTRACTS.md and Phase 3 requirements."""

from __future__ import annotations

import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.audit import AuditEvent
from app.models.project import Project
from app.models.user import User
from app.schemas.user import CurrentUserResponse, UserRead
from app.services.github_auth import (
    GitHubAuthError,
    build_github_oauth_url,
    exchange_code_for_token,
    fetch_github_user_profile,
)
from app.services.session import (
    create_oauth_state_token,
    create_session_token,
    is_safe_relative_url,
    revoke_session,
    verify_oauth_state,
    verify_session_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/github/login")
def github_login(
    return_to: str | None = None,
) -> RedirectResponse:
    """Initiates GitHub OAuth 2.0 Web Application Flow with cryptographic CSRF state.

    The return_to parameter is validated to be a safe relative URL on this host (prevents open redirects).
    """
    safe_return = return_to if is_safe_relative_url(return_to) else None
    state, signed_state = create_oauth_state_token(return_to=safe_return)
    auth_url = build_github_oauth_url(state=state)

    response = RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        key=settings.GITHUB_OAUTH_STATE_COOKIE_NAME,
        value=signed_state,
        max_age=settings.GITHUB_OAUTH_STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Handle GitHub OAuth callback, validate CSRF state, and issue encrypted session cookie."""
    request_id = getattr(request.state, "request_id", "req-unknown")

    if error:
        logger.warning("GitHub OAuth callback received error: %s (%s)", error, error_description)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "oauth_error",
                    "message": error_description or error,
                    "request_id": request_id,
                }
            },
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "missing_oauth_parameters",
                    "message": "Required 'code' and 'state' query parameters are missing",
                    "request_id": request_id,
                }
            },
        )

    state_cookie = request.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME)
    is_valid_state, safe_return_to = verify_oauth_state(raw_state=state, cookie_value=state_cookie)
    if not is_valid_state:
        logger.warning("GitHub OAuth callback CSRF state validation failed (request_id=%s)", request_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "invalid_state",
                    "message": "Invalid or expired CSRF state parameter",
                    "request_id": request_id,
                }
            },
        )

    try:
        access_token = await exchange_code_for_token(code=code)
        profile = await fetch_github_user_profile(access_token=access_token)
    except GitHubAuthError as exc:
        logger.error("GitHub OAuth token exchange/profile fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "oauth_exchange_failed",
                    "message": "Authentication failed during GitHub token exchange",
                    "request_id": request_id,
                }
            },
        ) from exc

    github_user_id = profile["id"]
    login = profile["login"]
    display_name = profile.get("name") or login

    try:
        user = db.query(User).filter(User.github_user_id == github_user_id).first()
        if not user:
            user = User(
                github_user_id=github_user_id,
                login=login,
                display_name=display_name,
            )
            db.add(user)
        else:
            user.login = login
            user.display_name = display_name
        db.commit()
        db.refresh(user)
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist user profile in database: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "database_error",
                    "message": "Failed to persist user profile",
                    "request_id": request_id,
                }
            },
        ) from exc

    session_token = create_session_token(str(user.id))
    # Destination strictly validated against open redirect
    redirect_path = safe_return_to if safe_return_to and is_safe_relative_url(safe_return_to) else "/"
    redirect_url = f"{settings.FRONTEND_BASE_URL.rstrip('/')}{redirect_path}"

    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    response.delete_cookie(
        key=settings.GITHUB_OAUTH_STATE_COOKIE_NAME,
        path="/",
    )
    return response


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Revokes session server-side and clears authentication cookies."""
    session_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_cookie:
        session_data = verify_session_token(session_cookie)
        if session_data and "session_id" in session_data:
            session_id = session_data["session_id"]
            user_id = session_data.get("user_id")
            revoke_session(session_id)
            if user_id:
                with contextlib.suppress(Exception):
                    audit = AuditEvent(
                        actor=f"user:{user_id}",
                        action="auth.logout",
                        target_type="user",
                        target_id=str(user_id),
                        safe_metadata={"session_id": session_id},
                    )
                    db.add(audit)
                    db.commit()

    response.delete_cookie(key=settings.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=settings.GITHUB_OAUTH_STATE_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUserResponse:
    """Returns current user identity and authorized project count.

    Never exposes tokens, private keys, or installation secrets.
    """
    authorized_count = (
        db.query(Project)
        .filter(Project.owner_user_id == current_user.id, Project.active.is_(True))
        .count()
    )

    return CurrentUserResponse(
        user=UserRead.model_validate(current_user),
        authorized_project_count=authorized_count,
    )
