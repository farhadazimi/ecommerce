"""Migrations build a fresh database from scratch and match the ORM models exactly.

Runs on a fresh SQLite file by default; set TEST_MIGRATION_DATABASE_URL to an *empty*
MySQL database to exercise the RDS dialect (done in CI).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test", "PYTHONPATH": str(ROOT / "libs/common")}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT / "database", env=env, capture_output=True, text=True,
        timeout=120,
    )


@pytest.fixture
def fresh_url(tmp_path) -> str:
    url = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    if url:
        engine = create_engine(url)
        with engine.begin() as conn:  # start from an empty schema
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for (table,) in conn.execute(text("SHOW TABLES")).all():
                conn.execute(text(f"DROP TABLE `{table}`"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        engine.dispose()
        return url
    return f"sqlite:///{tmp_path}/migrations.db"


def test_upgrade_check_downgrade_roundtrip(fresh_url):
    up = _alembic(fresh_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = create_engine(fresh_url)
    with engine.connect() as conn:
        roles = {r for (r,) in conn.execute(text("SELECT name FROM roles"))}
        assert roles == {"ADMIN", "CUSTOMER"}  # reference data committed (regression: advisory-lock txn)
    engine.dispose()

    check = _alembic(fresh_url, "check")
    assert check.returncode == 0, check.stdout + check.stderr  # models == migrations

    down = _alembic(fresh_url, "downgrade", "base")
    assert down.returncode == 0, down.stderr
    again = _alembic(fresh_url, "upgrade", "head")
    assert again.returncode == 0, again.stderr


def test_seed_is_idempotent(fresh_url, tmp_path):
    assert _alembic(fresh_url, "upgrade", "head").returncode == 0
    env = {**os.environ, "DATABASE_URL": fresh_url, "APP_ENV": "development", "LOCAL_STORAGE_PATH": str(tmp_path / "s"),
           "PYTHONPATH": str(ROOT / "libs/common"), "REDIS_HOST": ""}
    for expected in ("'products': 17", "'products': 0"):
        run = subprocess.run([sys.executable, "database/seed/seed.py"], cwd=ROOT, env=env, capture_output=True, text=True,
                             timeout=120)
        assert run.returncode == 0, run.stderr
        assert expected in run.stdout
