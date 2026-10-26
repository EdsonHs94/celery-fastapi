"""Contrato del CSV ficticio, independiente del identificador y del entorno."""

import csv
import io
import re
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.errors import TemporaryReportError
from app.infrastructure.csv_report import CSVReportGenerator


def test_csv_has_exact_header_and_100_deterministic_decimal_sales():
    generator = CSVReportGenerator(simulate_transient_failure=False)
    content = generator.generate(uuid4(), 1)
    assert content == generator.generate(uuid4(), 2)
    assert b"\r" not in content
    assert content.endswith(b"\n")
    text = content.decode("utf-8")
    assert text.splitlines()[0] == "sale_id,customer,total,currency"
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 100
    for n, row in enumerate(rows, 1):
        assert row == {
            "sale_id": str(n),
            "customer": f"Cliente {n:03d}",
            "total": format(Decimal(n) * Decimal("10.00"), ".2f"),
            "currency": "PEN",
        }
        assert re.fullmatch(r"\d+\.\d{2}", row["total"])
    assert text.splitlines()[1] == "1,Cliente 001,10.00,PEN"
    assert text.splitlines()[-1] == "100,Cliente 100,1000.00,PEN"


def test_simulation_fails_before_csv_serialization(monkeypatch):
    def unexpected_writer(*args, **kwargs):
        pytest.fail("El primer intento no debe empezar a producir el CSV")

    with monkeypatch.context() as patch:
        patch.setattr(csv, "writer", unexpected_writer)
        with pytest.raises(TemporaryReportError):
            CSVReportGenerator(simulate_transient_failure=True).generate(uuid4(), 1)

    assert CSVReportGenerator(simulate_transient_failure=True).generate(uuid4(), 2)


def test_disabled_simulation_succeeds_on_first_attempt():
    assert CSVReportGenerator(simulate_transient_failure=False).generate(uuid4(), 1)
