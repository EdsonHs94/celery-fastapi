from alembic import command
from sqlalchemy import DateTime, Integer, String, Uuid, inspect


def test_initial_migration_columns_and_reversible_downgrade(engine, migration_config):
    columns = {
        column["name"]: column for column in inspect(engine).get_columns("reports")
    }
    assert set(columns) == {
        "id",
        "status",
        "attempts",
        "created_at",
        "started_at",
        "finished_at",
        "file_ref",
        "error_code",
    }
    for name in ("id", "status", "attempts", "created_at"):
        assert columns[name]["nullable"] is False
    for name in ("started_at", "finished_at", "file_ref", "error_code"):
        assert columns[name]["nullable"] is True
    assert inspect(engine).get_pk_constraint("reports")["constrained_columns"] == ["id"]
    assert isinstance(columns["id"]["type"], Uuid)
    assert isinstance(columns["attempts"]["type"], Integer)
    for name in ("status", "file_ref", "error_code"):
        assert isinstance(columns[name]["type"], String)
    for name in ("created_at", "started_at", "finished_at"):
        assert isinstance(columns[name]["type"], DateTime)
        assert columns[name]["type"].timezone is True
    command.downgrade(migration_config, "base")
    assert "reports" not in inspect(engine).get_table_names()
    command.upgrade(migration_config, "head")
    command.check(migration_config)


def test_api_startup_and_health_do_not_create_tables(engine, migration_config):
    import asyncio

    import httpx

    from app.bootstrap.api import create_app
    from app.bootstrap.config import Settings

    command.downgrade(migration_config, "base")
    application = create_app(
        Settings(
            _env_file=None,
            database_url=engine.url.render_as_string(hide_password=False),
            broker_url="amqp://reports:local@unreachable.invalid/reports",
        )
    )

    async def start_and_request():
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                assert (await client.get("/health")).status_code == 200
                assert (await client.get("/docs")).status_code == 200

    asyncio.run(start_and_request())
    assert "reports" not in inspect(engine).get_table_names()


def test_url_encoded_password_does_not_use_ini_interpolation(
    migration_config, monkeypatch
):
    from io import StringIO

    output = StringIO()
    migration_config.output_buffer = output
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:p%25ss%40word@localhost/unit_test"
    )
    command.upgrade(migration_config, "head", sql=True)
    assert "CREATE TABLE reports" in output.getvalue()
    assert "p%25ss" not in output.getvalue()
