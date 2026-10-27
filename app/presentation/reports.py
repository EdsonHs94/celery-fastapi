"""Transporte HTTP síncrono; los casos de uso conservan las transacciones."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.application.errors import ReportNotReady
from app.application.ports import ReportFileResolver
from app.application.use_cases import GetReport, RequestReport
from app.bootstrap.http_dependencies import (
    get_get_report,
    get_report_storage,
    get_request_report,
)
from app.domain.report import ReportStatus
from app.presentation.report_schemas import (
    ErrorResponse,
    PublicationErrorResponse,
    ReportAccepted,
    ReportCreate,
    ReportResponse,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "",
    status_code=202,
    response_model=ReportAccepted,
    responses={
        503: {
            "model": ErrorResponse | PublicationErrorResponse,
            "description": "Persistencia o publicación no disponible",
        },
    },
)
def request_report(
    body: ReportCreate,
    use_case: Annotated[RequestReport, Depends(get_request_report)],
) -> ReportAccepted:
    identifier = use_case.execute()
    return ReportAccepted(
        id=identifier,
        status_url=f"/reports/{identifier}",
        download_url=f"/reports/{identifier}/download",
    )


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_report(
    report_id: UUID,
    use_case: Annotated[GetReport, Depends(get_get_report)],
) -> ReportResponse:
    report = use_case.execute(report_id)
    return ReportResponse(
        id=report.id,
        status=report.status,
        attempts=report.attempts,
        created_at=report.created_at,
        started_at=report.started_at,
        finished_at=report.finished_at,
        error_code=report.error_code,
        download_url=f"/reports/{report.id}/download"
        if report.status == ReportStatus.COMPLETED
        else None,
    )


@router.get(
    "/{report_id}/download",
    response_class=FileResponse,
    responses={
        200: {
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
            "description": "CSV completo como archivo adjunto",
        },
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def download_report(
    report_id: UUID,
    use_case: Annotated[GetReport, Depends(get_get_report)],
    storage: Annotated[ReportFileResolver, Depends(get_report_storage)],
) -> FileResponse:
    report = use_case.execute(report_id)
    if report.status != ReportStatus.COMPLETED:
        raise ReportNotReady()
    path = storage.resolve(report.file_ref)
    return FileResponse(path, media_type="text/csv", filename=f"report-{report.id}.csv")
