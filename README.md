# Celery y FastAPI

Laboratorio educativo de reportes asíncronos. La tarea 1 prepara un entorno
reproducible con FastAPI, un worker Celery, RabbitMQ y PostgreSQL. La tarea 2
implementa el dominio y los casos de uso con contratos y pruebas unitarias. La
API ofrece `GET /health` y la documentación en `/docs`; los endpoints de
reportes, adaptadores SQL/CSV/archivos y tareas Celery de generación se
implementarán después.

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

## Capas y casos de uso

El [diseño vinculante](openspec/changes/async-report-generation/design.md)
describe las dependencias y los límites de este laboratorio:

| Capa | Responsabilidad y dependencias |
|---|---|
| `domain` | `Report` inmutable, `ReportStatus`, transiciones, fechas UTC y presupuesto de tres intentos; solo biblioteca estándar. |
| `application` | `RequestReport`, `GetReport`, `GenerateReport`, errores públicos y contratos; depende de dominio y biblioteca estándar. |
| `presentation` | Transporte HTTP y tareas Celery; delegará en aplicación. |
| `infrastructure` | Implementará los contratos con SQLModel, publicación Celery, generación CSV y archivos. |
| `bootstrap` | Configuración y construcción de dependencias concretas para API y worker. |

Los casos de uso reciben sus dependencias por constructor, sin contenedor DI.
Los contratos síncronos están en `app/application/ports.py`:

- `ReportRepository`: `get(UUID) -> Report | None`, `add(Report)`, `save(Report)`
  y `transaction() -> ContextManager[None]`.
- `ReportTaskPublisher`: `publish(UUID) -> None`.
- `ReportGenerator`: `generate(UUID, attempt) -> bytes`.
- `ReportStorage`: `write(UUID, bytes) -> str`, una referencia relativa.

`RequestReport.execute()` crea y confirma `pending` antes de publicar y devuelve
solo el UUID, pues el worker puede avanzar antes de retornar. Si falla la
persistencia, no publica y lanza `PersistenceUnavailable`. Si falla publicar,
intenta registrar `failed` con `publication_failed` y lanza `PublicationFailed`
con `report_id`, aunque la compensación también falle. Nunca sobrescribe un
estado terminal que haya observado al releer el registro.

`GetReport.execute(UUID)` devuelve el reporte persistido de cualquier estado;
si no existe, lanza `ReportNotFound` sin escribir ni encargar trabajo.

`GenerateReport.execute(UUID)` confirma primero `processing` e incrementa los
intentos. Genera y almacena fuera de la transacción; después relee y confirma
el resultado. Conserva `started_at` del primer intento, asigna `finished_at`
al terminar y guarda `file_ref` solo tras escritura exitosa. Un
`TemporaryReportError` registra `retrying` con `temporary_failure` y se propaga
solo después del commit y mientras queden intentos. El tercer fallo temporal
retorna `failed` con `attempts_exhausted`; un error permanente retorna `failed`
con `generation_failed`. Al completar limpia el error temporal. Aplicación no
programa reintentos ni esperas: el adaptador Celery futuro traducirá esa señal.

Los reportes `completed` y `failed` se omiten sin alterar archivo, intentos ni
fechas. Una entrega en `processing` también se omite; no recupera un intento
interrumpido. La relectura antes de guardar evita reemplazar un terminal ya
observado, pero no ofrece exclusión entre consumidores concurrentes. El diseño
limita el laboratorio a un consumidor; no promete ejecución exactamente una
vez, outbox ni recuperación automática. Una caída entre commit y publicación
puede dejar `pending`; un archivo escrito cuyo commit final falla puede quedar
sin estado completado. Los errores de persistencia se traducen a
`PersistenceUnavailable` sin mensajes privados del adaptador.

Todas las lecturas y escrituras usan `repository.transaction()`, que debe
confirmar o revertir al salir; el repositorio no confirma cada operación por
su cuenta. Las entidades separadas del ORM y sus reemplazos requieren un
`save` explícito. Los fakes de pruebas usan copias y rollback, y la prueba de
arquitectura restringe las importaciones de las dos capas internas. No hay
conexiones SQL globales ni adaptadores reales de reportes en esta entrega.

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
