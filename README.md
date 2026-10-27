# Celery y FastAPI

Laboratorio educativo de reportes asíncronos. La tarea 1 prepara un entorno
reproducible con FastAPI, un worker Celery, RabbitMQ y PostgreSQL. La tarea 2
implementa el dominio y los casos de uso con contratos y pruebas unitarias. La
tarea 3 añade persistencia SQLModel/PostgreSQL, migraciones Alembic y sesiones
por petición/proceso del worker. La tarea 4 incorpora el generador CSV y el
almacenamiento de archivos completos. La tarea 5 conecta publicación y
procesamiento real con Celery/RabbitMQ. La tarea 6 añade aceptación, consulta y
descarga de reportes por HTTP. La API ofrece también `GET /health` y la
documentación OpenAPI en `/docs`.

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
docker compose up -d postgres rabbitmq
docker compose run --rm migrate
docker compose up -d --no-build api worker
docker compose ps
```

API y worker comparten la imagen construida con `build api`. Compose espera los
healthchecks de PostgreSQL y RabbitMQ antes de iniciar esos dos procesos. El
proceso puntual `migrate` espera PostgreSQL y aplica el esquema; ejecútalo antes
de API y worker. Las migraciones no se ejecutan automáticamente al iniciar
ninguno de ellos. El worker puede tardar unos segundos adicionales en aparecer
como saludable.

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
en la red interna de Compose. Esta entrega crea la tabla `reports` mediante
Alembic y registra la tarea `reports.generate`.

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
| `SIMULATE_TRANSIENT_FAILURE` | Booleano; `true` habilita el fallo educativo del primer intento en el generador CSV. Celery programa el segundo intento cinco segundos después. |
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
| `presentation` | Transporte HTTP y tareas Celery; delega en aplicación. |
| `infrastructure` | Implementa persistencia SQLModel, generación CSV y archivos; y publicación Celery. |
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
programa reintentos ni esperas: el adaptador Celery traduce esa señal.

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
conexiones SQL globales abiertas al importar módulos. La persistencia real se
compone desde bootstrap.

## PostgreSQL, migraciones y sesiones

`ReportTable` vive en infraestructura y guarda UUID, estado como cadena,
intentos (por defecto 0), fechas PostgreSQL `timestamp with time zone` y
referencias/errores anulables según el diseño. El adaptador devuelve entidades
`Report` inmutables y separadas del ORM, convirtiendo el estado a `ReportStatus`.
Las conexiones de aplicación fijan UTC. Todas las operaciones exigen
`repository.transaction()`: confirma al salir bien y hace rollback ante errores,
incluido un fallo del commit. Ningún método hace commit por sí mismo. `save`
es explícito y lanza `LookupError` si el UUID ya no existe; `get` devuelve `None`.
No hay transacciones anidadas ni sesiones externas con transacción ya abierta.

La migración inicial reversible está en `migrations/versions/0001_create_reports.py`;
la imagen incluye los scripts y `alembic.ini`. Comandos de mantenimiento:

```bash
docker compose run --rm migrate
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic check
```

Alembic lee `DATABASE_URL` directamente del entorno, sin guardarla en el INI ni
pasarla por la interpolación de `%`. Las contraseñas URL-encoded funcionan.
El comando no requiere `BROKER_URL` fuera de Compose. Para usar PostgreSQL en el
host, configura una URL accesible desde él y ejecuta `uv run --locked alembic
upgrade head`. Para crear una revisión futura usa `alembic revision
--autogenerate -m "descripcion"` y revisa el resultado antes de aplicarlo.
`alembic downgrade base` elimina `reports`: úsalo solo en una base desechable.
`/health`, `/docs` y el arranque de API/worker no crean tablas ni ejecutan DDL.

`Database` en `app/bootstrap/database.py` conserva un engine perezoso por
proceso y crea una sesión síncrona distinta con cada `with database.session()`;
el scope siempre la cierra en éxito y error. La dependencia
`get_report_repository` obtiene ese scope desde `request.app.state.database`
para las rutas de reportes. El lifespan de FastAPI libera el pool y cierra el
cliente Celery propio, si se creó, al terminar. Cada aplicación conserva sus
settings y recursos aislados; crearla no abre conexiones SQL ni AMQP.
Celery conserva el recurso en `app.database`, sin construir engine ni abrir
conexiones en el proceso padre. `worker_process_init` descarta cualquier pool
heredado con `dispose(close=False)`; también hay comprobación defensiva del PID.
Los procesos liberan su engine al recibir las señales de cierre. No hay una
conexión SQL compartida entre API y worker ni se crea engine por consulta.

La composición de la tarea usa el recurso dentro de cada invocación:

```python
with app.database.session() as session:
    repository = SQLModelReportRepository(session)
    # Construir el caso de uso con repository; él delimita transaction().
```

Cada entrega de `reports.generate` construye `GenerateReport` con una sesión
nueva y la cierra también ante error o retry. No mantiene una transacción
durante generación de archivos.

Las pruebas de persistencia requieren `TEST_DATABASE_URL` **explícito** y una
base cuyo nombre termine en `_test`, con driver `postgresql+psycopg`. Sin esa
variable se omiten; nunca usan `DATABASE_URL` como alternativa. Las pruebas
aplican upgrade/downgrade y destruyen sus tablas: usa exclusivamente una base
desechable y ejecútalas en serie. Ejemplo aislado, sin tocar el volumen Compose:

```bash
docker run --rm --name reports-postgres-test -d \
  -e POSTGRES_USER=reports -e POSTGRES_PASSWORD=test-local-only \
  -e POSTGRES_DB=reports_test -p 127.0.0.1:55433:5432 postgres:16.13-bookworm
# Espera a que pg_isready acepte conexiones antes de probar:
docker exec reports-postgres-test pg_isready -U reports -d reports_test
TEST_DATABASE_URL=postgresql+psycopg://reports:test-local-only@127.0.0.1:55433/reports_test \
  uv run --locked pytest -q
docker stop reports-postgres-test
```

La suite verifica el esquema y downgrade real, alta/lectura/actualización,
ausentes, rollback (también al fallar commit), sesiones aisladas y su cierre,
y conexiones distintas tras un fork real. No usa SQLite como sustituto.

## CSV ficticio y archivos completos

`CSVReportGenerator(simulate_transient_failure: bool)` recibe la configuración
por constructor. `generate(report_id, attempt)` devuelve bytes UTF-8 con coma
como delimitador, saltos de línea `\n` y exactamente 100 ventas ficticias.
No consulta ventas reales ni utiliza reloj, aleatoriedad o floats. Para cada
entero `n` del 1 al 100 usa `sale_id=n`, `customer=Cliente NNN`,
`total=Decimal(n) * Decimal("10.00")` con dos decimales y `currency=PEN`.
Dos reportes distintos producen el mismo contenido.

El encabezado y las primeras filas son exactamente:

```csv
sale_id,customer,total,currency
1,Cliente 001,10.00,PEN
2,Cliente 002,20.00,PEN
```

Tras las filas intermedias, la última fila es:

```csv
100,Cliente 100,1000.00,PEN
```

Con `SIMULATE_TRANSIENT_FAILURE=true`, el generador lanza
`TemporaryReportError` cuando `attempt == 1`, antes de serializar el CSV o
escribir archivos. El segundo intento permite producir el contenido. Junto al
punto de fallo se incluye este comentario educativo:

> Fallo intencional para demostrar los reintentos de Celery. La primera ejecución falla y el siguiente intento continúa normalmente. Esta simulación es educativa y debe desactivarse fuera de la prueba.

Para completar desde el primer intento, cambia en `.env`:

```dotenv
SIMULATE_TRANSIENT_FAILURE=false
```

Tras cambiar configuración, recrea el worker para que lea el entorno nuevo:

```bash
docker compose up -d --no-build --force-recreate worker
```

Las pruebas unitarias llaman a `GenerateReport` con adaptadores reales y un
repositorio de prueba. El recorrido real descrito abajo añade PostgreSQL,
RabbitMQ y el worker Celery. La composición pasa
`settings.simulate_transient_failure` al generador y `settings.reports_dir` al
almacenamiento; los adaptadores no leen el entorno.

`LocalReportStorage(reports_dir: Path)` crea el directorio al escribir. Primero
escribe y cierra un temporal único dentro de él, y luego usa sustitución atómica
para publicar `report-{id}.csv`. `write(report_id, content)` devuelve solo ese
nombre relativo tras el reemplazo exitoso. Un fallo intenta limpiar el temporal
y conserva el archivo final anterior completo, si existía. `GenerateReport`
registra `completed` y `file_ref` después de esa escritura.

El método concreto `resolve(file_ref)` devuelve un `Path` solo para nombres
canónicos `report-{uuid}.csv` dentro de la raíz. Rechaza rutas absolutas,
traversal, temporales, directorios y symlinks, incluidos los internos. No recibe
rutas del cliente. Si falta un archivo final, lanza `ReportFileMissing` con
código público `report_file_missing`, traducido a HTTP 410 sin
exponer rutas privadas. El contrato pequeño `ReportFileResolver` permite
inyectar la resolución en la descarga sin acoplarla al adaptador concreto.

El volumen Compose `reports` es compartido por API y worker, ambos en
`/app/reports`, el valor de `REPORTS_DIR`. Así, el worker escribe donde
la ruta de descarga resuelve el archivo. Cambiar esa ruta requiere ajustar el montaje de
ambos servicios. La sustitución evita servir archivos parciales; no se ofrecen
locks entre consumidores ni durabilidad ante caída con `fsync`. El laboratorio
prevé un consumidor.

## API de reportes

Con el laboratorio iniciado, acepta un encargo enviando el objeto JSON vacío
obligatorio. Los campos extra se rechazan:

```bash
API_URL=http://localhost:8000
curl -i -X POST "$API_URL/reports" \
  -H 'Content-Type: application/json' -d '{}'
```

La respuesta es `202 Accepted`, con este formato:

```json
{"id":"6c678f08-5a90-4dc1-b4ae-c5744e249df3","status_url":"/reports/6c678f08-5a90-4dc1-b4ae-c5744e249df3","download_url":"/reports/6c678f08-5a90-4dc1-b4ae-c5744e249df3/download"}
```

Las URLs son relativas al origen de la API. La aceptación confirma el registro
SQL y la publicación, y no promete que la generación haya terminado. El worker
puede avanzar antes de recibir la respuesta; por eso el `202` no incluye estado.
Cada `POST` crea un UUID nuevo: repetirlo no es una operación idempotente.

Para guardar el UUID de una nueva solicitud y consultar su estado:

```bash
REPORT_ID=$(curl --fail-with-body -sS -X POST "$API_URL/reports" \
  -H 'Content-Type: application/json' -d '{}' | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl --fail-with-body -sS "$API_URL/reports/$REPORT_ID"
```

`GET /reports/{id}` devuelve `200` en cualquiera de los estados `pending`,
`processing`, `retrying`, `completed` o `failed`. PostgreSQL es la fuente de
estado; la consulta no utiliza resultados Celery. Ejemplo recién aceptado:

```json
{"id":"6c678f08-5a90-4dc1-b4ae-c5744e249df3","status":"pending","attempts":0,"created_at":"2026-01-01T00:00:00Z","started_at":null,"finished_at":null,"download_url":null,"error_code":null}
```

Las fechas ausentes y el error sin valor son `null`. `download_url` solo tiene
valor en `completed`; `file_ref`, rutas internas y trazas no se serializan.
La simulación activa permite observar `retrying` antes del segundo intento.
Espera un estado terminal antes de descargar:

```bash
while true; do
  REPORT_STATUS=$(curl --fail-with-body -sS "$API_URL/reports/$REPORT_ID" | \
    python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])') || break
  echo "$REPORT_STATUS"
  case "$REPORT_STATUS" in completed|failed) break ;; esac
  sleep 1
done
# Ejecutar cuando el estado sea completed:
curl --fail-with-body -OJ "$API_URL/reports/$REPORT_ID/download"
```

La descarga responde `200`, `Content-Type: text/csv` y
`Content-Disposition: attachment; filename="report-{id}.csv"`. `curl -OJ`
guarda ese nombre en el directorio actual. El archivo contiene 100 ventas.
Un reporte `failed` conserva un `error_code` público y no admite descarga.

Los errores de aplicación usan `{"detail":{"code":"…","message":"…"}}`:

| HTTP | Código | Situación |
|---|---|---|
| 404 | `report_not_found` | UUID válido sin registro, tanto en consulta como descarga. |
| 409 | `report_not_ready` | Descarga de cualquier estado distinto de `completed`, incluido `failed`. |
| 410 | `report_file_missing` | Estado `completed` cuyo archivo ya no existe. |
| 503 | `persistence_unavailable` | Fallo al guardar o consultar en PostgreSQL. |
| 503 | `publication_failed` | Fallo detectado al publicar; incluye `detail.report_id` porque ya se confirmó el registro. |

Ante fallo de publicación se intenta marcar `failed` con el código
`publication_failed`. Si el worker ya completó el reporte, se conserva ese
resultado. Consulta `detail.report_id` para diagnosticarlo antes de repetir el
POST; un timeout de publicación puede ser ambiguo.

Estos ejemplos muestran validación y ausentes sin modificar datos:

```bash
# 422: UUID inválido; 404: UUID válido inexistente.
curl -i "$API_URL/reports/no-es-un-uuid"
curl -i "$API_URL/reports/00000000-0000-0000-0000-000000000000"
# 422: no se aceptan campos extra, null ni cuerpo ausente.
curl -i -X POST "$API_URL/reports" \
  -H 'Content-Type: application/json' -d '{"filter":"unused"}'
curl -i -X POST "$API_URL/reports" \
  -H 'Content-Type: application/json' -d 'null'
curl -i -X POST "$API_URL/reports"
```

`422` usa el formato convencional de validación de FastAPI y no crea ni publica
un reporte. Para observar `409`, detén el worker con `docker compose stop worker`,
acepta un reporte y llama a su URL de descarga mientras siga `pending`; después
reanuda con `docker compose up -d --no-build worker`. `410` requiere que se haya
perdido el CSV de un reporte completado. `503` se observa si PostgreSQL o RabbitMQ
no están disponibles durante la operación; `/health` sigue comprobando solo el
proceso. No hay endpoints públicos para provocar esos fallos.

`/docs` y `/openapi.json` muestran `ReportCreate`, `ReportAccepted`,
`ReportResponse`, los errores y la descarga CSV. Son modelos SQLModel de datos,
sin tabla, separados de la entidad de dominio y de `ReportTable`.

## Publicación y procesamiento Celery

Como utilidad de diagnóstico, este cliente Python compone el mismo caso de
uso que `POST /reports` directamente dentro del contenedor:

```bash
docker compose run --rm -T --no-deps api python - <<'PYCLIENT'
import logging
from app.bootstrap.config import Settings
from app.bootstrap.worker import create_celery_app
from app.application.use_cases import RequestReport
from app.infrastructure.celery_publisher import CeleryReportTaskPublisher
from app.infrastructure.persistence import SQLModelReportRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = create_celery_app(Settings())
try:
    with app.database.session() as session:
        report_id = RequestReport(
            SQLModelReportRepository(session), CeleryReportTaskPublisher(app)
        ).execute()
    print(report_id)
finally:
    app.database.dispose()
    app.close()
PYCLIENT
```

El cliente confirma `pending` antes de publicar únicamente `[str(report_id)]`
como argumentos JSON de `reports.generate`, con kwargs vacíos. Usa la cola
`reports` durable y mensajes persistentes. El UUID también es `task_id` para
correlación; no deduplica entregas. No llama a `AsyncResult.get()` ni espera la
generación. Los resultados Celery se ignoran y el backend está deshabilitado:
PostgreSQL conserva el estado público, sin Redis ni backend RPC.

Las confirmaciones RabbitMQ se habilitan con `confirm_publish`. La conexión
explícita del publisher evita esperar un productor del pool; no hay reintentos
de publicación ni reconexiones indefinidas en la solicitud. El timeout de
conexión, los timeouts de lectura/escritura del transporte AMQP y los timeouts
de publicación/confirmación son de cinco segundos por operación. Estas opciones
se consumen por Kombu/py-amqp; `broker_connection_timeout` por sí solo no limita
la espera de confirmación. No constituyen un deadline total de cinco segundos:
resolución DNS y comportamiento del sistema operativo pueden añadir tiempo.
El consumidor puede reconectar al iniciar, independientemente de la publicación.

El worker tiene concurrencia 1 y prefetch 1. Solo `TemporaryReportError` se
traduce a `retry(countdown=5, max_retries=2)`: máximo tres intentos, limitado
también por `attempts` persistidos. Un error permanente termina en `failed`
sin retry. Un fallo al publicar el retry intenta registrar `failed` con
`retry_publication_failed`; esta compensación es best-effort y conserva un
terminal ya observado. Si también falla PostgreSQL, el error original sigue
propagándose y se registra `retry_compensation_failed`.

En <http://localhost:15672>, abre **Queues and Streams → reports** para observar
la cola. Con el worker detenido, un encargo permanece listo en RabbitMQ:

```bash
docker compose stop worker
# Ejecuta el cliente Python anterior y observa pending con attempts=0.
docker compose up -d --no-build worker
docker compose logs --tail=100 worker
```

Los logs incluyen `report_id`, `stage` y, al procesar estados, `attempt`.
El cliente registra `publishing`/`published`; el worker registra `received`,
`processing attempt=1`, `retrying attempt=1`, `retry_scheduled`,
`processing attempt=2` y `completed attempt=2`. El mismo UUID permite seguir
publicación, recepción, reintento y finalización. No se imprimen URLs ni
credenciales en estos logs de aplicación.

La prueba real reproducible crea un proyecto Compose, imagen y volúmenes con
nombres únicos y ningún puerto del host. Construye, migra, publica con el worker
ausente, inspecciona el JSON mediante la API interna RabbitMQ y vuelve a poner
el mensaje en cola. Después arranca un worker prefork real y comprueba
`pending/0 → retrying/1 → completed/2`, al menos cinco segundos desde el primer
inicio, CSV con 100 filas y logs correlacionados. No usa eager ni SQLite:

```bash
python3 scripts/verify_report_worker.py
# Para seleccionar contexto sin cambiar el contexto global:
python3 scripts/verify_report_worker.py --context desktop-linux
```

El script elimina únicamente sus contenedores, volúmenes e imagen en `finally`,
incluso ante error. Puede tardar varios minutos la primera vez. No modifica el
proyecto Compose habitual ni usa sus credenciales, puertos o volúmenes.

Confirmar publicación significa que RabbitMQ aceptó el mensaje; no garantiza
finalización futura ni supervivencia ante cualquier caída. Se conserva el ack
temprano predeterminado de Celery (`task_acks_late=False`): al caer el worker
una tarea ya reconocida puede perderse y dejar `processing`. Una entrega aún
no reconocida puede volver a entregarse; un retry es otro mensaje. No hay
recuperación automática de `processing`, outbox, exclusión entre múltiples
workers ni garantía exactamente una vez. Una caída entre commit y publicación
puede dejar `pending` sin mensaje; un timeout de confirmación puede ser ambiguo.
Los terminales se omiten sin regenerar ni relanzar `failed`.

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
docker compose run --rm migrate
docker compose up -d --no-build api worker
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
docker compose up -d postgres rabbitmq
docker compose run --rm migrate
docker compose up -d --no-build api worker
```

Compose mantiene separada esta eliminación de la parada habitual. Redis no
forma parte de este entorno: el seguimiento de reportes está en PostgreSQL.
