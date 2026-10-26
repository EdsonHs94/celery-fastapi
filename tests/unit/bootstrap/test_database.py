"""Ciclo de vida sin abrir sockets: sesiones reales instrumentadas al cerrarse."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from celery import signals
from sqlmodel import Session

from app.bootstrap.config import Settings


def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://reports:local@unreachable.invalid/reports",
        broker_url="amqp://reports:local@unreachable.invalid/reports",
    )


@pytest.fixture
def database_module():
    from app.bootstrap import database

    return database


@pytest.mark.parametrize("fail", [False, True])
def test_session_scope_closes_on_success_and_error(database_module, monkeypatch, fail):
    closed = []

    class TrackingSession(Session):
        def close(self):
            closed.append(self)
            super().close()

    monkeypatch.setattr(database_module, "Session", TrackingSession)
    database = database_module.Database(str(settings().database_url))
    try:
        with database.session() as session:
            assert database.engine is database.engine
            if fail:
                raise ValueError("failure")
    except ValueError:
        assert fail
    assert closed == [session]
    database.dispose()


def test_fork_discards_inherited_pool_and_uses_new_engine(database_module, monkeypatch):
    database = database_module.Database(str(settings().database_url))
    parent_engine = database.engine
    parent_pool = parent_engine.pool
    # Simulate a child PID without opening a connection in the parent.
    monkeypatch.setattr(database_module.os, "getpid", lambda: 987654)
    child_engine = database.engine
    assert child_engine is not parent_engine
    assert parent_engine.pool is not parent_pool
    assert child_engine is database.engine
    database.dispose()


@pytest.mark.parametrize("fail", [False, True])
def test_api_lifespan_disposes_engine_and_closes_dependency_session(
    database_module, monkeypatch, fail
):
    from app.bootstrap.api import create_app
    from app.bootstrap.database import get_report_repository

    closed = []
    disposed = []

    class TrackingSession(Session):
        def close(self):
            closed.append(self)
            super().close()

    monkeypatch.setattr(database_module, "Session", TrackingSession)
    application = create_app(settings())
    database = application.state.database
    engine = database.engine
    from sqlalchemy import event

    event.listen(engine, "engine_disposed", lambda _: disposed.append(True))

    async def request_scope():
        async with application.router.lifespan_context(application):
            dependency = get_report_repository(SimpleNamespace(app=application))
            next(dependency)
            if fail:
                with pytest.raises(ValueError):
                    dependency.throw(ValueError("request failed"))
            else:
                with pytest.raises(StopIteration):
                    next(dependency)
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                assert (await client.get("/health")).json() == {"status": "ok"}
                assert (await client.get("/docs")).status_code == 200

    asyncio.run(request_scope())
    assert len(closed) == 1
    assert disposed == [True]


def test_worker_creates_no_engine_in_parent_and_resets_after_fork(
    database_module, monkeypatch
):
    from app.bootstrap.worker import create_celery_app

    created = []
    original = database_module.create_engine

    def track_engine(*args, **kwargs):
        engine = original(*args, **kwargs)
        created.append(engine)
        return engine

    monkeypatch.setattr(database_module, "create_engine", track_engine)
    with create_celery_app(settings()) as worker:
        assert created == []
        database = worker.database
        # A process-init signal must discard even an accidentally created pool.
        parent_engine = database.engine
        signals.worker_process_init.send(sender=None)
        assert database.engine is not parent_engine
        engine = database.engine
        disposed = []
        from sqlalchemy import event

        event.listen(engine, "engine_disposed", lambda _: disposed.append(True))
        signals.worker_process_shutdown.send(sender=None)
        assert disposed == [True]
        assert created == [parent_engine, engine]
