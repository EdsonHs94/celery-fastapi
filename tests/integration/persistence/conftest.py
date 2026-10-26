"""Solo una base explícita y desechable; nunca DATABASE_URL como fallback."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlmodel import create_engine

ROOT = Path(__file__).resolve().parents[3]


def test_database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("PostgreSQL requiere TEST_DATABASE_URL explícito")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg" or not (
        url.database and url.database.endswith("_test")
    ):
        raise pytest.UsageError(
            "La base de pruebas debe ser PostgreSQL y terminar en _test"
        )
    return value


@pytest.fixture
def migration_config(monkeypatch):
    value = test_database_url()
    monkeypatch.setenv("DATABASE_URL", value)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


@pytest.fixture
def engine(migration_config):
    engine = create_engine(test_database_url())
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(migration_config, "base")
