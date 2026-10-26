"""Construcción de la entrada HTTP del laboratorio."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.config import Settings
from app.bootstrap.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else Settings()
    database = Database(str(configured.database_url))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            database.dispose()

    application = FastAPI(title="Celery FastAPI Lab", lifespan=lifespan)
    application.state.settings = configured
    application.state.database = database

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Comprueba el proceso HTTP; no certifica sus dependencias externas."""
        return {"status": "ok"}

    return application
