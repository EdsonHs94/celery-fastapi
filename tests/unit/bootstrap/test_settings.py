from pathlib import Path

import pytest
from pydantic import ValidationError


def settings_class():
    from app.bootstrap.config import Settings

    return Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in (
        "DATABASE_URL",
        "BROKER_URL",
        "REPORTS_DIR",
        "SIMULATE_TRANSIENT_FAILURE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_loads_connections_and_options_from_dotenv(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql+psycopg://reports:local@postgres:5432/reports\n"
        "BROKER_URL=amqp://reports:local@rabbitmq:5672/reports\n"
        f"REPORTS_DIR={tmp_path / 'exports'}\n"
        "SIMULATE_TRANSIENT_FAILURE=false\n"
        "POSTGRES_USER=reports\n",
        encoding="utf-8",
    )

    settings = settings_class()(_env_file=env)

    assert str(settings.database_url) == (
        "postgresql+psycopg://reports:local@postgres:5432/reports"
    )
    assert str(settings.broker_url) == "amqp://reports:local@rabbitmq:5672/reports"
    assert settings.reports_dir == tmp_path / "exports"
    assert settings.simulate_transient_failure is False
    assert not settings.reports_dir.exists()


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql+psycopg://reports:local@postgres:5432/reports\n"
        "BROKER_URL=amqp://reports:local@rabbitmq:5672/reports\n"
        "SIMULATE_TRANSIENT_FAILURE=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SIMULATE_TRANSIENT_FAILURE", "false")

    assert settings_class()(_env_file=env).simulate_transient_failure is False


def test_demo_defaults_do_not_supply_connection_credentials():
    with pytest.raises(ValidationError) as error:
        settings_class()(_env_file=None)

    assert {item["loc"] for item in error.value.errors()} == {
        ("database_url",),
        ("broker_url",),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///local.db"),
        ("database_url", "postgresql://reports:local@postgres/reports"),
        ("database_url", "postgresql+psycopg://reports:local@postgres"),
        ("broker_url", "redis://redis:6379/0"),
        ("broker_url", "amqp://"),
        ("reports_dir", "relative/reports"),
        ("simulate_transient_failure", "sometimes"),
    ],
)
def test_rejects_invalid_runtime_configuration(field, value):
    values = {
        "database_url": "postgresql+psycopg://reports:local@postgres:5432/reports",
        "broker_url": "amqp://reports:local@rabbitmq:5672/reports",
        field: value,
    }

    with pytest.raises(ValidationError):
        settings_class()(_env_file=None, **values)


def test_secrets_are_not_in_settings_repr():
    settings = settings_class()(
        _env_file=None,
        database_url="postgresql+psycopg://reports:db-secret@postgres/reports",
        broker_url="amqp://reports:broker-secret@rabbitmq/reports",
    )

    assert "db-secret" not in repr(settings)
    assert "broker-secret" not in repr(settings)
    assert settings.reports_dir == Path("/app/reports")
    assert settings.simulate_transient_failure is True
