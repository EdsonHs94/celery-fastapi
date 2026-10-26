"""Adaptador específico SQLModel; las entidades nunca quedan ligadas a la sesión."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, Session, SQLModel

from app.domain.report import Report, ReportStatus


class ReportTable(SQLModel, table=True):
    __tablename__ = "reports"

    id: UUID = Field(primary_key=True)
    status: str
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    file_ref: str | None = None
    error_code: str | None = None


class SQLModelReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._active = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._active or self._session.in_transaction():
            raise RuntimeError("transaction ya activa")
        self._active = True
        try:
            yield
            self._session.commit()
        except BaseException:
            self._session.rollback()
            raise
        finally:
            self._active = False

    def _require_transaction(self) -> None:
        if not self._active:
            raise RuntimeError("Se requiere transaction activa")

    def get(self, report_id: UUID) -> Report | None:
        self._require_transaction()
        row = self._session.get(ReportTable, report_id, populate_existing=True)
        if row is None:
            return None
        return Report(
            id=row.id,
            status=ReportStatus(row.status),
            attempts=row.attempts,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            file_ref=row.file_ref,
            error_code=row.error_code,
        )

    def add(self, report: Report) -> None:
        self._require_transaction()
        self._session.add(ReportTable(**self._values(report)))

    def save(self, report: Report) -> None:
        self._require_transaction()
        row = self._session.get(ReportTable, report.id)
        if row is None:
            raise LookupError("No existe el reporte que se intenta actualizar")
        for name, value in self._values(report).items():
            setattr(row, name, value)

    @staticmethod
    def _values(report: Report) -> dict:
        return {
            "id": report.id,
            "status": report.status.value,
            "attempts": report.attempts,
            "created_at": report.created_at,
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "file_ref": report.file_ref,
            "error_code": report.error_code,
        }
