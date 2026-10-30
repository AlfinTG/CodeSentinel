import contextlib
import logging
import sys
from logging.config import fileConfig
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import engine_from_config, inspect, pool

from alembic import context

_logger = logging.getLogger("alembic.env")

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set model metadata
target_metadata = Base.metadata

# Override sqlalchemy.url with dynamic application settings
current_url = config.get_main_option("sqlalchemy.url")
if not current_url or current_url.startswith("driver://"):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_num_length=64,
    )

    with context.begin_transaction():
        context.run_migrations()


def _ensure_version_num_length(connection: sa.Connection) -> None:
    """Widen alembic_version.version_num to VARCHAR(64) if the existing column is shorter.

    This is a one-time self-healing migration for databases created with older Alembic
    defaults (VARCHAR(32)) that now need longer revision identifiers. The operation is
    idempotent, non-destructive, and only modifies the Alembic metadata table — never
    application schema.

    Required database permissions: ALTER TABLE on ``alembic_version``.
    """
    dialect_name = connection.dialect.name
    if dialect_name not in ("postgresql", "mysql"):
        return

    try:
        inspector = inspect(connection)
        if "alembic_version" not in inspector.get_table_names():
            return

        cols = {c["name"]: c for c in inspector.get_columns("alembic_version")}
        if "version_num" not in cols:
            return

        col_type = cols["version_num"]["type"]
        length = getattr(col_type, "length", None)
        if length is None or length >= 64:
            return

        _logger.info(
            "Widening alembic_version.version_num from VARCHAR(%d) to VARCHAR(64)",
            length,
        )

        if dialect_name == "postgresql":
            connection.execute(
                sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
            )
        else:
            connection.execute(
                sa.text("ALTER TABLE alembic_version MODIFY COLUMN version_num VARCHAR(64)")
            )
        connection.commit()
        _logger.info("alembic_version.version_num widened successfully.")
    except sa.exc.OperationalError as exc:
        _logger.warning(
            "Failed to widen alembic_version.version_num (likely insufficient privileges): %s. "
            "Migrations will continue; if a revision id exceeds the current column width, "
            "Alembic will raise an error.",
            exc,
        )
        with contextlib.suppress(Exception):
            connection.rollback()
    except Exception as exc:
        _logger.error(
            "Unexpected error inspecting/altering alembic_version.version_num: %s",
            exc,
        )
        with contextlib.suppress(Exception):
            connection.rollback()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    url = config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_version_num_length(connection)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_num_length=64,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
