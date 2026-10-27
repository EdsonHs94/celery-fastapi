"""La configuración normal Uvicorn debe mostrar el seguimiento de aplicación."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("preconfigured", [False, True])
def test_normal_uvicorn_factory_logs_application_info_and_respects_existing_logging(
    preconfigured,
):
    source = """
import logging
import sys
import uvicorn

preconfigured = sys.argv[1] == 'True'
if preconfigured:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('CUSTOM %(message)s'))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.ERROR)
    before_handlers = list(logging.getLogger().handlers)
config = uvicorn.Config('app.bootstrap.api:create_app', factory=True)
config.load()
logger = logging.getLogger('app.infrastructure.celery_publisher')
logger.info('report_id=example stage=published')
if preconfigured:
    assert logging.getLogger().handlers == before_handlers
    assert logging.getLogger().level == logging.ERROR
    logger.error('existing configuration preserved')
"""
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL="postgresql+psycopg://reports:local@unreachable.invalid/reports",
        BROKER_URL="amqp://reports:local@unreachable.invalid/reports",
    )
    result = subprocess.run(
        [sys.executable, "-c", source, str(preconfigured)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    if preconfigured:
        assert "CUSTOM existing configuration preserved" in result.stderr
        assert "stage=published" not in result.stderr
    else:
        assert "report_id=example stage=published" in result.stderr
