from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.domain.report import Report, ReportStatus


def repository(session):
    from app.infrastructure.persistence import SQLModelReportRepository

    return SQLModelReportRepository(session)


def new_report():
    return Report(id=uuid4(), created_at=datetime(2026, 10, 26, tzinfo=UTC))


def test_add_read_update_and_detached_entities(engine):
    report = new_report()
    with Session(engine) as session:
        repo = repository(session)
        with repo.transaction():
            repo.add(report)
        with repo.transaction():
            found = repo.get(report.id)
            assert found == report
            assert found is not report
            assert type(found.status) is ReportStatus
            assert found.created_at.utcoffset().total_seconds() == 0
            completed = found.start_attempt().complete("report.csv")
            repo.save(completed)
    with Session(engine) as session:
        repo = repository(session)
        with repo.transaction():
            found = repo.get(report.id)
            assert found == completed
            assert found.started_at.tzinfo is not None
            assert found.finished_at.tzinfo is not None
            # Entity changes are never tracked until save is explicitly called.
            replace(found, file_ref="unsaved.csv")
        with repo.transaction():
            assert repo.get(report.id).file_ref == "report.csv"


def test_missing_uuid_read_and_save(engine):
    with Session(engine) as session:
        repo = repository(session)
        with repo.transaction():
            assert repo.get(uuid4()) is None
        with pytest.raises(LookupError), repo.transaction():
            repo.save(new_report())
        assert not session.in_transaction()


def test_rollback_discards_all_writes_and_allows_reuse(engine):
    first, second = new_report(), new_report()
    with Session(engine) as session:
        repo = repository(session)
        with pytest.raises(ValueError, match="abort"), repo.transaction():
            repo.add(first)
            assert repo.get(first.id) == first  # Flush insert before the failure.
            repo.add(second)
            raise ValueError("abort")
        with repo.transaction():
            assert repo.get(first.id) is None
            assert repo.get(second.id) is None
            repo.add(second)
        with repo.transaction():
            assert repo.get(second.id) == second


def test_commit_failure_rolls_back_flushed_writes_and_allows_reuse(engine):
    report = new_report()
    with Session(engine) as session:
        repo = repository(session)
        with pytest.raises(IntegrityError), repo.transaction():
            repo.add(report)
            assert repo.get(report.id) == report
            repo.add(report)  # Duplicate PK fails when transaction commits.
        assert not session.in_transaction()
        with repo.transaction():
            assert repo.get(report.id) is None
            repo.add(report)


def test_operations_require_transaction_and_nested_transactions_are_rejected(engine):
    with Session(engine) as session:
        repo = repository(session)
        for operation in (
            lambda: repo.get(uuid4()),
            lambda: repo.add(new_report()),
            lambda: repo.save(new_report()),
        ):
            with pytest.raises(RuntimeError, match="transaction"):
                operation()
        with repo.transaction():
            with pytest.raises(RuntimeError, match="transaction"):
                with repo.transaction():
                    pass


def test_database_sessions_are_distinct_and_persist_between_scopes(engine):
    from app.bootstrap.database import Database

    database = Database(engine.url)
    report = new_report()
    try:
        with database.session() as first, database.session() as second:
            assert first is not second
            first_repo, second_repo = repository(first), repository(second)
            with first_repo.transaction():
                first_repo.add(report)
                assert first_repo.get(report.id) == report
                with second_repo.transaction():
                    assert second_repo.get(report.id) is None
            with second_repo.transaction():
                assert second_repo.get(report.id) == report
        assert database.engine.pool.checkedout() == 0
        with pytest.raises(ValueError), database.session() as session:
            repo = repository(session)
            with repo.transaction():
                repo.add(new_report())
                raise ValueError("abort")
        assert database.engine.pool.checkedout() == 0
    finally:
        database.dispose()
