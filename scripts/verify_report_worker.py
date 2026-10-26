#!/usr/bin/env python3
"""Recorrido real aislado; requiere Docker/Compose, no dependencias del host."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

PUBLISH = r"""
import base64
import json
import logging
import os
import sys
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

from app.application.use_cases import GetReport, RequestReport
from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app
from app.infrastructure.celery_publisher import CeleryReportTaskPublisher
from app.infrastructure.persistence import SQLModelReportRepository

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
app = create_celery_app(Settings())
try:
    with app.database.session() as session:
        repository = SQLModelReportRepository(session)
        report_id = RequestReport(repository, CeleryReportTaskPublisher(app)).execute()
        report = GetReport(repository).execute(report_id)
    assert report.status == "pending" and report.attempts == 0, report
    broker = urlsplit(os.environ["BROKER_URL"])
    credentials = base64.b64encode(
        f"{unquote(broker.username)}:{unquote(broker.password)}".encode()
    ).decode()
    endpoint = (
        "http://rabbitmq:15672/api/queues/"
        + quote(unquote(broker.path[1:]), safe="")
        + "/reports"
    )
    headers = {
        "Authorization": "Basic " + credentials,
        "Content-Type": "application/json",
    }
    with urlopen(Request(endpoint, headers=headers), timeout=5) as response:
        queue = json.load(response)
    assert queue["durable"] is True
    body = json.dumps(
        {"count": 1, "ackmode": "ack_requeue_true", "encoding": "auto"}
    ).encode()
    with urlopen(
        Request(endpoint + "/get", data=body, headers=headers), timeout=5
    ) as response:
        messages = json.load(response)
    assert len(messages) == 1, messages
    message = messages[0]
    assert message["properties"]["content_type"] == "application/json"
    assert message["properties"]["delivery_mode"] == 2
    assert message["properties"]["headers"]["task"] == "reports.generate"
    args, kwargs, embed = json.loads(message["payload"])
    assert args == [str(report_id)] and kwargs == {}, (args, kwargs)
    print(
        json.dumps(
            {
                "report_id": str(report_id),
                "status": report.status,
                "attempts": report.attempts,
                "queued_json_uuid": True,
            }
        ),
        flush=True,
    )
finally:
    app.database.dispose()
    app.close()
"""

MONITOR = r"""
import csv
import io
import json
import sys
import time
from uuid import UUID

from app.application.use_cases import GetReport
from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app
from app.infrastructure.persistence import SQLModelReportRepository
from app.infrastructure.report_storage import LocalReportStorage

settings = Settings()
app = create_celery_app(settings)
report_id = UUID(sys.argv[1])
seen_retrying = False
deadline = time.monotonic() + 90
try:
    while time.monotonic() < deadline:
        with app.database.session() as session:
            report = GetReport(SQLModelReportRepository(session)).execute(report_id)
        if report.status == "retrying":
            assert report.attempts == 1
            if not seen_retrying:
                print(
                    json.dumps(
                        {
                            "report_id": str(report_id),
                            "status": "retrying",
                            "attempts": 1,
                        }
                    ),
                    flush=True,
                )
            seen_retrying = True
        if report.status == "completed":
            assert seen_retrying, "No se observó retrying antes de completar"
            assert report.attempts == 2
            elapsed = (report.finished_at - report.started_at).total_seconds()
            assert elapsed >= 5, elapsed
            path = LocalReportStorage(settings.reports_dir).resolve(report.file_ref)
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
            assert len(rows) == 100
            assert rows[0]["sale_id"] == "1" and rows[-1]["sale_id"] == "100"
            print(
                json.dumps(
                    {
                        "report_id": str(report_id),
                        "status": "completed",
                        "attempts": 2,
                        "elapsed_seconds": elapsed,
                        "csv_rows": len(rows),
                    }
                ),
                flush=True,
            )
            break
        assert report.status != "failed", report.error_code
        time.sleep(0.1)
    else:
        raise AssertionError("Timeout esperando completed")
finally:
    app.database.dispose()
    app.close()
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context", help="Contexto Docker explícito, por ejemplo desktop-linux"
    )
    args = parser.parse_args()
    project = f"reports-task5-{uuid4().hex[:12]}"
    image = f"{project}:local"
    docker = ["docker"] + (["--context", args.context] if args.context else [])
    environment = os.environ.copy()
    # Compose no hereda nombres/profiles de otros proyectos del usuario.
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        path = Path(directory)
        password = uuid4().hex
        app_env = {
            "DATABASE_URL": f"postgresql+psycopg://reports:{password}@postgres/reports_test",
            "BROKER_URL": f"amqp://reports:{password}@rabbitmq/reports",
            "REPORTS_DIR": "/app/reports",
            "SIMULATE_TRANSIENT_FAILURE": "true",
        }
        application = {
            "image": image,
            "environment": app_env,
            "volumes": ["reports:/app/reports"],
            "init": True,
        }
        compose = {
            "services": {
                "postgres": {
                    "image": "postgres:16.13-bookworm",
                    "environment": {
                        "POSTGRES_USER": "reports",
                        "POSTGRES_PASSWORD": password,
                        "POSTGRES_DB": "reports_test",
                    },
                    "volumes": ["postgres-data:/var/lib/postgresql/data"],
                    "healthcheck": {
                        "test": ["CMD-SHELL", "pg_isready -U reports -d reports_test"],
                        "interval": "1s",
                        "timeout": "5s",
                        "retries": 60,
                    },
                },
                "rabbitmq": {
                    "image": "rabbitmq:4.1.8-management",
                    "hostname": "rabbitmq",
                    "environment": {
                        "RABBITMQ_DEFAULT_USER": "reports",
                        "RABBITMQ_DEFAULT_PASS": password,
                        "RABBITMQ_DEFAULT_VHOST": "reports",
                    },
                    "volumes": ["rabbitmq-data:/var/lib/rabbitmq"],
                    "healthcheck": {
                        "test": ["CMD", "rabbitmq-diagnostics", "-q", "ping"],
                        "interval": "2s",
                        "timeout": "5s",
                        "retries": 60,
                    },
                },
                "api": {**application, "build": {"context": str(ROOT)}},
                "migrate": {**application, "command": ["alembic", "upgrade", "head"]},
                "worker": {
                    **application,
                    "command": [
                        "celery",
                        "-A",
                        "app.presentation.worker:app",
                        "worker",
                        "--loglevel=INFO",
                        "--concurrency=1",
                        "--prefetch-multiplier=1",
                    ],
                },
            },
            "volumes": {"reports": {}, "postgres-data": {}, "rabbitmq-data": {}},
        }
        definition = path / "compose.json"
        definition.write_text(json.dumps(compose))
        env_file = path / "empty.env"
        env_file.write_text("")
        command = docker + [
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(definition),
            "-p",
            project,
        ]

        def run(
            *arguments: str, source: str | None = None, capture: bool = False
        ) -> str:
            result = subprocess.run(
                command + list(arguments),
                input=source,
                text=True,
                capture_output=capture,
                env=environment,
                check=True,
            )
            return result.stdout if capture else ""

        try:
            print(f"Proyecto aislado: {project}", flush=True)
            run("build", "api")
            run("up", "-d", "--wait", "--wait-timeout", "180", "postgres", "rabbitmq")
            run("run", "--rm", "--no-deps", "migrate")
            # El worker todavía no existe: el mensaje debe permanecer en RabbitMQ.
            published = run(
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "api",
                "python",
                "-u",
                "-",
                source=PUBLISH,
                capture=True,
            )
            print(published, end="", flush=True)
            record = json.loads(published.strip().splitlines()[-1])
            assert f"report_id={record['report_id']} stage=published" in published
            report_id = record["report_id"]
            # Sin --wait: observar retrying antes de que pasen los cinco segundos.
            run("up", "-d", "--no-build", "worker")
            run(
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "api",
                "python",
                "-u",
                "-",
                report_id,
                source=MONITOR,
            )
            logs = run("logs", "--no-color", "worker", capture=True)
            for stage in (
                "received",
                "processing",
                "retrying",
                "retry_scheduled",
                "completed",
            ):
                assert any(
                    report_id in line and f"stage={stage}" in line
                    for line in logs.splitlines()
                ), stage
            # Publicación ocurre en el proceso cliente, no en logs del worker.
            print(
                f"report_id={report_id} "
                "verified=publication,receipt,retry,completion,csv",
                flush=True,
            )
        finally:
            # Sólo recursos del proyecto único creado por esta ejecución.
            subprocess.run(
                command + ["down", "--volumes", "--remove-orphans"],
                env=environment,
                check=False,
            )
            subprocess.run(
                docker + ["image", "rm", image], env=environment, check=False
            )


if __name__ == "__main__":
    main()
