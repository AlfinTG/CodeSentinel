"""GitHub App OAuth 2.0 Web Application Flow and Installation Token client."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.config import settings

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    """Raised when GitHub OAuth exchange or API request fails."""

    pass


def build_github_oauth_url(state: str, redirect_uri: str | None = None) -> str:
    """Build the GitHub user authorization redirect URL."""
    params: dict[str, str] = {
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "state": state,
        "scope": "read:user,user:email",
    }
    effective_redirect = redirect_uri or settings.GITHUB_OAUTH_REDIRECT_URI
    if effective_redirect:
        params["redirect_uri"] = effective_redirect

    return f"{settings.GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str, redirect_uri: str | None = None) -> str:
    """Exchange authorization code for a GitHub user access token."""
    payload: dict[str, str] = {
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
        "code": code,
    }
    effective_redirect = redirect_uri or settings.GITHUB_OAUTH_REDIRECT_URI
    if effective_redirect:
        payload["redirect_uri"] = effective_redirect

    headers = {
        "Accept": "application/json",
        "User-Agent": "CodeSentinel-App",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                settings.GITHUB_OAUTH_TOKEN_URL,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("GitHub token exchange request failed: %s", type(exc).__name__)
        raise GitHubAuthError("Failed to connect to GitHub OAuth endpoint") from exc

    if "error" in data:
        error_msg = data.get("error_description") or data.get("error")
        logger.warning("GitHub OAuth exchange returned error: %s", error_msg)
        raise GitHubAuthError(f"GitHub OAuth error: {error_msg}")

    access_token = data.get("access_token")
    if not access_token:
        raise GitHubAuthError("GitHub OAuth response missing access_token")

    return access_token


async def fetch_github_user_profile(access_token: str) -> dict[str, Any]:
    """Retrieve the authenticated GitHub user profile."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "CodeSentinel-App",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(settings.GITHUB_OAUTH_USER_URL, headers=headers)
            resp.raise_for_status()
            profile = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch GitHub user profile: %s", type(exc).__name__)
        raise GitHubAuthError("Failed to fetch GitHub user profile") from exc

    if not isinstance(profile, dict) or "id" not in profile or "login" not in profile:
        raise GitHubAuthError("GitHub user profile payload missing required fields ('id', 'login')")

    return profile


def generate_github_app_jwt(
    app_id: str | None = None,
    private_key_pem: str | bytes | None = None,
    private_key_path: str | None = None,
) -> str:
    """Generate an RS256 JWT for GitHub App authentication (RFC 7519)."""
    effective_app_id = app_id or settings.GITHUB_APP_ID
    if not effective_app_id:
        raise GitHubAuthError("GITHUB_APP_ID is not configured")

    pem_bytes: bytes | None = None
    if private_key_pem:
        pem_bytes = (
            private_key_pem.encode("utf-8")
            if isinstance(private_key_pem, str)
            else private_key_pem
        )
    else:
        path = private_key_path or settings.GITHUB_APP_PRIVATE_KEY_PATH
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                pem_bytes = f.read()

    if not pem_bytes:
        raise GitHubAuthError("GitHub App private key PEM content or valid path is required")

    try:
        private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:
        raise GitHubAuthError(f"Failed to parse GitHub App private key: {exc}") from exc

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise GitHubAuthError("GitHub App private key must be an RSA private key")

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": effective_app_id,
    }

    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    claims_b64 = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )

    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    return f"{header_b64}.{claims_b64}.{sig_b64}"


async def get_installation_access_token(
    installation_id: int,
    jwt_token: str | None = None,
) -> str:
    """Generate an installation access token for GitHub App API interactions."""
    token = jwt_token or generate_github_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "CodeSentinel-App",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Failed to create installation access token: %s", type(exc).__name__)
        raise GitHubAuthError("Failed to create installation access token") from exc

    access_token = data.get("token")
    if not access_token:
        raise GitHubAuthError("Installation access token response missing 'token'")

    return access_token
