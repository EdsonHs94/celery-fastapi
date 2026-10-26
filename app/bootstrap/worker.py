"""Construcción mínima de Celery; las tareas se incorporarán después."""

from celery import Celery

from app.bootstrap.config import Settings


def create_celery_app(settings: Settings) -> Celery:
    application = Celery("reports", broker=str(settings.broker_url))
    application.conf.update(
        accept_content=["json"],
        task_serializer="json",
        task_ignore_result=True,
        task_default_queue="reports",
        broker_connection_retry_on_startup=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        enable_utc=True,
    )
    return application
