"""Configuración validada compartida por las entradas HTTP y Celery."""

from pathlib import Path
from typing import Annotated

from pydantic import AmqpDsn, Field, PostgresDsn, UrlConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DatabaseUrl = Annotated[
    PostgresDsn, UrlConstraints(allowed_schemes=["postgresql+psycopg"])
]
BrokerUrl = Annotated[AmqpDsn, UrlConstraints(host_required=True)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # El mismo .env contiene variables de los servicios Docker.
        hide_input_in_errors=True,
    )

    database_url: DatabaseUrl = Field(repr=False)
    broker_url: BrokerUrl = Field(repr=False)
    reports_dir: Path = Path("/app/reports")
    simulate_transient_failure: bool = True

    @field_validator("database_url")
    @classmethod
    def require_database_name(cls, value: PostgresDsn) -> PostgresDsn:
        if not value.path or value.path == "/":
            raise ValueError("DATABASE_URL debe incluir el nombre de la base de datos")
        return value

    @field_validator("reports_dir")
    @classmethod
    def require_absolute_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("REPORTS_DIR debe ser una ruta absoluta")
        return value
