"""Composición por invocación sobre el recurso SQL perezoso de cada proceso."""

from collections.abc import Iterator
from contextlib import contextmanager

from celery import Celery

from app.application.use_cases import GenerateReport
from app.infrastructure.csv_report import CSVReportGenerator
from app.infrastructure.persistence import SQLModelReportRepository
from app.infrastructure.report_storage import LocalReportStorage


@contextmanager
def generate_report_scope(app: Celery) -> Iterator[GenerateReport]:
    with app.database.session() as session:
        yield GenerateReport(
            SQLModelReportRepository(session),
            CSVReportGenerator(app.report_settings.simulate_transient_failure),
            LocalReportStorage(app.report_settings.reports_dir),
        )
