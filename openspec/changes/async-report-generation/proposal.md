# Proposal

## Why

Aprender a delegar desde FastAPI un trabajo a un worker de Celery mediante RabbitMQ, observando su estado, un reintento y el resultado descargable. Partimos de una carpeta vacía y queremos fijar un contrato verificable antes de implementar.

## What Changes

- Definir la solicitud asíncrona de un reporte CSV con 100 ventas ficticias reproducibles.
- Permitir consultar el estado del reporte y descargar el CSV cuando termine.
- Demostrar un fallo temporal intencional y su reintento, con una opción para desactivar la simulación y un comentario claro junto a ella.
- Persistir el seguimiento en PostgreSQL y guardar los archivos en un volumen local compartido.
- Adoptar capas, Repository e inyección de dependencias con dominio independiente del ORM y modelos SQLModel separados para entrada, tabla y respuesta.
- Preparar una ejecución local reproducible de los cuatro servicios mediante Docker Compose.

Este cambio prepara únicamente el SDD de esas capacidades futuras. La implementación quedará en tareas pendientes. La primera versión no incluye Redis, Kafka, frontend, autenticación, datos externos, programación periódica, cancelación, despliegue productivo ni recuperación automática mediante outbox.

## Capabilities

### New Capabilities

- `report-generation`: solicitar un reporte sin esperar su generación, consultar su ciclo de vida, observar reintentos limitados y descargar un CSV completado.

### Modified Capabilities

Ninguna: no existen especificaciones ni aplicación previas.

## Impact

La implementación futura incorporará `POST /reports`, `GET /reports/{id}` y `GET /reports/{id}/download`. API y worker compartirán los casos de uso; la persistencia usará SQLModel, Pydantic y SQLAlchemy sobre PostgreSQL, y el transporte usará Celery con RabbitMQ.

Se necesitarán una tabla de reportes, su migración, almacenamiento compartido, configuración local y pruebas unitarias, de repositorio y del recorrido completo. Este cambio documental no crea esos componentes, no ejecuta migraciones y no instala dependencias.
