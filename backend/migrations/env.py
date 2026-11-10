"""Alembic environment. Migrations are plain SQL; there is no ORM metadata to compare."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from txlens_backend.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def database_url() -> str:
    """URL set by the caller (tests), else the owner DATABASE_URL from the environment."""
    return (
        config.get_main_option("sqlalchemy.url") or get_settings().database_url.get_secret_value()
    )


def run_migrations() -> None:
    """Run migrations online, each in its own transaction."""
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not supported; run against a database.")
run_migrations()
