# Celery y FastAPI

Laboratorio educativo de reportes asíncronos. La tarea 1 prepara un entorno
reproducible con FastAPI, un worker Celery, RabbitMQ y PostgreSQL. La API ofrece
`GET /health` y la documentación en `/docs`; los endpoints de reportes,
repositorios, migraciones y tareas de generación se implementarán después.

El diseño y los escenarios están en
[OpenSpec](openspec/changes/async-report-generation/design.md). El progreso se
registra en su [checklist](openspec/changes/async-report-generation/tasks.md).

## Requisitos

- Docker Engine o Docker Desktop **iniciado**, con Docker Compose v2.17 o posterior.
- Puertos locales `8000` y `15672` libres; se pueden cambiar en `.env`.
- Para desarrollar y ejecutar pruebas en el host: [uv](https://docs.astral.sh/uv/),
  versión 0.12.22 o posterior. `uv sync` obtiene Python 3.12 si no está instalado.

Si Docker no responde, verifica `docker version` y `docker context ls`. En Docker
Desktop puede ser necesario seleccionar el contexto con
`docker context use desktop-linux`. Esto cambia el contexto de tu CLI.

## Arranque desde cero

Desde la raíz del repositorio:

```bash
cp .env.example .env
docker compose config --quiet
docker compose build api
docker compose up -d --no-build
docker compose ps
```

API y worker comparten la imagen construida con `build api`. Compose espera los
healthchecks de PostgreSQL y RabbitMQ antes de iniciar esos dos procesos. El
worker puede tardar unos segundos adicionales en aparecer como saludable.

Comprueba el proceso HTTP y el consumidor:

```bash
curl --fail http://localhost:8000/health
docker compose exec -T worker celery -A app.presentation.worker:app inspect ping --timeout 5
```

Las respuestas esperadas son `{"status":"ok"}` y un `pong` del worker. El
endpoint `/health` comprueba el proceso de API, no la disponibilidad de la base
de datos ni del broker. Los healthchecks de cada servicio se muestran en `ps`.

- API y Swagger: <http://localhost:8000/docs>.
- Administración de RabbitMQ: <http://localhost:15672>.
- Usuario de RabbitMQ: `reports`; contraseña inicial de ejemplo:
  `reports-broker-local-only`.

Los puertos publicados están vinculados a `127.0.0.1`. PostgreSQL y AMQP quedan
en la red interna de Compose. Esta entrega todavía no necesita migraciones:
no crea tablas de negocio ni registra tareas de generación.

## Configuración

`.env` está excluido de Git y de la imagen. `.env.example` contiene valores
explícitos del laboratorio local y no credenciales de un entorno real.

| Variable | Uso |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Inicialización de PostgreSQL. |
| `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `RABBITMQ_DEFAULT_VHOST` | Usuario y vhost locales; no se utiliza `guest`. |
| `DATABASE_URL` | URL `postgresql+psycopg://…` con nombre de base de datos. |
| `BROKER_URL` | URL `amqp://…` o `amqps://…` con host. |
| `REPORTS_DIR` | Ruta absoluta dentro del contenedor; conservar `/app/reports` para el volumen incluido. |
| `SIMULATE_TRANSIENT_FAILURE` | Booleano; `true` prepara la demostración futura del fallo inicial y reintento. Todavía no provoca fallos. |
| `API_PORT`, `RABBITMQ_MANAGEMENT_PORT` | Puertos del host; por defecto `8000` y `15672`. |

Las URLs del ejemplo se interpolan a partir de las variables anteriores. Los
valores iniciales usan caracteres que no necesitan escape de URL; si cambias
credenciales por valores con caracteres reservados, codifícalos en las URLs.
Las variables de entorno tienen prioridad sobre `.env`. Pydantic Settings
valida las conexiones, el booleano y la ruta antes de crear las entradas.

`postgres` y `rabbitmq` son nombres DNS internos de Compose. Las URLs del ejemplo
no sirven para conectar desde Python en el host. Las pruebas unitarias no
requieren esos servicios ni utilizan las credenciales del ejemplo.

Cambiar las credenciales de inicialización no modifica los usuarios de un
volumen existente. Para reiniciar los datos del laboratorio usa el procedimiento
de eliminación de volúmenes descrito abajo.

## Desarrollo y pruebas

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

`uv.lock` fija las versiones resueltas para host e imagen. La imagen instala
solo dependencias de ejecución y utiliza un usuario sin privilegios. Para
validar el SDD, con OpenSpec instalado:

```bash
openspec validate async-report-generation --strict --no-interactive
```

La estructura inicial separa `presentation`, `application`, `domain`,
`infrastructure` y `bootstrap`. Las entradas mínimas se construyen en
`bootstrap`; no hay conexiones SQL globales ni lógica de reportes en ellas.

## Logs y almacenamiento

```bash
docker compose logs --tail=100 api worker
docker compose logs --tail=100 postgres rabbitmq
```

El volumen `reports` se monta en `/app/reports` tanto en API como en worker.
Los datos de PostgreSQL y RabbitMQ tienen sus propios volúmenes persistentes.
Los nombres físicos llevan el prefijo del proyecto Compose.

Para aplicar cambios de código, reconstruye la imagen y recrea los procesos:

```bash
docker compose build api
docker compose up -d --no-build
```

## Detener o reiniciar el laboratorio

Detener contenedores **conservando** base de datos, cola y archivos:

```bash
docker compose down
```

Volver a iniciar con esos datos:

```bash
docker compose up -d --no-build
```

Eliminar los contenedores **y todos los datos de los volúmenes de este
laboratorio** para empezar de nuevo:

```bash
docker compose down --volumes
docker compose up -d --no-build
```

Compose mantiene separada esta eliminación de la parada habitual. Redis no
forma parte de este entorno: el seguimiento de reportes irá en PostgreSQL.
