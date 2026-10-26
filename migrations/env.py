"""Alembic obtiene DATABASE_URL del entorno, sin interpolación ConfigParser."""

import os

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

from app.infrastructure.persistence import ReportTable  # noqa: F401

target_metadata = SQLModel.metadata
url = os.environ["DATABASE_URL"]

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
