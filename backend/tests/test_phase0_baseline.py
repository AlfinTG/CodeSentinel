"""Phase 0 Gate Tests: Baseline verification, health probes, and security headers."""

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    """Test API root returns application identity."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "CodeSentinel API"
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_health_live_probe(async_client: AsyncClient):
    """Test /api/v1/health/live probe conforms to API_CONTRACTS.md."""
    response = await async_client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_probe(async_client: AsyncClient):
    """Test /api/v1/health/ready probe returns 200 and hides connection credentials."""
    response = await async_client.get("/api/v1/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    # Ensure database credentials or host strings are NEVER leaked in ready response
    assert "@" not in str(data)
    assert "password" not in str(data)


@pytest.mark.asyncio
async def test_security_headers_and_request_id(async_client: AsyncClient):
    """Test security headers and X-Request-ID propagation."""
    response = await async_client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_no_hardcoded_live_secrets_in_settings():
    """Verify that settings default configuration uses safe development placeholders."""
    # Ensure no real api keys exist in default settings
    assert (
        settings.NEBIUS_TOKEN_FACTORY_API_KEY == ""
        or "placeholder" in settings.NEBIUS_TOKEN_FACTORY_API_KEY
    )
    assert (
        settings.GITHUB_APP_CLIENT_SECRET == ""
        or "secret" in settings.GITHUB_APP_CLIENT_SECRET.lower()
    )
