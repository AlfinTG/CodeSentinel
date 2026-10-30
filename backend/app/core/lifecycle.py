"""Application lifecycle initialization and secret resolution coordination."""

import logging

from app.core.config import Settings, settings
from app.core.secrets import SecretProvider
from app.db.session import init_db

logger = logging.getLogger(__name__)


def resolve_secrets_and_validate(
    settings_obj: Settings | None = None,
    provider: SecretProvider | None = None,
) -> Settings:
    """Resolve secrets dynamically and validate production constraints without re-binding the application engine.

    Used by database migrations (Alembic) and CLI utilities that only require configuration resolution.
    """
    target_settings = settings_obj or settings

    # Resolve secrets dynamically
    logger.info(
        "Resolving application secrets via provider: %s",
        target_settings.SECRET_PROVIDER,
    )
    target_settings.initialize_secrets(provider=provider)

    # If production mode, strictly enforce security rules on the resolved settings
    if target_settings.CODESENTINEL_ENV == "production":
        target_settings.validate_production_security()

    return target_settings


def initialize_application(
    settings_obj: Settings | None = None,
    provider: SecretProvider | None = None,
) -> Settings:
    """Coordinate startup secret resolution, security validation, and database setup.

    Order of operations:
      1. Resolves managed secrets from the configured KMS provider into settings in-memory.
      2. Validates production security constraints on the resolved settings.
      3. Reconfigures the database engine and SessionLocal with the resolved DATABASE_URL.
      4. Returns the validated Settings instance.

    Fails closed if the secret provider raises an error or production secrets are invalid.
    """
    target_settings = resolve_secrets_and_validate(settings_obj=settings_obj, provider=provider)

    # Reconfigure database engine with resolved DATABASE_URL
    init_db(target_settings.DATABASE_URL)

    logger.info("Application lifecycle initialization completed successfully.")
    return target_settings
