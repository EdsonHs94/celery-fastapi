# Tasks

Checklist de implementación futura; ninguna tarea se completa al preparar el SDD. Leer `proposal.md`, `design.md` y `specs/report-generation/spec.md` antes de implementar. Las decisiones técnicas y valores predeterminados están en el diseño; los criterios observables están en la especificación.

## 1. Base del proyecto y entorno reproducible

- [x] 1.1 Crear la estructura de capas y definir Python 3.12, dependencias compatibles con FastAPI, Celery, SQLModel, Psycopg 3, Alembic, configuración y pytest mediante uv; generar uv.lock y verificar `uv sync --locked`.
- [x] 1.2 Crear Dockerfile y Compose con api, worker, rabbitmq y postgres, volúmenes persistentes, carpeta compartida de reportes, healthchecks y usuario RabbitMQ local propio; verificar `docker compose config --quiet` y construcción de la imagen.
- [x] 1.3 Añadir configuración tipada y .env.example con conexiones internas, REPORTS_DIR y SIMULATE_TRANSIENT_FAILURE=true; verificar con pruebas unitarias que se carga la configuración y se rechazan valores inválidos.
- [x] 1.4 Documentar en README requisitos, servicios, variables y comandos de arranque/paro, distinguiendo conservación y eliminación de volúmenes; verificar los comandos de configuración desde un entorno limpio.

## 2. Dominio y contratos de aplicación

- [x] 2.1 Definir Report y ReportStatus independientes del ORM, con transiciones, intentos y fechas del diseño; escribir primero y ejecutar pruebas para estados válidos, transiciones inválidas y conservación de datos terminales.
- [x] 2.2 Definir los contratos ReportRepository, ReportTaskPublisher, ReportGenerator y ReportStorage del diseño, y errores de aplicación; verificar sus usos mediante dependencias falsas en tests/unit/application y comprobar que dominio/aplicación no importan frameworks.
- [x] 2.3 Implementar RequestReport para confirmar pending antes de publicar; verificar con fakes el orden commit/publicación, ausencia de publicación si falla la persistencia y respuesta de error con registro fallido ante publicación fallida.
- [x] 2.4 Implementar GetReport sobre el repositorio; verificar consulta de todos los estados y error para UUID inexistente, sin crear nuevas tareas ni registros.
- [x] 2.5 Implementar GenerateReport con transacciones cortas, incremento de intentos, resultados terminales y presupuesto de tres intentos; verificar con fakes éxito, reintento, agotamiento, error permanente y omisión de reportes completed/failed sin efectos adicionales.
- [x] 2.6 Documentar las responsabilidades de las capas y sus dependencias en README, enlazando al diseño; verificar mediante una prueba de arquitectura que el dominio no depende del ORM y los casos de uso no dependen de presentación/infraestructura.

## 3. Persistencia SQLModel sobre PostgreSQL

- [x] 3.1 Crear ReportTable y migración Alembic inicial con campos y nulabilidad del diseño; verificar upgrade desde base vacía, columnas esperadas y downgrade sobre una base de prueba.
- [x] 3.2 Implementar el repositorio específico con conversión entidad/tabla y transaction() que confirma o revierte según el resultado; verificar contra PostgreSQL alta, consulta, actualización, UUID ausente y rollback sin escrituras parciales.
- [x] 3.3 Crear factories de engine/sesión para API y procesos del worker, con cierre por petición/tarea y sin conexiones abiertas heredadas entre procesos; verificar aislamiento de dos sesiones, persistencia entre ellas y cierre tanto en éxito como en error.
- [x] 3.4 Documentar migraciones y configuración de PostgreSQL en README; verificar el comando de migración con el servicio Compose y que la API no crea tablas al iniciar.

## 4. CSV y almacenamiento compartido

- [x] 4.1 Implementar el generador determinista de 100 ventas con Decimal, encabezado y formato exactos; verificar con pruebas UTF-8, importes de dos decimales, 100 filas y contenido idéntico entre dos reportes.
- [x] 4.2 Incorporar la simulación configurable de TemporaryReportError solo en el primer intento y el comentario educativo literal del diseño; verificar fallo antes de producir bytes, éxito en el segundo intento y éxito inicial al desactivarla.
- [x] 4.3 Implementar escritura temporal y sustitución atómica con referencias relativas dentro de REPORTS_DIR; verificar nombre final, conservación del archivo completo, ausencia de descarga parcial, resolución restringida al directorio y error de archivo perdido.
- [x] 4.4 Documentar el contenido ficticio, la ubicación compartida y cómo desactivar la simulación; verificar que el ejemplo de CSV coincide con el generador y el comentario explica que el fallo es intencional y educativo.

## 5. Publicación y procesamiento con Celery

- [x] 5.1 Configurar Celery con RabbitMQ, JSON, cola reports, confirmaciones de publicación, límite de conexión/publicación de cinco segundos, resultados ignorados y sin backend Redis/RPC; verificar configuración y publicación real de un mensaje que contiene solo el UUID.
- [x] 5.2 Implementar ReportTaskPublisher con logs correlacionados; verificar propagación de errores de publicación, ausencia de espera por el resultado y conversión del UUID a un argumento serializable.
- [x] 5.3 Crear la tarea delgada y su composición de dependencias por proceso, traduciendo TemporaryReportError a retry con countdown=5 y max_retries=2; verificar con pruebas del adaptador que no se reintentan errores permanentes y que un fallo al programar retry intenta registrar failed.
- [x] 5.4 Fijar un worker con concurrencia 1 y prefetch 1 para el laboratorio; verificar recepción y reintento con un worker y RabbitMQ reales, y persistencia de un encargo enviado mientras el worker estaba detenido, sin usar modo eager como sustituto de la integración.
- [x] 5.5 Documentar en README logs, interfaz de RabbitMQ, límites de reentrega y ausencia de recuperación automática/outbox; verificar que publicación, recepción, reintento y finalización se correlacionan con report_id.

## 6. API e inyección de dependencias

- [ ] 6.1 Crear ReportCreate, esquema de aceptación y ReportResponse como modelos de datos sin tabla, separados de ReportTable y Report; verificar validación de objeto vacío, rechazo de campos extra y serialización sin rutas internas ni trazas.
- [ ] 6.2 Construir casos de uso con Depends y sesiones síncronas por petición; implementar POST /reports y verificar 202 con id/URLs, 422 sin efectos, 503 ante fallo de persistencia y 503 con report_id ante fallo de publicación.
- [ ] 6.3 Implementar GET /reports/{id} con lectura del estado persistido; verificar 200 en todos los estados, fechas/errores anulables, URL de descarga solo en completed, UUID ausente 404 e inválido 422.
- [ ] 6.4 Implementar GET /reports/{id}/download con almacenamiento inyectado; verificar 200 y encabezados de descarga, 409 para estados no completados, 410 para archivo perdido, 404 para reporte ausente y 422 para UUID inválido.
- [ ] 6.5 Completar ejemplos curl y contratos de respuesta en README; verificar los ejemplos contra la API y que la documentación OpenAPI muestra los esquemas de transporte, sin exponer el modelo de tabla.

## 7. Verificación integrada del comportamiento

- [ ] 7.1 Ejecutar el recorrido Compose con base vacía, migraciones y simulación activa; verificar POST 202, seguimiento de dos intentos, estado completed y descarga del CSV con 100 filas mediante una prueba de integración reproducible.
- [ ] 7.2 Repetir con la simulación desactivada y verificar completed con un intento; volver a entregar un UUID completado y verificar que archivo, fechas y cantidad de intentos no cambian.
- [ ] 7.3 Ejecutar pruebas integradas de errores con generador de prueba transitorio permanente y otro no recuperable; verificar failed a los tres intentos en el primer caso, failed sin retry en el segundo y rechazo de la descarga en ambos.
- [ ] 7.4 Ejecutar pruebas unitarias, de arquitectura, repositorio y recorrido real, además de `openspec validate async-report-generation --strict --no-interactive`; verificar que todos los escenarios de la especificación tienen cobertura y registrar comandos/resultados antes de marcar tareas completadas.

## Workflow follow-up

- Preparar estos documentos no completa ninguna tarea de implementación ni despliega servicios.
- Archivar el cambio solo después de implementar, verificar y revisar su resultado; no archivarlo durante la entrega del SDD.
