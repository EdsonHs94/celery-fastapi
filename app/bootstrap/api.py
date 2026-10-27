"""Construcción de la entrada HTTP del laboratorio."""

import logging
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.application.errors import (
    ApplicationError,
    PersistenceUnavailable,
    PublicationFailed,
    ReportFileMissing,
    ReportNotFound,
    ReportNotReady,
)
from app.bootstrap.config import Settings
from app.bootstrap.database import Database
from app.presentation.reports import router


def create_app(settings: Settings | None = None) -> FastAPI:
    # Uvicorn configura sus propios loggers; el seguimiento de app necesita INFO.
    # basicConfig conserva handlers/niveles cuando el host ya configuró logging.
    logging.basicConfig(level=logging.INFO)
    configured = settings if settings is not None else Settings()
    database = Database(str(configured.database_url))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            database.dispose()
            if application.state.celery is not None:
                application.state.celery.database.dispose()
                application.state.celery.close()
                application.state.celery = None

    application = FastAPI(title="Celery FastAPI Lab", lifespan=lifespan)
    application.state.settings = configured
    application.state.database = database
    application.state.celery = None
    application.state.celery_lock = Lock()
    application.include_router(router)

    @application.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError):
        statuses = {
            PersistenceUnavailable: 503,
            PublicationFailed: 503,
            ReportNotFound: 404,
            ReportNotReady: 409,
            ReportFileMissing: 410,
        }
        detail = {"code": error.code, "message": error.message}
        if isinstance(error, PublicationFailed):
            detail["report_id"] = str(error.report_id)
        return JSONResponse(
            status_code=statuses[type(error)], content={"detail": detail}
        )

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Comprueba el proceso HTTP; no certifica sus dependencias externas."""
        return {"status": "ok"}

    return application
