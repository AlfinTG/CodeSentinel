"""Pytest configuration and shared test fixtures with isolated test database."""

from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.config import settings
from app.main import app
from app.models.base import Base
from app.models.user import User
from app.services.runner import is_container_runtime_available

# In-memory SQLite using StaticPool ensures a single shared in-memory database across threads
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db() -> Generator[Session, None, None]:
    """Provide a fresh database schema for each test."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
async def client(db: Session):
    """Async HTTP test client with database dependency overridden."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def async_client(client: AsyncClient):
    """Alias for client fixture for phase 0 compatibility."""
    return client


@pytest.fixture
def user_alice(db: Session) -> User:
    """Create test user Alice."""
    alice = User(
        github_user_id=101,
        login="alice",
        display_name="Alice Engineer",
    )
    db.add(alice)
    db.commit()
    db.refresh(alice)
    return alice


@pytest.fixture
def user_bob(db: Session) -> User:
    """Create test user Bob for cross-user authorization tests."""
    bob = User(
        github_user_id=202,
        login="bob",
        display_name="Bob Unauthorized",
    )
    db.add(bob)
    db.commit()
    db.refresh(bob)
    return bob


def apply_default_test_runner_runtime(monkeypatch):
    """Helper to configure runner runtime for tests without leaking into production."""
    if settings.CODESENTINEL_ENV == "production":
        return
    if settings.RUNNER_CONTAINER_RUNTIME == "docker" and not is_container_runtime_available("docker"):
        monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "subprocess")


@pytest.fixture(autouse=True)
def default_test_runner_runtime(monkeypatch):
    """Ensure baseline unit and integration tests run without an external Docker daemon.
    When Docker is unavailable on the host, default to 'subprocess' for general test fixtures,
    while container isolation tests explicitly test container runtime and fail-closed enforcement.
    Strictly forbidden from overriding runtime in production environments.
    """
    apply_default_test_runner_runtime(monkeypatch)
