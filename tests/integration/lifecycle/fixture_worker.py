"""Worker exclusivo del recorrido real: sustituye únicamente el generador.

Se monta read-only fuera de la imagen de producción. No lo importa la aplicación.
"""

import os
import time

from app.application.errors import TemporaryReportError
from app.bootstrap import reports
from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app


class AlwaysTemporaryGenerator:
    def generate(self, report_id, attempt):
        time.sleep(0.4)  # Permite observar processing por HTTP durante el fallo.
        raise TemporaryReportError


class PermanentFailureGenerator:
    def generate(self, report_id, attempt):
        time.sleep(0.4)
        raise RuntimeError("test-only generator failure; never expose this detail")


kind = os.environ["TEST_REPORT_FAILURE"]
generator = {
    "temporary": AlwaysTemporaryGenerator,
    "permanent": PermanentFailureGenerator,
}[kind]
# Conserva scope, repositorio, storage, tarea y configuración reales.
reports.CSVReportGenerator = lambda simulate: generator()
app = create_celery_app(Settings())
