"""Casos de uso compartidos por presentación HTTP y procesamiento de tareas."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.errors import (
    ApplicationError,
    PersistenceUnavailable,
    PublicationFailed,
    ReportNotFound,
    TemporaryReportError,
)
from app.application.ports import (
    ReportGenerator,
    ReportRepository,
    ReportStorage,
    ReportTaskPublisher,
)
from app.domain.report import MAX_ATTEMPTS, Report, ReportStatus


@contextmanager
def _transaction(repository: ReportRepository) -> Iterator[None]:
    """Traduce fallos del adaptador sin incluir detalles privados en el mensaje."""
    try:
        with repository.transaction():
            yield
    except ApplicationError:
        raise
    except Exception as exc:
        raise PersistenceUnavailable() from exc


def _get(repository: ReportRepository, report_id: UUID) -> Report:
    report = repository.get(report_id)
    if report is None:
        raise ReportNotFound(report_id)
    return report


class RequestReport:
    def __init__(
        self, repository: ReportRepository, publisher: ReportTaskPublisher
    ) -> None:
        self.repository = repository
        self.publisher = publisher

    def execute(self) -> UUID:
        report = Report(id=uuid4(), created_at=datetime.now(UTC))
        with _transaction(self.repository):
            self.repository.add(report)
        try:
            self.publisher.publish(report.id)
        except Exception as exc:
            # El broker puede haber entregado el encargo pese al error detectado.
            # Releer evita sobrescribir un resultado terminal del worker.
            try:
                with _transaction(self.repository):
                    current = _get(self.repository, report.id)
                    if not current.is_terminal:
                        self.repository.save(current.fail("publication_failed"))
            except ApplicationError:
                # El error público conserva el UUID aun si falla esta compensación.
                pass
            raise PublicationFailed(report.id) from exc
        # No devolver pending: el worker pudo avanzar durante publish.
        return report.id


class GetReport:
    def __init__(self, repository: ReportRepository) -> None:
        self.repository = repository

    def execute(self, report_id: UUID) -> Report:
        with _transaction(self.repository):
            return _get(self.repository, report_id)


class GenerateReport:
    def __init__(
        self,
        repository: ReportRepository,
        generator: ReportGenerator,
        storage: ReportStorage,
    ) -> None:
        self.repository = repository
        self.generator = generator
        self.storage = storage

    def execute(self, report_id: UUID) -> Report:
        with _transaction(self.repository):
            report = _get(self.repository, report_id)
            if report.is_terminal or report.status == ReportStatus.PROCESSING:
                # No hay recuperación automática de intentos interrumpidos.
                return report
            if report.attempts >= MAX_ATTEMPTS:
                report = report.fail("attempts_exhausted")
                self.repository.save(report)
                return report
            report = report.start_attempt()
            self.repository.save(report)

        # Generación y archivos quedan fuera de las transacciones SQL.
        file_ref = None
        error_code = None
        temporary = False
        try:
            content = self.generator.generate(report.id, report.attempts)
            file_ref = self.storage.write(report.id, content)
        except TemporaryReportError:
            temporary = True
            error_code = "temporary_failure"
        except Exception:
            error_code = "generation_failed"

        with _transaction(self.repository):
            current = _get(self.repository, report_id)
            if current.is_terminal:
                return current
            if error_code is None:
                result = current.complete(file_ref)
            elif temporary and current.attempts < MAX_ATTEMPTS:
                result = current.retry(error_code)
            else:
                result = current.fail("attempts_exhausted" if temporary else error_code)
            self.repository.save(result)

        # Un commit fallido nunca debe provocar un retry con estado no confirmado.
        if result.status == ReportStatus.RETRYING:
            raise TemporaryReportError()
        return result
