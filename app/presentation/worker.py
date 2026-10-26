"""Entrada CLI del worker, sin tareas de negocio registradas todavía."""

from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app

app = create_celery_app(Settings())
