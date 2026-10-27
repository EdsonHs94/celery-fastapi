"""Composición de casos de uso HTTP y adaptadores por aplicación."""

from typing import Annotated

from fastapi import Depends, Request

from app.application.ports import (
    ReportFileResolver,
    ReportRepository,
    ReportTaskPublisher,
)
from app.application.use_cases import GetReport, RequestReport
from app.bootstrap.database import get_report_repository
from app.bootstrap.worker import create_celery_app
from app.infrastructure.celery_publisher import CeleryReportTaskPublisher
from app.infrastructure.report_storage import LocalReportStorage


def get_report_publisher(request: Request) -> ReportTaskPublisher:
    state = request.app.state
    with state.celery_lock:
        if state.celery is None:
            state.celery = create_celery_app(state.settings)
        return CeleryReportTaskPublisher(state.celery)


def get_report_storage(request: Request) -> ReportFileResolver:
    return LocalReportStorage(request.app.state.settings.reports_dir)


def get_request_report(
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
    publisher: Annotated[ReportTaskPublisher, Depends(get_report_publisher)],
) -> RequestReport:
    return RequestReport(repository, publisher)


def get_get_report(
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
) -> GetReport:
    return GetReport(repository)
