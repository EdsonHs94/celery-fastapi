"""Contrato HTTP con casos de uso reales y adaptadores sustituidos."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.bootstrap.api import create_app
from app.bootstrap.config import Settings
from app.bootstrap.database import get_report_repository
from app.domain.report import Report, ReportStatus
from app.infrastructure.report_storage import LocalReportStorage
from tests.unit.application.fakes import MemoryRepository, Publisher


def make_app(tmp_path, reports=()):
    application = create_app(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://reports:local@unreachable.invalid/reports",
            broker_url="amqp://reports:local@unreachable.invalid/reports",
            reports_dir=tmp_path,
        )
    )
    repository = MemoryRepository(reports)
    publisher = Publisher(repository)
    application.dependency_overrides[get_report_repository] = lambda: repository
    from app.bootstrap.http_dependencies import get_report_publisher

    application.dependency_overrides[get_report_publisher] = lambda: publisher
    return application, repository, publisher


def request(application, method, url, **kwargs):
    async def run():
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://test"
            ) as client:
                return await client.request(method, url, **kwargs)

    return asyncio.run(run())


def report(status):
    item = Report(id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=UTC))
    if status == ReportStatus.PENDING:
        return item
    item = item.start_attempt(datetime(2026, 1, 2, tzinfo=UTC))
    if status == ReportStatus.RETRYING:
        return item.retry("temporary_failure")
    if status == ReportStatus.COMPLETED:
        return item.complete(f"report-{item.id}.csv", datetime(2026, 1, 3, tzinfo=UTC))
    if status == ReportStatus.FAILED:
        return item.fail("generation_failed", datetime(2026, 1, 3, tzinfo=UTC))
    return item


def test_acceptance_commits_then_publishes_without_promising_status(tmp_path):
    app, repo, publisher = make_app(tmp_path)
    response = request(app, "POST", "/reports", json={})
    assert response.status_code == 202
    identifier = UUID(response.json()["id"])
    assert response.json() == {
        "id": str(identifier),
        "status_url": f"/reports/{identifier}",
        "download_url": f"/reports/{identifier}/download",
    }
    assert repo.reports[identifier].status == ReportStatus.PENDING
    assert publisher.calls == [identifier]
    assert repo.events == ["begin", "add", "commit", "publish"]


@pytest.mark.parametrize(
    "body", [None, "null", "[]", '"text"', "1", "true", '{"extra":1}', "{"]
)
def test_invalid_body_has_no_persistence_or_publication(tmp_path, body):
    app, repo, publisher = make_app(tmp_path)
    response = request(
        app,
        "POST",
        "/reports",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert repo.events == []
    assert publisher.calls == []


@pytest.mark.parametrize("status", list(ReportStatus))
def test_status_reads_persisted_public_fields(tmp_path, status):
    item = report(status)
    app, _, _ = make_app(tmp_path, (item,))
    response = request(app, "GET", f"/reports/{item.id}")
    assert response.status_code == 200
    assert response.json() == {
        "id": str(item.id),
        "status": status.value,
        "attempts": item.attempts,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None if item.started_at is None else "2026-01-02T00:00:00Z",
        "finished_at": None if item.finished_at is None else "2026-01-03T00:00:00Z",
        "download_url": f"/reports/{item.id}/download"
        if status == ReportStatus.COMPLETED
        else None,
        "error_code": item.error_code,
    }


@pytest.mark.parametrize("suffix", ["", "/download"])
@pytest.mark.parametrize(
    "identifier,expected", [("bad-uuid", 422), (str(uuid4()), 404)]
)
def test_missing_and_invalid_identifiers(tmp_path, suffix, identifier, expected):
    app, _, _ = make_app(tmp_path)
    response = request(app, "GET", f"/reports/{identifier}{suffix}")
    assert response.status_code == expected
    if expected == 404:
        assert response.json()["detail"]["code"] == "report_not_found"


@pytest.mark.parametrize(
    "method,url",
    [("POST", "/reports"), ("GET", "/reports/{id}"), ("GET", "/reports/{id}/download")],
)
def test_persistence_failure_is_public_503(tmp_path, method, url):
    item = report(ReportStatus.COMPLETED)
    app, repo, publisher = make_app(tmp_path, (item,))
    repo.fail_add = repo.fail_get = True
    response = request(
        app,
        method,
        url.format(id=item.id),
        **({"json": {}} if method == "POST" else {}),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "persistence_unavailable",
        "message": "La persistencia no está disponible.",
    }
    assert publisher.calls == []


def test_failed_publication_returns_identifier_and_compensates(tmp_path):
    app, repo, publisher = make_app(tmp_path)
    publisher.error = OSError("private broker password")
    response = request(app, "POST", "/reports", json={})
    assert response.status_code == 503
    detail = response.json()["detail"]
    identifier = UUID(detail["report_id"])
    assert detail == {
        "code": "publication_failed",
        "message": "No se pudo publicar el encargo del reporte.",
        "report_id": str(identifier),
    }
    assert repo.reports[identifier].status == ReportStatus.FAILED
    assert repo.reports[identifier].error_code == "publication_failed"


@pytest.mark.parametrize(
    "status",
    [
        ReportStatus.PENDING,
        ReportStatus.PROCESSING,
        ReportStatus.RETRYING,
        ReportStatus.FAILED,
    ],
)
def test_download_requires_completed(tmp_path, status):
    item = report(status)
    app, _, _ = make_app(tmp_path, (item,))
    response = request(app, "GET", f"/reports/{item.id}/download")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "report_not_ready"


def test_download_streams_csv_with_attachment_headers(tmp_path):
    item = report(ReportStatus.COMPLETED)
    LocalReportStorage(tmp_path).write(item.id, b"sale_id,customer\n1,Cliente 001\n")
    app, _, _ = make_app(tmp_path, (item,))
    response = request(app, "GET", f"/reports/{item.id}/download")
    assert response.status_code == 200
    assert response.content == b"sale_id,customer\n1,Cliente 001\n"
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="report-{item.id}.csv"'
    )


def test_missing_completed_file_is_public_410(tmp_path):
    item = report(ReportStatus.COMPLETED)
    app, _, _ = make_app(tmp_path, (item,))
    response = request(app, "GET", f"/reports/{item.id}/download")
    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "report_file_missing",
        "message": "El archivo del reporte ya no está disponible.",
    }
    assert str(tmp_path) not in response.text


def test_openapi_exposes_transport_models_and_csv(tmp_path):
    app, _, _ = make_app(tmp_path)
    response = request(app, "GET", "/openapi.json")
    schema = response.json()
    models = schema["components"]["schemas"]
    assert {"ReportCreate", "ReportAccepted", "ReportResponse"} <= models.keys()
    assert "ReportTable" not in models
    assert "file_ref" not in response.text
    assert models["ReportCreate"]["additionalProperties"] is False
    assert (
        "text/csv"
        in schema["paths"]["/reports/{report_id}/download"]["get"]["responses"]["200"][
            "content"
        ]
    )


def test_transport_models_are_sqlmodel_data_models():
    from sqlmodel import SQLModel

    from app.presentation.report_schemas import (
        ReportAccepted,
        ReportCreate,
        ReportResponse,
    )

    for model in (ReportCreate, ReportAccepted, ReportResponse):
        assert issubclass(model, SQLModel)
        assert not model.model_config.get("table", False)
    assert ReportCreate().model_dump() == {}


@pytest.mark.parametrize(
    "body,expected", [("{}", 202), ('{"extra":1}', 422), ("null", 422)]
)
def test_real_request_scopes_close_distinct_sessions(
    tmp_path, monkeypatch, body, expected
):
    from sqlmodel import Session

    from app.bootstrap import database as database_module
    from app.bootstrap.http_dependencies import get_report_publisher

    closed = []
    sessions = []

    class TrackingSession(Session):
        def close(self):
            closed.append(self)
            super().close()

    app, repo, publisher = make_app(tmp_path)
    app.dependency_overrides.pop(get_report_repository)
    app.dependency_overrides[get_report_publisher] = lambda: publisher

    def repository_for_session(session):
        sessions.append(session)
        return repo

    monkeypatch.setattr(database_module, "Session", TrackingSession)
    monkeypatch.setattr(
        database_module, "SQLModelReportRepository", repository_for_session
    )

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for _ in range(2):
                    response = await client.post(
                        "/reports",
                        content=body,
                        headers={"Content-Type": "application/json"},
                    )
                    assert response.status_code == expected

    asyncio.run(run())
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert closed == sessions


def test_error_request_also_closes_session(tmp_path, monkeypatch):
    from sqlmodel import Session

    from app.bootstrap import database as database_module

    closed = []

    class TrackingSession(Session):
        def close(self):
            closed.append(self)
            super().close()

    app, repo, _ = make_app(tmp_path)
    repo.fail_add = True
    app.dependency_overrides.pop(get_report_repository)
    monkeypatch.setattr(database_module, "Session", TrackingSession)
    monkeypatch.setattr(
        database_module, "SQLModelReportRepository", lambda session: repo
    )
    response = request(app, "POST", "/reports", json={})
    assert response.status_code == 503
    assert len(closed) == 1


def test_application_resources_are_lazy_isolated_and_closed(tmp_path, monkeypatch):
    from celery import Celery

    from app.bootstrap.http_dependencies import get_report_publisher
    from app.infrastructure.celery_publisher import CeleryReportTaskPublisher

    apps = [make_app(tmp_path / name)[0] for name in ("one", "two")]
    closed = []
    disposed = []
    original_close = Celery.close

    def close(client):
        closed.append(client)
        original_close(client)

    monkeypatch.setattr(Celery, "close", close)
    monkeypatch.setattr(
        CeleryReportTaskPublisher, "publish", lambda self, report_id: None
    )
    clients = []

    async def run():
        for app in apps:
            app.dependency_overrides.pop(get_report_publisher)
            async with app.router.lifespan_context(app):
                assert app.state.database._engine is None
                assert app.state.celery is None
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    for path in ("/health", "/docs", "/openapi.json"):
                        assert (await client.get(path)).status_code == 200
                    assert app.state.celery is None
                    assert app.state.database._engine is None
                    assert (await client.post("/reports", json={})).status_code == 202
                    celery = app.state.celery
                    clients.append(celery)
                    assert celery.report_settings is app.state.settings
                    assert celery.database._engine is None
                    assert (
                        celery.report_settings.reports_dir
                        == app.state.settings.reports_dir
                    )
                    monkeypatch.setattr(
                        celery.database, "dispose", lambda: disposed.append(True)
                    )
            assert app.state.celery is None

    asyncio.run(run())
    assert clients[0] is not clients[1]
    assert closed == clients
    assert disposed == [True, True]
