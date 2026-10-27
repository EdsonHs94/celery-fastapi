# Verificación del ciclo de reportes

Contrato: [23 escenarios OpenSpec](../openspec/changes/async-report-generation/specs/report-generation/spec.md).
La matriz distingue pruebas unitarias, HTTP en proceso con ASGI y adaptadores
falsos, repositorio PostgreSQL y recorrido HTTP por red en Compose. Una prueba
ASGI no constituye evidencia de RabbitMQ, PostgreSQL ni prefork reales.

## Ejecución reproducible

```bash
uv sync --locked
uv run pytest -q
# Base PostgreSQL desechable explícita, cuyo nombre termine en "_test":
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:PORT/reports_test' uv run pytest -q
uv run ruff check .
openspec validate async-report-generation --strict --no-interactive
uv run --locked python scripts/verify_report_lifecycle.py --context desktop-linux
```

El runner se ejecuta con Python 3.12 del proyecto, resuelto por uv; no se usa el
`python3` del sistema. Usa únicamente la biblioteca estándar.

La base indicada a pytest es exclusiva de pruebas: sus fixtures hacen DDL y
limpieza. El recorrido Compose crea su propia base vacía y no necesita esa
variable. `--context` es opcional y no cambia el contexto global de Docker.

El runner usa `compose.yaml` con un override temporal, credenciales e imagen
propias, proyecto y volúmenes únicos, y puertos efímeros en `127.0.0.1`. Sobrescribe
las variables interpoladas del entorno del host. Verifica ausencia de tablas,
ejecuta Alembic explícitamente y arranca API antes del worker. Conserva el
prefork, concurrencia 1 y prefetch 1. Las esperas HTTP/Compose/subprocesos están
acotadas; ante error imprime logs y ejecuta `down --volumes` solo para su proyecto,
seguido de la eliminación de su imagen.

API usa el comando Uvicorn normal de la imagen. Su factory configura INFO con
`logging.basicConfig` solo cuando el host no ha configurado handlers, permitiendo
correlacionar publicación por UUID sin reemplazar logging existente. Dos pruebas
regresivas en subprocess verifican ambos modos. Los generadores de fallo están
en `tests/integration/lifecycle/fixture_worker.py`, montado read-only fuera de la
imagen de producción (que excluye tests). Solo sustituyen el generador en la
composición existente; API, tarea, repositorio, storage, broker y base son reales.
No hay endpoints ni configuración de producto para provocar errores.

Los cuatro casos Compose producen cuatro UUID distintos y una reentrega adicional
del UUID completado (además de las entregas debidas a los retries):

- **C1**: POST 202 sin worker; GET pending/0 y descarga 409; simulación activa,
  retrying/1 observado y completed/2 con al menos cinco segundos; CSV de 100
  filas exactas, encabezado, UTF-8, Decimal/PEN y headers HTTP; logs por UUID.
- **C2**: recrear worker con simulación desactivada; completed/1, CSV idéntico a
  C1. Publicar otra vez ese mismo UUID por el publisher real y esperar un nuevo
  log Celery `succeeded` antes de comparar respuesta completa, fechas, intentos,
  contenido y `mtime_ns` del archivo. No se espera con un sleep arbitrario ni se
  llama a `AsyncResult.get()`.
- **C3**: generador siempre transitorio; observar retrying/1 y retrying/2,
  failed/3 con `attempts_exhausted`, fecha final y al menos diez segundos;
  descarga 409, sin archivo; exactamente tres recepciones y dos retry_scheduled.
- **C4**: generador no recuperable; failed/1 con `generation_failed`, fecha final,
  descarga 409 y sin archivo; una recepción y ningún retry_scheduled.

C3/C4 vigilan seis segundos adicionales (más de un countdown) para detectar
intentos extra. La prueba unitaria del presupuesto verifica además la omisión
incluso al invocar explícitamente una cuarta entrega. El runner no demuestra
exclusión concurrente, recuperación de caídas ni garantías exactamente una vez.

## Matriz de escenarios

Abreviaturas de rutas para los métodos exactos de la tabla:

- **HTTP**: `tests/unit/presentation/test_reports_http.py` (ASGI, repositorio y
  publisher falsos; casos de uso y almacenamiento local reales).
- **APP**: `tests/unit/application/test_reports.py` (unitarias con adaptadores falsos).
- **CSV**: `tests/unit/infrastructure/test_csv_report.py` (generador real).
- **STORE**: `tests/unit/infrastructure/test_report_storage.py` (archivos locales).
- **DOM**: `tests/unit/domain/test_report.py` (dominio puro).
- **Compose**: `scripts/verify_report_lifecycle.py`, casos C1–C4 de arriba.

Los conteos son invocaciones parametrizadas por método para ese escenario;
compartir un método entre filas no añade pruebas al total de la suite.

| Escenario (nombre exacto) | Método y conteo existente | Evidencia Compose / límite |
|---|---|---|
| Solicitud aceptada | HTTP `test_acceptance_commits_then_publishes_without_promising_status` (1); APP `test_request_commits_pending_before_publication_and_returns_identifier` (1) | C1–C4: cuatro POST 202 y URLs exactas |
| Solicitud sin worker activo | APP `test_request_commits_pending_before_publication_and_returns_identifier` (1) | C1: worker ausente, pending/0, consumo tras arrancar |
| Cuerpo inválido | HTTP `test_invalid_body_has_no_persistence_or_publication` (8) | C1: cuatro cuerpos 422, tabla aún vacía; ausencia de publicación se comprueba directamente con fake en HTTP |
| Consulta de un reporte pendiente | HTTP `test_status_reads_persisted_public_fields[pending]` (1) | C1: pending/0, fechas finales ausentes |
| Consulta durante la ejecución | HTTP `test_status_reads_persisted_public_fields[processing]` (1); APP `test_generation_commits_processing_then_writes_outside_transactions` (1) | C3/C4 permiten observar processing durante 0.4s; cobertura determinista ASGI |
| Consulta de un reporte completado | HTTP `test_status_reads_persisted_public_fields[completed]` (1) | C1/C2: ocho campos públicos exactos, fechas y URL |
| Reporte inexistente | HTTP `test_missing_and_invalid_identifiers` (2 parámetros 404); APP `test_unknown_report_has_no_write_or_external_effect` (2) | C1: GET y download 404 |
| Identificador mal formado | HTTP `test_missing_and_invalid_identifiers` (2 parámetros 422) | C1: GET y download 422 |
| Contenido generado | CSV `test_csv_has_exact_header_and_100_deterministic_decimal_sales` (1) | C1/C2: descarga UTF-8; 100 filas comparadas campo por campo |
| Reproducibilidad entre reportes | CSV `test_csv_has_exact_header_and_100_deterministic_decimal_sales` (1) | C2: bytes idénticos entre dos UUID distintos |
| Descarga disponible | HTTP `test_download_streams_csv_with_attachment_headers` (1) | C1/C2: 200, text/csv y attachment con nombre por UUID |
| Descarga prematura o fallida | HTTP `test_download_requires_completed` (4: pending, processing, retrying, failed) | C1 pending y C3/C4 failed: 409; processing/retrying cubiertos determinísticamente ASGI |
| Archivo perdido | HTTP `test_missing_completed_file_is_public_410` (1); STORE `test_missing_report_has_public_error_and_private_cause` (1) | ASGI + archivos reales; no se elimina un CSV en Compose |
| Simulación habilitada | CSV `test_simulation_fails_before_csv_serialization` (1); APP `test_transient_retry_then_success_keeps_first_start_and_clears_error` (1) | C1: retrying/1 → completed/2, espera ≥5s |
| Simulación deshabilitada | CSV `test_disabled_simulation_succeeds_on_first_attempt` (1) | C2: worker recreado y completed/1 |
| Intención educativa documentada | Inspección de comentario literal en `app/infrastructure/csv_report.py` y sección Configuración de README (2 fuentes, sin caso pytest específico) | Guía explica fallo intencional, objetivo y desactivación fuera de prueba |
| Error transitorio persistente | APP `test_third_transient_failure_is_terminal_and_fourth_delivery_has_no_effect` (1); DOM `test_domain_disallows_fourth_attempt_and_retry_after_third` (1) | C3: failed/3, dos esperas de 5s, sin cuarta recepción ni archivo, 409 |
| Error no recuperable | APP `test_errors_in_generation_or_storage_are_recorded` (2 parámetros no transitorios: generador/storage) | C4: failed/1, sin retry, archivo ni descarga |
| Persistencia no disponible | HTTP `test_persistence_failure_is_public_503` (3, incluido POST); APP `test_request_does_not_publish_when_initial_persistence_fails` (2) | Fallo inyectado ASGI/unitario, sin detener PostgreSQL en Compose |
| Fallo detectado al publicar | HTTP `test_failed_publication_returns_identifier_and_compensates` (1); APP `test_publication_failure_records_failed_and_exposes_only_public_error` (1) | Fallo inyectado ASGI/unitario; 503 y failed/publication_failed |
| Reentrega después de completar | APP `test_terminal_delivery_never_changes_data_or_calls_generator_storage[completed]` (1) | C2: reentrega adicional consumida y finalizada; estado, fechas, bytes y mtime inmutables |
| Recorrido desde un entorno limpio | `tests/integration/persistence/test_migrations.py::test_initial_migration_columns_and_reversible_downgrade` (1 PostgreSQL) | C1: cero tablas antes de migrar, servicios reales, CSV 100 filas |
| Inspección del seguimiento | `tests/unit/infrastructure/test_celery_publisher.py::test_publisher_uses_dedicated_connection_and_only_json_uuid` (1) | C1: published/received/processing/retrying/retry_scheduled/completed correlacionados |

Además, `tests/architecture/test_dependencies.py` aporta dos casos de límites
de capas. `tests/integration/persistence/` aporta diez casos que requieren
PostgreSQL explícito (repositorio 6, migraciones 3, fork 1), más tres casos de
seguridad que se ejecutan sin conectarse a PostgreSQL.

## Registro de ejecución

Evidencia de implementación, 2026-10-27:

| Comando | Resultado |
|---|---|
| `.venv/bin/pytest -q` sin TEST_DATABASE_URL (baseline antes de añadir regresión logging) | **158 passed, 10 skipped**, 4.47s; los diez skips requieren PostgreSQL explícito |
| `.venv/bin/ruff check scripts/verify_report_lifecycle.py tests/integration/lifecycle/fixture_worker.py` | **All checks passed!** |
| `python3 scripts/verify_report_lifecycle.py --help` (host Python 3.9) | **exit 0**, smoke de argumentos únicamente; no acredita ejecución del recorrido en Python 3.9 |
| `.venv/bin/pytest -q tests/unit/bootstrap/test_api_logging.py` antes del fix | **1 failed, 1 passed**, 0.90s; INFO no llegaba a stderr con configuración Uvicorn normal |
| Mismo comando tras `basicConfig` sin force en factory API | **2 passed**, 0.79s; INFO visible y handler/nivel preconfigurados conservados |
| `TEST_DATABASE_URL=… .venv/bin/pytest -q` (PostgreSQL aislado del coordinador) | **170 passed**, 3.33s; sin skips |
| `uv run --locked --no-sync python scripts/verify_report_lifecycle.py --context desktop-linux` (Python 3.12 del proyecto) | **exit 0**, cuatro casos PASS; proyecto `reports-task7-63325ea1d4e0`, cleanup propio completado |
| `.venv/bin/ruff check .` | **All checks passed!**, confirmado por coordinador |
| `openspec validate async-report-generation --strict --no-interactive` | **Válido**, confirmado por coordinador |

Primer arranque Compose: readiness detectó una conexión cerrada antes de que
Uvicorn atendiera HTTP (`RemoteDisconnected`); cleanup de recursos propios
completado. El poll inicial ahora contempla ConnectionError bajo el mismo
deadline; los requests de los escenarios continúan estrictos. La segunda
ejecución completó todos los casos y el cleanup. Evidencia de estados terminales:

| Caso | UUID | Resultado | Duración desde primer intento |
|---|---|---|---|
| C1 | `7311deb5-73c4-4bec-9789-26688b7a3b42` | completed/2; CSV 100 filas | 5.050837s |
| C2 | `3151d8e6-406d-4040-a635-6414f6c433f3` | completed/1; reentrega finalizada sin efectos | 0.005575s |
| C3 | `0d70a4db-f78f-40d0-a6fc-1175a9bf58c6` | failed/3; attempts_exhausted; download 409; sin archivo | 11.294595s |
| C4 | `45689be7-3329-40ef-ad4d-15bf2ea89f63` | failed/1; generation_failed; download 409; sin archivo | 0.405953s |

Matriz contrastada con la especificación: **23/23 escenarios** y los métodos
referenciados existen. Tareas 7.1–7.4 marcadas tras recibir esta evidencia real,
suite PostgreSQL completa y checks finales. No se archiva el cambio OpenSpec.
