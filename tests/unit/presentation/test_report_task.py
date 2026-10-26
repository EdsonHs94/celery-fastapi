from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from celery.exceptions import Reject, Retry

from app.application.errors import TemporaryReportError
from app.application.use_cases import GenerateReport
from app.domain.report import Report
from tests.unit.application.fakes import Generator, MemoryRepository, Storage


def invoke(
    monkeypatch,
    tmp_path,
    *,
    error=None,
    retry_error=None,
    terminal=False,
    fail_compensation=False,
):
    import app.bootstrap.reports as composition
    from app.bootstrap.config import Settings
    from app.bootstrap.worker import create_celery_app
    from app.presentation.report_task import register_report_task

    report = Report(id=uuid4(), created_at=datetime.now(UTC))
    if terminal:
        report = report.fail("generation_failed")
    repository = MemoryRepository((report,))
    generator = Generator(repository)
    generator.error = error
    scope = []

    @contextmanager
    def dependencies(app):
        scope.append("open")
        try:
            yield GenerateReport(repository, generator, Storage(repository))
        finally:
            scope.append("close")

    monkeypatch.setattr(composition, "generate_report_scope", dependencies)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@db/reports",
        broker_url="amqp://u:p@broker/reports",
        reports_dir=tmp_path,
    )
    app = create_celery_app(settings)
    task = register_report_task(app)
    retry = Mock(side_effect=retry_error or Retry())
    monkeypatch.setattr(task, "retry", retry)
    if fail_compensation:
        repository.fail_commits.add(3)
    return task, report, repository, generator, retry, scope


def test_temporary_error_schedules_only_bounded_retry_and_closes(monkeypatch, tmp_path):
    task, report, repository, _, retry, scope = invoke(
        monkeypatch, tmp_path, error=TemporaryReportError()
    )
    with pytest.raises(Retry):
        task.run(str(report.id))
    assert repository.reports[report.id].status == "retrying"
    assert repository.reports[report.id].attempts == 1
    assert retry.call_args.kwargs["countdown"] == 5
    assert retry.call_args.kwargs["max_retries"] == 2
    assert (
        retry.call_args.kwargs["timeout"]
        == retry.call_args.kwargs["confirm_timeout"]
        == 5
    )
    assert scope == ["open", "close"]


@pytest.mark.parametrize(
    "error,terminal,status",
    [
        (ValueError("private"), False, "failed"),
        (None, False, "completed"),
        (None, True, "failed"),
    ],
)
def test_task_does_not_retry_permanent_success_or_terminal(
    monkeypatch, tmp_path, error, terminal, status, caplog
):
    task, report, repository, generator, retry, scope = invoke(
        monkeypatch, tmp_path, error=error, terminal=terminal
    )
    with caplog.at_level("INFO"):
        assert task.run(str(report.id)) is None
    assert repository.reports[report.id].status == status
    retry.assert_not_called()
    assert scope == ["open", "close"]
    if terminal:
        assert generator.calls == []
    assert str(report.id) in caplog.text


@pytest.mark.parametrize("fail_compensation", [False, True])
def test_retry_publication_failure_attempts_failed_preserving_original(
    monkeypatch, tmp_path, fail_compensation, caplog
):
    original = Reject(OSError("private"), requeue=False)
    task, report, repository, _, _, scope = invoke(
        monkeypatch,
        tmp_path,
        error=TemporaryReportError(),
        retry_error=original,
        fail_compensation=fail_compensation,
    )
    with pytest.raises(Reject) as raised:
        task.run(str(report.id))
    assert raised.value is original
    current = repository.reports[report.id]
    assert current.status == ("retrying" if fail_compensation else "failed")
    assert current.error_code == (
        "temporary_failure" if fail_compensation else "retry_publication_failed"
    )
    assert scope == ["open", "close"]
    assert "private" not in caplog.text


def test_real_celery_retry_publishes_json_signature(monkeypatch, tmp_path):
    from celery.app.task import Task
    from celery.canvas import Signature

    task, report, repository, _, _, scope = invoke(
        monkeypatch, tmp_path, error=TemporaryReportError()
    )
    monkeypatch.setattr(task, "retry", Task.retry.__get__(task, type(task)))
    published = []
    monkeypatch.setattr(Signature, "apply_async", lambda self: published.append(self))
    task.push_request(
        id=str(report.id),
        args=[str(report.id)],
        kwargs={},
        retries=0,
        called_directly=False,
        is_eager=False,
    )
    try:
        with pytest.raises(Retry):
            task.run(str(report.id))
    finally:
        task.pop_request()
    assert len(published) == 1
    signature = published[0]
    assert tuple(signature.args) == (str(report.id),)
    assert signature.options["countdown"] == 5
    assert signature.options["retries"] == 1
    assert signature.options["retry"] is False
    assert repository.reports[report.id].status == "retrying"
    assert scope == ["open", "close"]


def test_real_celery_retry_publication_reject_compensates(monkeypatch, tmp_path):
    from celery.app.task import Task
    from celery.canvas import Signature

    task, report, repository, _, _, _ = invoke(
        monkeypatch, tmp_path, error=TemporaryReportError()
    )
    monkeypatch.setattr(task, "retry", Task.retry.__get__(task, type(task)))

    def unavailable(self):
        raise OSError("private")

    monkeypatch.setattr(Signature, "apply_async", unavailable)
    task.push_request(
        id=str(report.id),
        args=[str(report.id)],
        kwargs={},
        retries=0,
        called_directly=False,
        is_eager=False,
    )
    try:
        with pytest.raises(Reject):
            task.run(str(report.id))
    finally:
        task.pop_request()
    assert repository.reports[report.id].error_code == "retry_publication_failed"


def test_compensation_preserves_terminal_observed_after_ambiguous_publish(
    monkeypatch, tmp_path
):
    task, report, repository, _, retry, _ = invoke(
        monkeypatch, tmp_path, error=TemporaryReportError()
    )

    def ambiguous(**kwargs):
        repository.reports[report.id] = (
            repository.reports[report.id]
            .start_attempt()
            .complete(f"report-{report.id}.csv")
        )
        raise Reject("unconfirmed", requeue=False)

    retry.side_effect = ambiguous
    with pytest.raises(Reject):
        task.run(str(report.id))
    assert repository.reports[report.id].status == "completed"
    assert repository.reports[report.id].error_code is None


def test_real_composition_opens_distinct_scope_and_closes_on_error(tmp_path):
    from contextlib import contextmanager

    from app.bootstrap.config import Settings
    from app.bootstrap.reports import generate_report_scope
    from app.bootstrap.worker import create_celery_app

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@db/reports",
        broker_url="amqp://u:p@broker/reports",
        reports_dir=tmp_path,
        simulate_transient_failure=False,
    )
    app = create_celery_app(settings)
    sessions = []
    closed = []

    @contextmanager
    def session():
        value = object()
        sessions.append(value)
        try:
            yield value
        finally:
            closed.append(value)

    app.database.session = session
    with generate_report_scope(app) as use_case:
        assert use_case.repository._session is sessions[0]
        assert use_case.storage.reports_dir == tmp_path
    with pytest.raises(ValueError), generate_report_scope(app):
        raise ValueError()
    assert sessions[0] is not sessions[1]
    assert closed == sessions


def test_factories_compose_with_their_own_app_before_finalize(monkeypatch, tmp_path):
    import app.bootstrap.reports as composition
    from app.bootstrap.config import Settings
    from app.bootstrap.worker import create_celery_app

    first_settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@first/reports",
        broker_url="amqp://u:p@broker/reports",
        reports_dir=tmp_path / "first",
        simulate_transient_failure=True,
    )
    second_settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@second/reports",
        broker_url="amqp://u:p@broker/reports",
        reports_dir=tmp_path / "second",
        simulate_transient_failure=False,
    )
    observed = []
    report = Report(id=uuid4(), created_at=datetime.now(UTC))

    @contextmanager
    def dependencies(app):
        observed.append((app, app.database, app.report_settings))
        repository = MemoryRepository((report,))
        yield GenerateReport(repository, Generator(repository), Storage(repository))

    monkeypatch.setattr(composition, "generate_report_scope", dependencies)
    first = create_celery_app(first_settings)
    second = create_celery_app(second_settings)
    try:
        assert not first.finalized and not second.finalized
        first.finalize()
        second.finalize()
        first.tasks["reports.generate"].run(str(report.id))
        second.tasks["reports.generate"].run(str(report.id))
        assert observed == [
            (first, first.database, first_settings),
            (second, second.database, second_settings),
        ]
        assert first.database is not second.database
    finally:
        first.close()
        second.close()
