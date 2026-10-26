"""Construcción de la entrada HTTP del laboratorio."""

from fastapi import FastAPI

from app.bootstrap.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Celery FastAPI Lab")
    application.state.settings = settings if settings is not None else Settings()

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Comprueba el proceso HTTP; no certifica sus dependencias externas."""
        return {"status": "ok"}

    return application
