"""Orden de efectos, errores públicos y presupuesto de generación."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.errors import (
    PersistenceUnavailable,
    PublicationFailed,
    ReportNotFound,
    TemporaryReportError,
)
from app.application.use_cases import GenerateReport, GetReport, RequestReport
from app.domain.report import Report, ReportStatus
from tests.unit.application.fakes import Generator, MemoryRepository, Publisher, Storage


def setup_generation(report=None):
    report = report or Report(id=uuid4(), created_at=datetime.now(UTC))
    repository = MemoryRepository((report,))
    generator, storage = Generator(repository), Storage(repository)
    return (
        report,
        repository,
        generator,
        storage,
        GenerateReport(repository, generator, storage),
    )


def test_request_commits_pending_before_publication_and_returns_identifier():
    repository = MemoryRepository()
    publisher = Publisher(repository)
    report_id = RequestReport(repository, publisher).execute()
    assert isinstance(report_id, UUID)
    assert publisher.calls == [report_id]
    assert repository.events == ["begin", "add", "commit", "publish"]
    report = repository.reports[report_id]
    assert report.status == "pending"
    assert report.attempts == 0
    assert report.created_at.tzinfo == UTC


@pytest.mark.parametrize("failure", ["commit", "add"])
def test_request_does_not_publish_when_initial_persistence_fails(failure):
    repository = MemoryRepository()
    repository.fail_commits = {1} if failure == "commit" else set()
    repository.fail_add = failure == "add"
    publisher = Publisher(repository)
    with pytest.raises(PersistenceUnavailable) as caught:
        RequestReport(repository, publisher).execute()
    assert caught.value.code == "persistence_unavailable"
    assert "privad" not in str(caught.value)
    assert not repository.reports
    assert not publisher.calls
    assert repository.events[-1] == "rollback"


def test_publication_failure_records_failed_and_exposes_only_public_error():
    repository = MemoryRepository()
    publisher = Publisher(repository)
    publisher.error = OSError("amqp://usuario:clave@privado")
    with pytest.raises(PublicationFailed) as caught:
        RequestReport(repository, publisher).execute()
    report = repository.reports[caught.value.report_id]
    assert caught.value.code == "publication_failed"
    assert "clave" not in str(caught.value)
    assert report.status == "failed"
    assert report.error_code == "publication_failed"
    assert report.finished_at is not None
    assert report.attempts == 0


def test_publication_error_survives_failure_to_record_terminal_state():
    repository = MemoryRepository()
    repository.fail_commits = {2}
    publisher = Publisher(repository)
    publisher.error = OSError("broker privado")
    with pytest.raises(PublicationFailed) as caught:
        RequestReport(repository, publisher).execute()
    assert repository.reports[caught.value.report_id].status == "pending"


@pytest.mark.parametrize("publication_fails", [False, True])
@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_request_preserves_worker_terminal_result_during_publication(
    publication_fails, terminal
):
    repository = MemoryRepository()
    publisher = Publisher(repository)

    def worker(report_id):
        processing = repository.reports[report_id].start_attempt()
        repository.reports[report_id] = (
            processing.complete("original.csv")
            if terminal == "completed"
            else processing.fail("generation_failed")
        )

    publisher.action = worker
    publisher.error = OSError("broker") if publication_fails else None
    if publication_fails:
        with pytest.raises(PublicationFailed) as caught:
            RequestReport(repository, publisher).execute()
        report_id = caught.value.report_id
    else:
        report_id = RequestReport(repository, publisher).execute()
    report = repository.reports[report_id]
    assert report.status == terminal
    assert report.attempts == 1
    assert report.error_code == (
        None if terminal == "completed" else "generation_failed"
    )
    assert report.file_ref == ("original.csv" if terminal == "completed" else None)


@pytest.mark.parametrize("status", list(ReportStatus))
def test_get_returns_every_persisted_state_without_writes(status):
    report = Report(id=uuid4(), created_at=datetime.now(UTC), status=status)
    repository = MemoryRepository((report,))
    result = GetReport(repository).execute(report.id)
    assert result == report
    assert repository.events == ["begin", "get", "commit"]
    assert repository.reports == {report.id: report}


@pytest.mark.parametrize("operation", ["get", "generate"])
def test_unknown_report_has_no_write_or_external_effect(operation):
    repository = MemoryRepository()
    generator, storage = Generator(repository), Storage(repository)
    use_case = (
        GetReport(repository)
        if operation == "get"
        else GenerateReport(repository, generator, storage)
    )
    with pytest.raises(ReportNotFound) as caught:
        use_case.execute(uuid4())
    assert caught.value.code == "report_not_found"
    assert not repository.reports
    assert not generator.calls and not storage.calls
    assert "add" not in repository.events and "save" not in repository.events


def test_read_persistence_failure_is_application_error():
    repository = MemoryRepository()
    repository.fail_get = True
    with pytest.raises(PersistenceUnavailable):
        GetReport(repository).execute(uuid4())


def test_generation_commits_processing_then_writes_outside_transactions():
    report, repository, generator, storage, use_case = setup_generation()
    result = use_case.execute(report.id)
    assert result == repository.reports[report.id]
    assert result.status == "completed"
    assert result.attempts == 1
    assert result.started_at is not None and result.finished_at is not None
    assert result.file_ref == f"report-{report.id}.csv"
    assert result.error_code is None
    assert generator.calls == [(report.id, 1)]
    assert storage.calls == [(report.id, b"contenido csv ficticio")]
    assert repository.events == [
        "begin",
        "get",
        "save",
        "commit",
        "begin",
        "get",
        "save",
        "commit",
    ]


def test_transient_retry_then_success_keeps_first_start_and_clears_error():
    report, repository, generator, storage, use_case = setup_generation()
    generator.error = TemporaryReportError()
    with pytest.raises(TemporaryReportError):
        use_case.execute(report.id)
    retrying = repository.reports[report.id]
    assert retrying.status == "retrying" and retrying.attempts == 1
    assert retrying.error_code == "temporary_failure"
    assert retrying.finished_at is None and retrying.file_ref is None
    assert not storage.calls
    generator.error = None
    result = use_case.execute(report.id)
    assert result.status == "completed" and result.attempts == 2
    assert result.started_at == retrying.started_at
    assert result.error_code is None
    assert generator.calls == [(report.id, 1), (report.id, 2)]


def test_third_transient_failure_is_terminal_and_fourth_delivery_has_no_effect():
    report, repository, generator, storage, use_case = setup_generation()
    generator.error = TemporaryReportError()
    for expected_attempt in (1, 2):
        with pytest.raises(TemporaryReportError):
            use_case.execute(report.id)
        assert repository.reports[report.id].attempts == expected_attempt
    result = use_case.execute(report.id)
    assert result.status == "failed" and result.attempts == 3
    assert result.error_code == "attempts_exhausted"
    assert result.finished_at is not None
    assert use_case.execute(report.id) == result
    assert generator.calls == [(report.id, 1), (report.id, 2), (report.id, 3)]
    assert not storage.calls


@pytest.mark.parametrize("stage", ["generator", "storage"])
@pytest.mark.parametrize("temporary", [False, True])
def test_errors_in_generation_or_storage_are_recorded(stage, temporary):
    report, repository, generator, storage, use_case = setup_generation()
    dependency = generator if stage == "generator" else storage
    dependency.error = (
        TemporaryReportError() if temporary else ValueError("ruta privada")
    )
    if temporary:
        with pytest.raises(TemporaryReportError):
            use_case.execute(report.id)
    else:
        use_case.execute(report.id)
    result = repository.reports[report.id]
    assert result.status == ("retrying" if temporary else "failed")
    assert result.error_code == (
        "temporary_failure" if temporary else "generation_failed"
    )
    assert result.attempts == 1 and result.file_ref is None
    assert (result.finished_at is None) == temporary


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_terminal_delivery_never_changes_data_or_calls_generator_storage(terminal):
    processing = Report(id=uuid4(), created_at=datetime.now(UTC)).start_attempt()
    report = (
        processing.complete("original.csv")
        if terminal == "completed"
        else processing.fail("generation_failed")
    )
    _, repository, generator, storage, use_case = setup_generation(report)
    assert use_case.execute(report.id) == report
    assert repository.reports[report.id] == report
    assert not generator.calls and not storage.calls
    assert "save" not in repository.events


def test_exhausted_persisted_budget_cannot_start_fourth_attempt():
    report = Report(
        id=uuid4(),
        created_at=datetime.now(UTC),
        status=ReportStatus.RETRYING,
        attempts=3,
    )
    _, repository, generator, storage, use_case = setup_generation(report)
    result = use_case.execute(report.id)
    assert result.status == "failed" and result.attempts == 3
    assert result.error_code == "attempts_exhausted"
    assert not generator.calls and not storage.calls


def test_processing_delivery_does_not_recover_or_duplicate_running_attempt():
    report = Report(id=uuid4(), created_at=datetime.now(UTC)).start_attempt()
    _, repository, generator, storage, use_case = setup_generation(report)
    assert use_case.execute(report.id) == report
    assert repository.reports[report.id] == report
    assert not generator.calls and not storage.calls


@pytest.mark.parametrize("commit", [1, 2])
def test_generation_commit_failure_does_not_claim_completion_or_request_retry(commit):
    report, repository, generator, storage, use_case = setup_generation()
    repository.fail_commits = {commit}
    with pytest.raises(PersistenceUnavailable):
        use_case.execute(report.id)
    result = repository.reports[report.id]
    assert result.status == ("pending" if commit == 1 else "processing")
    assert len(generator.calls) == (0 if commit == 1 else 1)
    assert len(storage.calls) == (0 if commit == 1 else 1)


def test_retry_commit_failure_raises_persistence_error_instead_of_retry_signal():
    report, repository, generator, _, use_case = setup_generation()
    repository.fail_commits = {2}
    generator.error = TemporaryReportError()
    with pytest.raises(PersistenceUnavailable):
        use_case.execute(report.id)
    assert repository.reports[report.id].status == "processing"


@pytest.mark.parametrize("temporary", [False, True])
def test_final_write_preserves_terminal_state_observed_after_external_work(temporary):
    report, repository, generator, _, use_case = setup_generation()
    terminal = report.start_attempt().complete("original.csv")
    generator.action = lambda: repository.reports.update({report.id: terminal})
    generator.error = TemporaryReportError() if temporary else None
    assert use_case.execute(report.id) == terminal
    assert repository.reports[report.id] == terminal


def test_repository_fake_does_not_persist_unsaved_replacement():
    report = Report(id=uuid4(), created_at=datetime.now(UTC))
    repository = MemoryRepository((report,))
    with repository.transaction():
        detached = repository.get(report.id)
        detached = replace(detached, attempts=2)
    assert repository.reports[report.id].attempts == 0
