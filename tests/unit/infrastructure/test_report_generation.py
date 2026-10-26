"""Composición local con adaptadores reales, sin tareas Celery ni rutas HTTP."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.errors import TemporaryReportError
from app.application.use_cases import GenerateReport
from app.domain.report import Report, ReportStatus
from app.infrastructure.csv_report import CSVReportGenerator
from app.infrastructure.report_storage import LocalReportStorage
from tests.unit.application.fakes import MemoryRepository


@pytest.mark.parametrize("simulate_failure", [True, False])
def test_generate_report_publishes_file_only_after_success(tmp_path, simulate_failure):
    report = Report(id=uuid4(), created_at=datetime.now(UTC))
    repository = MemoryRepository((report,))
    root = tmp_path / "reports"
    storage = LocalReportStorage(root)
    generate = GenerateReport(repository, CSVReportGenerator(simulate_failure), storage)

    if simulate_failure:
        with pytest.raises(TemporaryReportError):
            generate.execute(report.id)
        persisted = repository.reports[report.id]
        assert persisted.status == ReportStatus.RETRYING
        assert persisted.attempts == 1
        assert persisted.file_ref is None
        assert not root.exists()

    completed = generate.execute(report.id)
    assert completed.status == ReportStatus.COMPLETED
    assert completed.attempts == (2 if simulate_failure else 1)
    assert completed.error_code is None
    assert completed.file_ref == f"report-{report.id}.csv"
    assert repository.reports[report.id] == completed
    path = storage.resolve(completed.file_ref)
    lines = path.read_bytes().splitlines()
    assert len(lines) == 101
    assert lines[0] == b"sale_id,customer,total,currency"
    assert lines[-1] == b"100,Cliente 100,1000.00,PEN"
