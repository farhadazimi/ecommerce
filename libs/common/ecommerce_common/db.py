"""SQLAlchemy engine / session management with RDS-friendly connection pooling."""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings


def build_engine(settings: Settings) -> Engine:
    url = settings.sqlalchemy_url
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - sqlite only
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine

    connect_args: dict = {
        "connect_timeout": settings.database_connect_timeout,
        "read_timeout": 30,
        "write_timeout": 30,
        "charset": "utf8mb4",
    }
    if settings.database_ssl_ca:
        ctx = ssl.create_default_context(cafile=settings.database_ssl_ca)
        connect_args["ssl"] = ctx
    return create_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=settings.database_pool_recycle,  # below RDS wait_timeout
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,  # transparently replace connections dropped by RDS failover
        isolation_level="READ COMMITTED",
        connect_args=connect_args,
    )


class Database:
    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        self.engine = engine or build_engine(settings)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)

    def session(self) -> Session:
        return self.session_factory()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self.session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
