"""Reglas del ciclo de vida sin persistencia ni frameworks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.report import InvalidReportTransition, Report, ReportStatus

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(seconds=5)


def test_pending_defaults_and_first_attempt():
    report = Report(id=uuid4(), created_at=NOW)
    assert report.status == ReportStatus.PENDING
    assert report.attempts == 0
    assert (
        report.started_at,
        report.finished_at,
        report.file_ref,
        report.error_code,
    ) == (None, None, None, None)
    processing = report.start_attempt(NOW)
    assert processing.status == ReportStatus.PROCESSING
    assert processing.attempts == 1
    assert processing.started_at == NOW
    assert report.status == ReportStatus.PENDING


def test_retry_preserves_first_start_and_completion_clears_error():
    report = Report(id=uuid4(), created_at=NOW).start_attempt(NOW)
    retrying = report.retry("temporary_failure")
    assert retrying.status == ReportStatus.RETRYING
    assert retrying.finished_at is None
    processing = retrying.start_attempt(LATER)
    assert processing.attempts == 2
    assert processing.started_at == NOW
    completed = processing.complete("report.csv", LATER)
    assert completed.status == ReportStatus.COMPLETED
    assert completed.file_ref == "report.csv"
    assert completed.finished_at == LATER
    assert completed.error_code is None


@pytest.mark.parametrize("status", list(ReportStatus))
@pytest.mark.parametrize("operation", ["start_attempt", "retry", "complete", "fail"])
def test_transition_matrix(status, operation):
    allowed = {
        "start_attempt": {ReportStatus.PENDING, ReportStatus.RETRYING},
        "retry": {ReportStatus.PROCESSING},
        "complete": {ReportStatus.PROCESSING},
        "fail": {ReportStatus.PENDING, ReportStatus.PROCESSING, ReportStatus.RETRYING},
    }
    report = Report(id=uuid4(), status=status, attempts=1, created_at=NOW)
    arguments = {
        "start_attempt": (LATER,),
        "retry": ("temporary_failure",),
        "complete": ("report.csv", LATER),
        "fail": ("generation_failed", LATER),
    }
    if status in allowed[operation]:
        getattr(report, operation)(*arguments[operation])
    else:
        with pytest.raises(InvalidReportTransition):
            getattr(report, operation)(*arguments[operation])


@pytest.mark.parametrize("completed", [True, False])
def test_terminal_data_cannot_be_replaced_by_transitions(completed):
    processing = Report(id=uuid4(), created_at=NOW).start_attempt(NOW)
    terminal = (
        processing.complete("original.csv", LATER)
        if completed
        else processing.fail("generation_failed", LATER)
    )
    before = replace(terminal)
    for operation, args in [
        ("start_attempt", (LATER,)),
        ("retry", ("temporary_failure",)),
        ("complete", ("new.csv", LATER)),
        ("fail", ("publication_failed", LATER)),
    ]:
        with pytest.raises(InvalidReportTransition):
            getattr(terminal, operation)(*args)
        assert terminal == before


def test_domain_disallows_fourth_attempt_and_retry_after_third():
    report = Report(id=uuid4(), created_at=NOW)
    for attempt in (1, 2, 3):
        report = report.start_attempt(NOW)
        assert report.attempts == attempt
        if attempt < 3:
            report = report.retry("temporary_failure")
    with pytest.raises(InvalidReportTransition):
        report.retry("temporary_failure")
    exhausted = replace(report, status=ReportStatus.RETRYING)
    with pytest.raises(InvalidReportTransition):
        exhausted.start_attempt(NOW)
