"""Entrada CLI del worker con la tarea reports.generate registrada."""

from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app

app = create_celery_app(Settings())
