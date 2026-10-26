"""Configuración RabbitMQ y recursos perezosos del worker por proceso."""

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
        worker_concurrency=1,
        broker_connection_timeout=5,
        broker_pool_limit=0,
        broker_transport_options={
            "confirm_publish": True,
            "read_timeout": 5,
            "write_timeout": 5,
            "max_retries": 0,
        },
        task_publish_retry=False,
        task_default_delivery_mode="persistent",
        timezone="UTC",
        enable_utc=True,
    )
    database = Database(str(settings.database_url))
    application.database = database
    application.report_settings = settings
    from app.presentation.report_task import register_report_task

    register_report_task(application)

    # Referencias fuertes: las funciones locales deben sobrevivir al factory.
    def process_init(**kwargs):
        database.reset_after_fork()

    def process_shutdown(**kwargs):
        database.dispose()

    signals.worker_process_init.connect(process_init, weak=False)
    signals.worker_process_shutdown.connect(process_shutdown, weak=False)
    signals.worker_shutdown.connect(process_shutdown, weak=False)
    return application
