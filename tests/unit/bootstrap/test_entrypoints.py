import asyncio

import httpx

from app.bootstrap.config import Settings


def test_healthcheck_does_not_need_a_database_or_broker():
    from app.bootstrap.api import create_app

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://reports:local@unreachable.invalid/reports",
        broker_url="amqp://reports:local@unreachable.invalid/reports",
    )

    async def request_health():
        transport = httpx.ASGITransport(app=create_app(settings))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_worker_uses_the_supplied_broker_without_a_result_backend():
    from app.bootstrap.worker import create_celery_app

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://reports:local@postgres/reports",
        broker_url="amqp://reports:local@custom-broker:5673/custom-vhost",
    )

    with create_celery_app(settings) as worker:
        connection = worker.connection_for_write()
        assert connection.hostname == "custom-broker"
        assert connection.port == 5673
        assert connection.virtual_host == "custom-vhost"
        assert worker.backend.__class__.__name__ == "DisabledBackend"
        assert worker.conf.task_ignore_result is True
