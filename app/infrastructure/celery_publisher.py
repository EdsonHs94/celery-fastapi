"""Publicación Celery sin esperar ejecución ni adquirir un productor del pool."""

import logging
from uuid import UUID

from celery import Celery
from kombu import Producer

TASK_NAME = "reports.generate"
logger = logging.getLogger(__name__)


class CeleryReportTaskPublisher:
    def __init__(self, app: Celery) -> None:
        self.app = app

    def publish(self, report_id: UUID) -> None:
        logger.info("report_id=%s stage=publishing", report_id)
        try:
            with self.app.connection_for_write() as connection:
                connection.ensure_connection(max_retries=0)
                producer = Producer(connection)
                self.app.send_task(
                    TASK_NAME,
                    args=[str(report_id)],
                    kwargs={},
                    task_id=str(report_id),
                    queue="reports",
                    serializer="json",
                    delivery_mode=2,
                    ignore_result=True,
                    producer=producer,
                    retry=False,
                    timeout=5,
                    confirm_timeout=5,
                )
        except Exception:
            logger.error("report_id=%s stage=publication_failed", report_id)
            raise
        logger.info("report_id=%s stage=published", report_id)
