"""Phase 3 Gate Tests: GitHub App OAuth 2.0 Authentication & Identity Infrastructure.

Test coverage matrix:
  AUTH-01  GET /api/v1/auth/github/login generates CSRF state token, sets cookie, and redirects.
  AUTH-02  GET /api/v1/auth/github/callback rejects missing code or state parameters.
  AUTH-03  GET /api/v1/auth/github/callback rejects mismatched or tampered CSRF state.
  AUTH-04  GET /api/v1/auth/github/callback handles GitHub-returned error query parameters.
  AUTH-05  GET /api/v1/auth/github/callback exchanges code, creates user, issues encrypted session cookie.
  AUTH-06  GET /api/v1/auth/github/callback updates profile if user already exists in database.
  AUTH-07  Session cookie authenticates protected endpoints (/auth/me, /projects) without test header.
  AUTH-08  Tampered, expired, or invalid session cookie is rejected.
  AUTH-09  POST /api/v1/auth/logout revokes session cookies.
  AUTH-10  In production (CODESENTINEL_ENV='production'), unauthenticated requests return 401 Unauthorized.
  AUTH-11  In production, X-Test-User-ID header is ignored and demo-dev fallback is disabled.
  AUTH-12  generate_github_app_jwt produces a valid RFC 7519 RS256 JWT verifiable with RSA public key.
  AUTH-13  get_installation_access_token requests and parses token from GitHub API.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.main import app
from app.models.audit import AuditEvent
from app.models.project import Project
from app.models.user import User
from app.services.github_auth import (
    generate_github_app_jwt,
    get_installation_access_token,
)
from app.services.session import (
    create_oauth_state_token,
    create_session_token,
    is_safe_relative_url,
    verify_oauth_state,
    verify_session_token,
)


@pytest.fixture
def sync_client(db: Session):
    """Synchronous test client with in-memory database override."""
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def rsa_key_pair():
    """Generate an ephemeral RSA key pair for testing GitHub App JWT signatures."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_key = private_key.public_key()
    return private_pem, public_key


def test_auth01_login_redirect_generates_csrf_state_and_cookie(sync_client: TestClient):
    """AUTH-01: Login initiates OAuth flow, creates cryptographic state and sets HttpOnly cookie."""
    resp = sync_client.get("/api/v1/auth/github/login", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers.get("location", "")
    assert location.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=" in location
    assert "state=" in location
    assert "scope=" in location

    # Extract state parameter from URL
    parsed = urllib.parse.urlparse(location)
    query_params = urllib.parse.parse_qs(parsed.query)
    raw_state = query_params["state"][0]

    # Verify cookie was set
    cookie_value = resp.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME)
    assert cookie_value is not None
    is_valid, _ = verify_oauth_state(raw_state, cookie_value)
    assert is_valid is True


def test_auth02_callback_missing_parameters_rejected(sync_client: TestClient):
    """AUTH-02: Callback without code or state returns 400 Bad Request."""
    # Missing both
    resp = sync_client.get("/api/v1/auth/github/callback")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_oauth_parameters"

    # Missing state
    resp = sync_client.get("/api/v1/auth/github/callback?code=foo")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_oauth_parameters"

    # Missing code
    resp = sync_client.get("/api/v1/auth/github/callback?state=bar")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_oauth_parameters"


def test_auth03_callback_mismatched_state_rejected(sync_client: TestClient):
    """AUTH-03: Callback with mismatched or forged state returns 400 invalid_state."""
    # 1. State provided but no state cookie present
    resp = sync_client.get("/api/v1/auth/github/callback?code=testcode&state=forged_state")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_state"

    # 2. State cookie present, but state query param doesn't match
    _, signed_cookie = create_oauth_state_token()
    sync_client.cookies.set(settings.GITHUB_OAUTH_STATE_COOKIE_NAME, signed_cookie)
    resp = sync_client.get("/api/v1/auth/github/callback?code=testcode&state=wrong_state")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_state"


def test_auth04_callback_error_query_param_handled(sync_client: TestClient):
    """AUTH-04: User cancelling OAuth authorization returns 400 with oauth_error."""
    resp = sync_client.get(
        "/api/v1/auth/github/callback?error=access_denied&error_description=The+user+has+denied+your+application"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "oauth_error"
    assert "denied" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_auth05_callback_successful_exchange_creates_user_and_sets_session(
    sync_client: TestClient,
    db: Session,
):
    """AUTH-05: Valid code and state exchange creates user, issues session cookie, and redirects."""
    raw_state, signed_state = create_oauth_state_token()
    sync_client.cookies.set(settings.GITHUB_OAUTH_STATE_COOKIE_NAME, signed_state)

    mock_profile = {
        "id": 999111,
        "login": "octocat-engineer",
        "name": "Octo Cat",
    }

    with (
        patch("app.api.v1.endpoints.auth.exchange_code_for_token", new_callable=AsyncMock) as mock_exchange,
        patch("app.api.v1.endpoints.auth.fetch_github_user_profile", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_exchange.return_value = "ghu_mocked_token_xyz"
        mock_fetch.return_value = mock_profile

        resp = sync_client.get(
            f"/api/v1/auth/github/callback?code=good_code&state={raw_state}",
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers.get("location") == f"{settings.FRONTEND_BASE_URL}/"

    # Session cookie must be present
    session_cookie = resp.cookies.get(settings.SESSION_COOKIE_NAME)
    assert session_cookie is not None

    # State cookie must be cleared
    assert resp.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME) is None

    # User must be persisted in database
    user = db.query(User).filter(User.github_user_id == 999111).first()
    assert user is not None
    assert user.login == "octocat-engineer"
    assert user.display_name == "Octo Cat"

    # Session token must decrypt to this user's ID
    payload = verify_session_token(session_cookie)
    assert payload is not None
    assert payload["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_auth06_callback_updates_existing_user(
    sync_client: TestClient,
    db: Session,
):
    """AUTH-06: Existing user logging in again updates their login/display_name without duplicating rows."""
    existing_user = User(
        github_user_id=888222,
        login="old-handle",
        display_name="Old Name",
    )
    db.add(existing_user)
    db.commit()
    user_id = existing_user.id

    raw_state, signed_state = create_oauth_state_token()
    sync_client.cookies.set(settings.GITHUB_OAUTH_STATE_COOKIE_NAME, signed_state)

    mock_profile = {
        "id": 888222,
        "login": "new-handle",
        "name": "New Name",
    }

    with (
        patch("app.api.v1.endpoints.auth.exchange_code_for_token", new_callable=AsyncMock) as mock_exchange,
        patch("app.api.v1.endpoints.auth.fetch_github_user_profile", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_exchange.return_value = "ghu_mocked_token"
        mock_fetch.return_value = mock_profile

        resp = sync_client.get(
            f"/api/v1/auth/github/callback?code=good_code&state={raw_state}",
            follow_redirects=False,
        )

    assert resp.status_code == 303
    db.expire_all()

    # Must update existing row, not create a new one
    users = db.query(User).filter(User.github_user_id == 888222).all()
    assert len(users) == 1
    assert users[0].id == user_id
    assert users[0].login == "new-handle"
    assert users[0].display_name == "New Name"


def test_auth07_session_cookie_authenticates_protected_endpoints(
    sync_client: TestClient,
    db: Session,
):
    """AUTH-07: Valid session cookie authenticates requests to /auth/me and project endpoints."""
    user = User(
        github_user_id=777333,
        login="cookie-auth-user",
        display_name="Cookie User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    project = Project(
        owner_user_id=user.id,
        github_installation_id=1234,
        repository_id=5678,
        repository_full_name="org/cookie-repo",
    )
    db.add(project)
    db.commit()

    session_token = create_session_token(str(user.id))
    sync_client.cookies.set(settings.SESSION_COOKIE_NAME, session_token)

    # 1. /api/v1/auth/me returns current user identity
    me_resp = sync_client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["user"]["login"] == "cookie-auth-user"
    assert me_data["authorized_project_count"] == 1

    # 2. Object authorization on projects
    proj_resp = sync_client.get(f"/api/v1/projects/{project.id}")
    assert proj_resp.status_code == 200
    assert proj_resp.json()["id"] == project.id


def test_auth08_tampered_or_expired_session_cookie_rejected(
    sync_client: TestClient,
    db: Session,
    monkeypatch,
):
    """AUTH-08: Tampered or expired session cookies are rejected."""
    user = User(
        github_user_id=666444,
        login="tamper-target",
    )
    db.add(user)
    db.commit()

    # 1. Tampered token
    sync_client.cookies.set(settings.SESSION_COOKIE_NAME, "tampered-invalid-cookie-token")

    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")
    resp = sync_client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"

    # 2. Expired token (create with negative or 0 max_age)
    valid_token = create_session_token(str(user.id))
    assert verify_session_token(valid_token, max_age=-1) is None


def test_auth09_logout_revokes_session_cookie(sync_client: TestClient):
    """AUTH-09: POST /api/v1/auth/logout instructs browser to delete cookies."""
    sync_client.cookies.set(settings.SESSION_COOKIE_NAME, "active_session_val")
    sync_client.cookies.set(settings.GITHUB_OAUTH_STATE_COOKIE_NAME, "active_state_val")

    resp = sync_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_out"

    # Cookie headers should delete or expire them
    assert resp.cookies.get(settings.SESSION_COOKIE_NAME) is None
    assert resp.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME) is None


def test_auth10_unauthenticated_production_returns_401(
    sync_client: TestClient,
    monkeypatch,
):
    """AUTH-10: In production mode, unauthenticated access strictly returns 401 Unauthorized."""
    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")

    resp = sync_client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert "Authentication required" in resp.json()["error"]["message"]


def test_auth11_production_ignores_test_header(
    sync_client: TestClient,
    monkeypatch,
    db: Session,
):
    """AUTH-11: In production mode, X-Test-User-ID header is ignored and unauthenticated requests still 401."""
    user = User(
        github_user_id=555111,
        login="test-bypass-user",
    )
    db.add(user)
    db.commit()

    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")

    resp = sync_client.get("/api/v1/auth/me", headers={"X-Test-User-ID": str(user.id)})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_auth12_generate_github_app_jwt(rsa_key_pair):
    """AUTH-12: generate_github_app_jwt produces an RS256 JWT verified by RSA public key."""
    private_pem, public_key = rsa_key_pair

    app_id = "123456"
    jwt_token = generate_github_app_jwt(app_id=app_id, private_key_pem=private_pem)

    parts = jwt_token.split(".")
    assert len(parts) == 3

    # Decode header
    header_raw = base64.urlsafe_b64decode(parts[0] + "==")
    header = json.loads(header_raw.decode("utf-8"))
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"

    # Decode claims
    claims_raw = base64.urlsafe_b64decode(parts[1] + "==")
    claims = json.loads(claims_raw.decode("utf-8"))
    assert claims["iss"] == app_id
    now = int(time.time())
    assert claims["iat"] <= now
    assert claims["exp"] > now

    # Verify cryptographic signature using public key
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    sig_bytes = base64.urlsafe_b64decode(parts[2] + "==")
    public_key.verify(sig_bytes, signing_input, padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.asyncio
async def test_auth13_get_installation_access_token():
    """AUTH-13: get_installation_access_token calls GitHub API and parses the returned token."""
    mock_resp = {"token": "ghs_installation_token_abc123", "expires_at": "2026-10-29T21:00:00Z"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = AsyncMock()
        mock_response.status_code = 201
        # In httpx, json() and raise_for_status() are synchronous methods
        mock_response.json = lambda: mock_resp
        mock_response.raise_for_status = lambda: None
        mock_post.return_value = mock_response

        token = await get_installation_access_token(
            installation_id=45678,
            jwt_token="mock_jwt_token",
        )

        assert token == "ghs_installation_token_abc123"
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "45678/access_tokens" in call_url


@pytest.mark.asyncio
async def test_auth14_open_redirect_prevention(sync_client: TestClient):
    """AUTH-14: Open redirect attempts via return_to are rejected and fallback to /."""
    # 1. Unit assertions on is_safe_relative_url
    assert is_safe_relative_url("/dashboard") is True
    assert is_safe_relative_url("/projects/123?tab=scan#section") is True
    assert is_safe_relative_url("https://evil.com") is False
    assert is_safe_relative_url("//evil.com") is False
    assert is_safe_relative_url("/\\evil.com") is False
    assert is_safe_relative_url("javascript:alert(1)") is False
    assert is_safe_relative_url(None) is False
    assert is_safe_relative_url("") is False

    # 2. Malicious return_to in login URL is neutralized
    login_resp = sync_client.get(
        "/api/v1/auth/github/login?return_to=https://evil.com/phish",
        follow_redirects=False,
    )
    assert login_resp.status_code == 307
    cookie_value = login_resp.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME)
    assert cookie_value is not None

    parsed = urllib.parse.urlparse(login_resp.headers["location"])
    raw_state = urllib.parse.parse_qs(parsed.query)["state"][0]

    # Verify that the encrypted state does NOT retain the external redirect
    is_valid, safe_return = verify_oauth_state(raw_state, cookie_value)
    assert is_valid is True
    assert safe_return is None

    # Complete the callback and ensure we redirect to FRONTEND_BASE_URL/ and NOT evil.com
    mock_profile = {"id": 112233, "login": "safe-user", "name": "Safe User"}
    with (
        patch("app.api.v1.endpoints.auth.exchange_code_for_token", new_callable=AsyncMock) as mock_exchange,
        patch("app.api.v1.endpoints.auth.fetch_github_user_profile", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_exchange.return_value = "ghu_token"
        mock_fetch.return_value = mock_profile

        cb_resp = sync_client.get(
            f"/api/v1/auth/github/callback?code=safe_code&state={raw_state}",
            follow_redirects=False,
        )
    assert cb_resp.status_code == 303
    assert cb_resp.headers["location"] == f"{settings.FRONTEND_BASE_URL}/"

    # 3. Legitimate return_to path is safely preserved
    login_valid = sync_client.get(
        "/api/v1/auth/github/login?return_to=/projects/999",
        follow_redirects=False,
    )
    valid_cookie = login_valid.cookies.get(settings.GITHUB_OAUTH_STATE_COOKIE_NAME)
    assert valid_cookie is not None
    valid_state = urllib.parse.parse_qs(urllib.parse.urlparse(login_valid.headers["location"]).query)["state"][0]

    with (
        patch("app.api.v1.endpoints.auth.exchange_code_for_token", new_callable=AsyncMock) as mock_exchange,
        patch("app.api.v1.endpoints.auth.fetch_github_user_profile", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_exchange.return_value = "ghu_token"
        mock_fetch.return_value = mock_profile

        cb_valid = sync_client.get(
            f"/api/v1/auth/github/callback?code=safe_code2&state={valid_state}",
            follow_redirects=False,
        )
    assert cb_valid.status_code == 303
    assert cb_valid.headers["location"] == f"{settings.FRONTEND_BASE_URL}/projects/999"


def test_auth15_server_side_session_revocation(
    sync_client: TestClient,
    db: Session,
    monkeypatch,
):
    """AUTH-15: POST /api/v1/auth/logout revokes session on server; replaying cookie returns 401."""
    user = User(
        github_user_id=444333,
        login="revocation-test-user",
        display_name="Revoke User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session_token = create_session_token(str(user.id))
    sync_client.cookies.set(settings.SESSION_COOKIE_NAME, session_token)

    # 1. Authenticated endpoint succeeds before logout
    resp_before = sync_client.get("/api/v1/auth/me")
    assert resp_before.status_code == 200
    assert resp_before.json()["user"]["login"] == "revocation-test-user"

    # 2. Call logout endpoint
    logout_resp = sync_client.post("/api/v1/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["status"] == "logged_out"

    # Audit event should be recorded
    audit = db.query(AuditEvent).filter(AuditEvent.action == "auth.logout").first()
    assert audit is not None
    assert audit.target_id == str(user.id)
    assert audit.actor == f"user:{user.id}"

    # 3. Attacker replays the original session_token cookie in production mode
    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")
    sync_client.cookies.set(settings.SESSION_COOKIE_NAME, session_token)

    replay_resp = sync_client.get("/api/v1/auth/me")
    assert replay_resp.status_code == 401
    assert replay_resp.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_auth16_database_error_on_callback_rolls_back_cleanly(
    sync_client: TestClient,
    db: Session,
):
    """AUTH-16: Database failure during user persistence rolls back cleanly and returns 500."""
    raw_state, signed_state = create_oauth_state_token()
    sync_client.cookies.set(settings.GITHUB_OAUTH_STATE_COOKIE_NAME, signed_state)

    mock_profile = {"id": 121212, "login": "db-fail-user", "name": "DB Fail"}

    with (
        patch("app.api.v1.endpoints.auth.exchange_code_for_token", new_callable=AsyncMock) as mock_exchange,
        patch("app.api.v1.endpoints.auth.fetch_github_user_profile", new_callable=AsyncMock) as mock_fetch,
        patch.object(db, "commit", side_effect=SQLAlchemyError("Simulated DB connection failure")),
    ):
        mock_exchange.return_value = "ghu_token"
        mock_fetch.return_value = mock_profile

        resp = sync_client.get(
            f"/api/v1/auth/github/callback?code=fail_code&state={raw_state}",
            follow_redirects=False,
        )

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "database_error"
    assert "Failed to persist user profile" in resp.json()["error"]["message"]
