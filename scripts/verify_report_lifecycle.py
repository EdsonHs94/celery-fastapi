#!/usr/bin/env python3
"""Verifica HTTP + PostgreSQL + RabbitMQ + worker prefork en Compose aislado."""

import argparse
import csv
import io
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FIELDS = {
    "id",
    "status",
    "attempts",
    "created_at",
    "started_at",
    "finished_at",
    "download_url",
    "error_code",
}


class LifecycleVerification:
    def __init__(self, command, environment):
        self.command = command
        self.environment = environment
        self.base_url = ""

    def run(self, *arguments, source=None, timeout=180):
        result = subprocess.run(
            self.command + list(arguments),
            input=source,
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Compose {arguments} falló ({result.returncode}):\n"
                + result.stdout
                + result.stderr
            )
        return result.stdout

    def python(self, source, *arguments):
        return self.run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            *arguments,
            source=source,
        ).strip()

    def http(self, path, expected=200, body=None, method="GET"):
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            assert response.status == expected, (path, response.status, content)
            return content, response.headers

    def status(self, identifier):
        content, _ = self.http(f"/reports/{identifier}")
        record = json.loads(content)
        assert set(record) == PUBLIC_FIELDS, record
        return record

    def request_report(self):
        content, _ = self.http("/reports", 202, b"{}", "POST")
        accepted = json.loads(content)
        identifier = str(UUID(accepted["id"]))
        assert accepted == {
            "id": identifier,
            "status_url": f"/reports/{identifier}",
            "download_url": f"/reports/{identifier}/download",
        }, accepted
        return identifier

    def reject_download(self, identifier):
        content, _ = self.http(f"/reports/{identifier}/download", 409)
        assert json.loads(content)["detail"]["code"] == "report_not_ready"

    def monitor(self, identifier, terminal, attempts, retry_attempts=()):
        deadline = time.monotonic() + 90
        observed_retries = set()
        previous = None
        while time.monotonic() < deadline:
            record = self.status(identifier)
            state = record["status"], record["attempts"]
            if state != previous:
                print(json.dumps({"report_id": identifier, **record}), flush=True)
                previous = state
            if record["status"] == "retrying":
                observed_retries.add(record["attempts"])
            if record["status"] in {"pending", "processing", "retrying", "failed"}:
                assert record["download_url"] is None
                if record["status"] == "failed":
                    self.reject_download(identifier)
            if record["status"] in {"completed", "failed"}:
                assert record["status"] == terminal, record
                assert record["attempts"] == attempts, record
                assert record["finished_at"] and record["started_at"]
                assert observed_retries == set(retry_attempts), observed_retries
                elapsed = (
                    datetime.fromisoformat(record["finished_at"])
                    - datetime.fromisoformat(record["started_at"])
                ).total_seconds()
                assert elapsed >= 5 * len(retry_attempts), elapsed
                return record
            time.sleep(0.05)
        raise AssertionError(f"Timeout de estado: {identifier}; último={previous}")

    def download(self, identifier):
        content, headers = self.http(f"/reports/{identifier}/download")
        assert headers["Content-Type"].startswith("text/csv")
        assert headers["Content-Disposition"] == (
            f'attachment; filename="report-{identifier}.csv"'
        )
        text = content.decode("utf-8")
        assert text.splitlines()[0] == "sale_id,customer,total,currency"
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 100
        for number, row in enumerate(rows, 1):
            assert row == {
                "sale_id": str(number),
                "customer": f"Cliente {number:03}",
                "total": f"{Decimal(number) * Decimal('10.00'):.2f}",
                "currency": "PEN",
            }, row
        return content

    def logs(self, service="worker"):
        return self.run("logs", "--no-color", service)

    def assert_stages(self, identifier, stages, service="worker"):
        logs = self.logs(service)
        for stage in stages:
            assert any(
                f"report_id={identifier}" in line and f"stage={stage}" in line
                for line in logs.splitlines()
            ), (identifier, stage, logs)
        return logs

    def file_state(self, identifier):
        return json.loads(
            self.python(
                "import json, pathlib, sys\n"
                "p = pathlib.Path('/app/reports') / f'report-{sys.argv[1]}.csv'\n"
                "print(json.dumps({'exists': p.exists(), "
                "'mtime_ns': p.stat().st_mtime_ns if p.exists() else None}))\n",
                identifier,
            )
        )

    def check_no_retry(self, identifier, attempts, schedules):
        # Supera un countdown completo: un cuarto intento o retry permanente falla.
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            record = self.status(identifier)
            assert record["status"] == "failed" and record["attempts"] == attempts
            time.sleep(0.1)
        logs = self.logs()
        lines = [
            line for line in logs.splitlines() if f"report_id={identifier}" in line
        ]
        assert sum("stage=received" in line for line in lines) == attempts, lines
        assert sum("stage=retry_scheduled" in line for line in lines) == schedules
        assert not self.file_state(identifier)["exists"]

    def verify(self, start_worker):
        # Sin esquema antes de migrar; el proyecto y sus volúmenes son nuevos.
        tables = self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "reports",
            "-d",
            "reports_test",
            "-Atc",
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public'",
        )
        assert tables.strip() == "0", tables
        self.run("run", "--rm", "--no-deps", "migrate")
        self.run("up", "-d", "--no-build", "api")
        address = self.run("port", "api", "8000").strip()
        assert address.startswith("127.0.0.1:"), address
        self.base_url = "http://" + address
        deadline = time.monotonic() + 60
        while True:
            try:
                self.http("/health")
                break
            except (URLError, TimeoutError, ConnectionError):
                assert time.monotonic() < deadline, "API no arrancó"
                time.sleep(0.2)
        for body in (b"null", b"[]", b'{"filter":"unused"}', b""):
            self.http("/reports", 422, body, "POST")
        for identifier, code in ((str(uuid4()), 404), ("invalid-uuid", 422)):
            for suffix in ("", "/download"):
                self.http(f"/reports/{identifier}{suffix}", code)
        count = self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "reports",
            "-d",
            "reports_test",
            "-Atc",
            "SELECT count(*) FROM reports",
        )
        assert count.strip() == "0", count
        first = self.request_report()
        pending = self.status(first)
        assert pending["status"] == "pending" and pending["attempts"] == 0
        assert pending["started_at"] is None and pending["finished_at"] is None
        self.reject_download(first)
        start_worker("true")
        self.monitor(first, "completed", 2, (1,))
        csv_first = self.download(first)
        self.assert_stages(first, ("published",), "api")
        self.assert_stages(
            first,
            (
                "received",
                "processing",
                "retrying",
                "retry_scheduled",
                "completed",
            ),
        )
        print("PASS simulation=true completed/2 CSV=100", flush=True)

        start_worker("false")
        second = self.request_report()
        completed = self.monitor(second, "completed", 1)
        assert self.download(second) == csv_first
        before_file = self.file_state(second)
        succeeded = f"Task reports.generate[{second}] succeeded"
        deadline = time.monotonic() + 30
        while True:
            before_logs = self.logs()
            if before_logs.count(succeeded) >= 1:
                break
            assert time.monotonic() < deadline, "No finalizó el primer consumo"
            time.sleep(0.1)
        baseline_successes = before_logs.count(succeeded)
        baseline_receipts = sum(
            f"report_id={second} stage=received" in line
            for line in before_logs.splitlines()
        )
        assert baseline_successes == 1 and baseline_receipts == 1, before_logs
        self.python(
            "import sys\nfrom uuid import UUID\n"
            "from app.bootstrap.config import Settings\n"
            "from app.bootstrap.worker import create_celery_app\n"
            "from app.infrastructure.celery_publisher import (\n"
            " CeleryReportTaskPublisher)\n"
            "app = create_celery_app(Settings())\n"
            "try:\n CeleryReportTaskPublisher(app).publish(UUID(sys.argv[1]))\n"
            "finally:\n app.database.dispose()\n app.close()\n",
            second,
        )
        deadline = time.monotonic() + 30
        while True:
            logs = self.logs()
            receipts = sum(
                f"report_id={second} stage=received" in line
                for line in logs.splitlines()
            )
            if (
                logs.count(succeeded) > baseline_successes
                and receipts > baseline_receipts
            ):
                break
            assert time.monotonic() < deadline, "No finalizó la reentrega"
            time.sleep(0.1)
        assert self.status(second) == completed
        assert self.file_state(second) == before_file
        assert self.download(second) == csv_first
        self.assert_stages(second, ("received", "completed"))
        print(
            "PASS simulation=false completed/1; redelivery consumed+finished unchanged",
            flush=True,
        )

        for kind, attempts, code, retries in (
            ("temporary", 3, "attempts_exhausted", (1, 2)),
            ("permanent", 1, "generation_failed", ()),
        ):
            start_worker("false", kind)
            identifier = self.request_report()
            failed = self.monitor(identifier, "failed", attempts, retries)
            assert failed["error_code"] == code, failed
            self.check_no_retry(identifier, attempts, len(retries))
            self.assert_stages(identifier, ("received", "failed"))
            print(
                f"PASS {kind} failed/{attempts} {code} download=409 nofile", flush=True
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", help="Contexto Docker, sin cambiar el global")
    args = parser.parse_args()
    project = "reports-task7-" + uuid4().hex[:12]
    image = project + ":local"
    docker = ["docker"] + (["--context", args.context] if args.context else [])
    environment = os.environ.copy()
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        environment.pop(key, None)
    password = uuid4().hex
    settings = {
        "POSTGRES_USER": "reports",
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": "reports_test",
        "RABBITMQ_DEFAULT_USER": "reports",
        "RABBITMQ_DEFAULT_PASS": password,
        "RABBITMQ_DEFAULT_VHOST": "reports",
        "DATABASE_URL": f"postgresql+psycopg://reports:{password}@postgres/reports_test",
        "BROKER_URL": f"amqp://reports:{password}@rabbitmq/reports",
        "REPORTS_DIR": "/app/reports",
        "SIMULATE_TRANSIENT_FAILURE": "true",
        "API_PORT": "0",
        "RABBITMQ_MANAGEMENT_PORT": "0",
    }
    environment.update(settings)  # El entorno del usuario no gana a este laboratorio.
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        directory = Path(temporary)
        env_file = directory / "isolated.env"
        env_file.write_text("\n".join(f"{k}={v}" for k, v in settings.items()) + "\n")
        override = directory / "override.json"
        definition = {
            "services": {
                name: {"image": image} for name in ("api", "worker", "migrate")
            }
        }
        override.write_text(json.dumps(definition))
        command = docker + [
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            project,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
        ]
        verification = LifecycleVerification(command, environment)

        def start_worker(simulate, failure=None):
            verification.run("stop", "worker")
            worker = definition["services"]["worker"]
            worker["environment"] = {"SIMULATE_TRANSIENT_FAILURE": simulate}
            if failure:
                worker["environment"].update(
                    {
                        "TEST_REPORT_FAILURE": failure,
                        "PYTHONPATH": "/test-fixtures:/app",
                    }
                )
                worker["volumes"] = [
                    {
                        "type": "bind",
                        "source": str(ROOT / "tests/integration/lifecycle"),
                        "target": "/test-fixtures",
                        "read_only": True,
                    }
                ]
                worker["command"] = [
                    "celery",
                    "-A",
                    "fixture_worker:app",
                    "worker",
                    "--loglevel=INFO",
                    "--concurrency=1",
                    "--prefetch-multiplier=1",
                    "--hostname=celery@%h",
                ]
            override.write_text(json.dumps(definition))
            # No --wait: healthcheck 30s perdería la ventana retrying de 5s.
            verification.run(
                "up", "-d", "--no-build", "--no-deps", "--force-recreate", "worker"
            )

        try:
            print(f"Proyecto aislado: {project}", flush=True)
            verification.run("config", "--quiet")
            verification.run("build", "api", timeout=900)
            verification.run(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
                "postgres",
                "rabbitmq",
                timeout=240,
            )
            verification.verify(start_worker)
            print("PASS lifecycle: all four HTTP/Compose cases", flush=True)
        except BaseException:
            # Incluye worker/servicios vigentes para diagnosticar antes de cleanup.
            try:
                print(verification.run("logs", "--no-color", "--tail=150"), flush=True)
            except Exception as error:
                print(f"No se pudieron obtener logs: {error}", flush=True)
            raise
        finally:
            subprocess.run(
                command + ["down", "--volumes", "--remove-orphans"],
                env=environment,
                timeout=180,
                check=False,
            )
            subprocess.run(
                docker + ["image", "rm", image],
                env=environment,
                timeout=60,
                check=False,
            )


if __name__ == "__main__":
    main()
