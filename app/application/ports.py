"""Contratos síncronos; los adaptadores no filtran sesiones ni frameworks."""

from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from app.domain.report import Report


class ReportRepository(Protocol):
    """Todas las operaciones requieren un bloque transaction activo.

    El adaptador confirma al salir con éxito y revierte ante excepción,
    incluido un error del commit. get devuelve una entidad separada del ORM.
    """

    def get(self, report_id: UUID) -> Report | None: ...

    def add(self, report: Report) -> None: ...

    def save(self, report: Report) -> None: ...

    def transaction(self) -> AbstractContextManager[None]: ...


class ReportTaskPublisher(Protocol):
    def publish(self, report_id: UUID) -> None: ...


class ReportGenerator(Protocol):
    def generate(self, report_id: UUID, attempt: int) -> bytes: ...


class ReportStorage(Protocol):
    def write(self, report_id: UUID, content: bytes) -> str: ...
