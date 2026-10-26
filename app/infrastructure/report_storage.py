"""Archivos CSV completos dentro del directorio compartido de reportes."""

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from app.application.errors import ReportFileMissing


class LocalReportStorage:
    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir.resolve()

    def write(self, report_id: UUID, content: bytes) -> str:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        file_ref = f"report-{report_id}.csv"
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.reports_dir,
                prefix=".report-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
            # El cierre termina antes de publicar el archivo completo.
            os.replace(temporary_path, self.reports_dir / file_ref)
        finally:
            if temporary_path is not None:
                # No ocultar el error original si tampoco se puede limpiar.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
        return file_ref

    def resolve(self, file_ref: str) -> Path:
        """Resuelve solo nombres finales internos; nunca temporales ni symlinks."""
        try:
            report_id = UUID(file_ref[7:-4])
        except ValueError:
            raise ValueError("Referencia de reporte inválida.") from None
        if file_ref != f"report-{report_id}.csv":
            raise ValueError("Referencia de reporte inválida.")

        path = self.reports_dir / file_ref
        if path.is_symlink() or not path.resolve().is_relative_to(self.reports_dir):
            raise ValueError("Referencia de reporte inválida.")
        try:
            mode = path.stat().st_mode
        except FileNotFoundError as exc:
            raise ReportFileMissing() from exc
        if not stat.S_ISREG(mode):
            raise ValueError("Referencia de reporte inválida.")
        return path
