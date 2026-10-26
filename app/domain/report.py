"""Entidad de reporte y transiciones; sin dependencias de infraestructura."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

MAX_ATTEMPTS = 3


class ReportStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidReportTransition(ValueError):
    """La operación no es válida para el estado o presupuesto actual."""


@dataclass(frozen=True)
class Report:
    id: UUID
    created_at: datetime
    status: ReportStatus = ReportStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    file_ref: str | None = None
    error_code: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {ReportStatus.COMPLETED, ReportStatus.FAILED}

    def _require(self, *statuses: ReportStatus) -> None:
        if self.status not in statuses:
            raise InvalidReportTransition(f"Transición no válida desde {self.status}")

    def start_attempt(self, now: datetime | None = None) -> Report:
        self._require(ReportStatus.PENDING, ReportStatus.RETRYING)
        if self.attempts >= MAX_ATTEMPTS:
            raise InvalidReportTransition("Presupuesto de intentos agotado")
        return replace(
            self,
            status=ReportStatus.PROCESSING,
            attempts=self.attempts + 1,
            started_at=self.started_at or now or datetime.now(UTC),
        )

    def retry(self, error_code: str) -> Report:
        self._require(ReportStatus.PROCESSING)
        if self.attempts >= MAX_ATTEMPTS:
            raise InvalidReportTransition("Presupuesto de intentos agotado")
        return replace(self, status=ReportStatus.RETRYING, error_code=error_code)

    def complete(self, file_ref: str, now: datetime | None = None) -> Report:
        self._require(ReportStatus.PROCESSING)
        return replace(
            self,
            status=ReportStatus.COMPLETED,
            file_ref=file_ref,
            error_code=None,
            finished_at=now or datetime.now(UTC),
        )

    def fail(self, error_code: str, now: datetime | None = None) -> Report:
        self._require(
            ReportStatus.PENDING, ReportStatus.PROCESSING, ReportStatus.RETRYING
        )
        return replace(
            self,
            status=ReportStatus.FAILED,
            error_code=error_code,
            finished_at=now or datetime.now(UTC),
        )
