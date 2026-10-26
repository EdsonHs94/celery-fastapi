"""Generación del CSV de ventas ficticias del laboratorio."""

import csv
from decimal import Decimal
from io import StringIO
from uuid import UUID

from app.application.errors import TemporaryReportError


class CSVReportGenerator:
    def __init__(self, simulate_transient_failure: bool) -> None:
        self.simulate_transient_failure = simulate_transient_failure

    def generate(self, report_id: UUID, attempt: int) -> bytes:
        if self.simulate_transient_failure and attempt == 1:
            # Fallo intencional para demostrar los reintentos de Celery. La primera
            # ejecución falla y el siguiente intento continúa normalmente. Esta
            # simulación es educativa y debe desactivarse fuera de la prueba.
            raise TemporaryReportError()

        output = StringIO(newline="")
        writer = csv.writer(output, delimiter=",", lineterminator="\n")
        writer.writerow(("sale_id", "customer", "total", "currency"))
        for n in range(1, 101):
            total = Decimal(n) * Decimal("10.00")
            writer.writerow((n, f"Cliente {n:03d}", format(total, ".2f"), "PEN"))
        return output.getvalue().encode("utf-8")
