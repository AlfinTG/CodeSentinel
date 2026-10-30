"""SQLAlchemy database engine and session management."""

import contextlib
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


def _create_app_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    return create_engine(
        database_url if not is_sqlite else "sqlite:///./codesentinel_dev.db",
        connect_args=connect_args,
        pool_pre_ping=True,
    )


# Engine configuration with connection pooling
engine = _create_app_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db(database_url: str | None = None) -> None:
    """Reconfigure database engine and SessionLocal with new database URL.

    Invoked during startup secret resolution when DATABASE_URL is resolved from KMS.
    """
    global engine
    url = database_url or settings.DATABASE_URL
    new_engine = _create_app_engine(url)
    if engine is not None:
        with contextlib.suppress(Exception):
            engine.dispose()
    engine = new_engine
    SessionLocal.configure(bind=engine)


def get_db() -> Generator:
    """FastAPI dependency for database session lifecycle."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
