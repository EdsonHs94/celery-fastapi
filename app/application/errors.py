"""Errores públicos independientes de HTTP, SQL y Celery."""

from uuid import UUID


class ApplicationError(Exception):
    code = "application_error"
    message = "No se pudo realizar la operación."

    def __init__(self) -> None:
        super().__init__(self.message)


class PersistenceUnavailable(ApplicationError):
    code = "persistence_unavailable"
    message = "La persistencia no está disponible."


class PublicationFailed(ApplicationError):
    code = "publication_failed"
    message = "No se pudo publicar el encargo del reporte."

    def __init__(self, report_id: UUID) -> None:
        self.report_id = report_id
        super().__init__()


class ReportNotFound(ApplicationError):
    code = "report_not_found"
    message = "El reporte solicitado no existe."

    def __init__(self, report_id: UUID) -> None:
        self.report_id = report_id
        super().__init__()


class TemporaryReportError(ApplicationError):
    """Señal de fallo transitorio; solo se propaga si queda presupuesto."""

    code = "temporary_failure"
    message = "La generación falló temporalmente."


class ReportFileMissing(ApplicationError):
    code = "report_file_missing"
    message = "El archivo del reporte ya no está disponible."


class ReportNotReady(ApplicationError):
    code = "report_not_ready"
    message = "El reporte todavía no está disponible para descarga."
