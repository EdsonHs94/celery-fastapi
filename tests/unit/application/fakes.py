"""Dobles con copias y commits explícitos, sin mutaciones persistidas por alias."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from uuid import UUID

from app.application.ports import (
    ReportGenerator,
    ReportRepository,
    ReportStorage,
    ReportTaskPublisher,
)
from app.domain.report import Report


class MemoryRepository(ReportRepository):
    def __init__(self, reports: tuple[Report, ...] = ()):
        self.reports = {report.id: deepcopy(report) for report in reports}
        self.events: list[str] = []
        self.active = False
        self.commits = 0
        self.fail_commits: set[int] = set()
        self.fail_get = False
        self.fail_add = False
        self.fail_save = False
        self.staged: dict[UUID, Report] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        assert not self.active
        self.active = True
        self.staged = deepcopy(self.reports)
        self.events.append("begin")
        try:
            yield
            self.commits += 1
            if self.commits in self.fail_commits:
                raise OSError("credenciales privadas de persistencia")
            self.reports = deepcopy(self.staged)
            self.events.append("commit")
        except Exception:
            self.events.append("rollback")
            raise
        finally:
            self.active = False
            self.staged = {}

    def get(self, report_id: UUID) -> Report | None:
        assert self.active
        self.events.append("get")
        if self.fail_get:
            raise OSError("fallo de lectura privado")
        return deepcopy(self.staged.get(report_id))

    def add(self, report: Report) -> None:
        assert self.active
        self.events.append("add")
        if self.fail_add:
            raise OSError("fallo privado de alta")
        assert report.id not in self.staged
        self.staged[report.id] = deepcopy(report)

    def save(self, report: Report) -> None:
        assert self.active
        self.events.append("save")
        if self.fail_save:
            raise OSError("fallo privado de escritura")
        assert report.id in self.staged
        self.staged[report.id] = deepcopy(report)


class Publisher(ReportTaskPublisher):
    def __init__(self, repository: MemoryRepository):
        self.repository = repository
        self.calls: list[UUID] = []
        self.action: Callable[[UUID], None] = lambda report_id: None
        self.error: Exception | None = None

    def publish(self, report_id: UUID) -> None:
        assert not self.repository.active
        assert report_id in self.repository.reports
        self.repository.events.append("publish")
        self.calls.append(report_id)
        self.action(report_id)
        if self.error:
            raise self.error


class Generator(ReportGenerator):
    def __init__(self, repository: MemoryRepository):
        self.repository = repository
        self.calls: list[tuple[UUID, int]] = []
        self.error: Exception | None = None
        self.action: Callable[[], None] = lambda: None

    def generate(self, report_id: UUID, attempt: int) -> bytes:
        assert not self.repository.active
        report = self.repository.reports[report_id]
        assert report.status == "processing"
        assert report.attempts == attempt
        self.calls.append((report_id, attempt))
        self.action()
        if self.error:
            raise self.error
        return b"contenido csv ficticio"


class Storage(ReportStorage):
    def __init__(self, repository: MemoryRepository):
        self.repository = repository
        self.calls: list[tuple[UUID, bytes]] = []
        self.error: Exception | None = None

    def write(self, report_id: UUID, content: bytes) -> str:
        assert not self.repository.active
        self.calls.append((report_id, content))
        if self.error:
            raise self.error
        return f"report-{report_id}.csv"
