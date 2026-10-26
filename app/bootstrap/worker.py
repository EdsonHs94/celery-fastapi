"""Construcción mínima de Celery; las tareas se incorporarán después."""

from celery import Celery, signals

from app.bootstrap.config import Settings
from app.bootstrap.database import Database


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
    database = Database(str(settings.database_url))
    application.database = database

    # Referencias fuertes: las funciones locales deben sobrevivir al factory.
    def process_init(**kwargs):
        database.reset_after_fork()

    def process_shutdown(**kwargs):
        database.dispose()

    signals.worker_process_init.connect(process_init, weak=False)
    signals.worker_process_shutdown.connect(process_shutdown, weak=False)
    signals.worker_shutdown.connect(process_shutdown, weak=False)
    return application
