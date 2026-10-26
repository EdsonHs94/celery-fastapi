"""Traduce únicamente el fallo temporal de aplicación al protocolo retry."""

import logging
from uuid import UUID

from celery import Celery
from celery.exceptions import Retry

from app.application.errors import TemporaryReportError
from app.bootstrap import reports
from app.infrastructure.celery_publisher import TASK_NAME

logger = logging.getLogger(__name__)


def register_report_task(app: Celery):
    @app.task(bind=True, name=TASK_NAME, ignore_result=True, max_retries=2)
    def generate_report(task, report_id: str) -> None:
        identifier = UUID(report_id)
        logger.info("report_id=%s stage=received", identifier)
        with reports.generate_report_scope(app) as use_case:
            try:
                result = use_case.execute(identifier)
            except TemporaryReportError as exc:
                logger.info("report_id=%s stage=retry_scheduling", identifier)
                try:
                    raise task.retry(
                        exc=exc,
                        countdown=5,
                        max_retries=2,
                        retry=False,
                        timeout=5,
                        confirm_timeout=5,
                    )
                except Retry:
                    logger.info("report_id=%s stage=retry_scheduled", identifier)
                    raise
                except Exception:
                    logger.error(
                        "report_id=%s stage=retry_publication_failed", identifier
                    )
                    try:
                        use_case.fail_retry_publication(identifier)
                    except Exception:
                        logger.error(
                            "report_id=%s stage=retry_compensation_failed", identifier
                        )
                    raise
            logger.info(
                "report_id=%s stage=%s attempt=%s",
                identifier,
                result.status.value,
                result.attempts,
            )

    return generate_report
