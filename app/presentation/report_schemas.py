"""Modelos de transporte independientes de la tabla y la entidad."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict
from sqlmodel import SQLModel

from app.domain.report import ReportStatus


class ReportCreate(SQLModel):
    model_config = ConfigDict(extra="forbid")


class ReportAccepted(SQLModel):
    id: UUID
    status_url: str
    download_url: str


class ReportResponse(SQLModel):
    id: UUID
    status: ReportStatus
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    download_url: str | None
    error_code: str | None


class ErrorDetail(SQLModel):
    code: str
    message: str


class ErrorResponse(SQLModel):
    detail: ErrorDetail


class PublicationErrorDetail(ErrorDetail):
    report_id: UUID


class PublicationErrorResponse(SQLModel):
    detail: PublicationErrorDetail
