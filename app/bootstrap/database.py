"""Recursos SQL síncronos por proceso y sesiones acotadas a petición/tarea."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from fastapi import Request
from sqlalchemy.engine import URL, Engine
from sqlmodel import Session, create_engine

from app.application.ports import ReportRepository
from app.infrastructure.persistence import SQLModelReportRepository


class Database:
    """Construcción perezosa: instanciar en el padre Celery no abre conexiones."""

    def __init__(self, url: str | URL) -> None:
        self._url = url
        self._engine: Engine | None = None
        self._pid = os.getpid()
        self._lock = Lock()

    @property
    def engine(self) -> Engine:
        if self._pid != os.getpid():
            self.reset_after_fork()
        with self._lock:
            if self._engine is None:
                self._engine = create_engine(
                    self._url,
                    pool_pre_ping=True,
                    hide_parameters=True,
                    connect_args={"options": "-c timezone=UTC"},
                )
            return self._engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        # El repositorio decide commit/rollback; este scope siempre cierra la sesión.
        with Session(self.engine) as session:
            yield session

    def reset_after_fork(self) -> None:
        # No cerrar sockets del padre desde el hijo ni heredar un lock tomado.
        self._lock = Lock()
        if self._engine is not None:
            self._engine.dispose(close=False)
        self._engine = None
        self._pid = os.getpid()

    def dispose(self) -> None:
        if self._pid != os.getpid():
            self.reset_after_fork()
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


def get_report_repository(request: Request) -> Iterator[ReportRepository]:
    """Dependencia para futuras rutas síncronas; no abre sesión en /health."""
    with request.app.state.database.session() as session:
        yield SQLModelReportRepository(session)
