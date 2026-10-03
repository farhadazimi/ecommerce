"""Alembic environment: reads connection settings from the same env vars as the services."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from ecommerce_common.config import get_settings
from ecommerce_common.db import build_engine
from ecommerce_common.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


MIGRATION_LOCK = "ecommerce_schema_migrations"


def run_migrations_online() -> None:
    engine = build_engine(get_settings())
    with engine.connect() as connection:
        is_mysql = connection.dialect.name == "mysql"
        if is_mysql:
            # serialise concurrent runs (e.g. two deploy pipelines) with a MySQL advisory lock
            got = connection.execute(text("SELECT GET_LOCK(:n, 300)"), {"n": MIGRATION_LOCK}).scalar()
            if got != 1:
                raise RuntimeError("could not acquire the schema migration lock within 300s")
            # the lock is session-scoped; end the implicit transaction so Alembic manages its own
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if is_mysql:
                connection.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": MIGRATION_LOCK})
                connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
