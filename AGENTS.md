# Instrucciones para agentes

## Proyecto y referencias

Laboratorio educativo de reportes CSV asíncronos con Python 3.12, FastAPI,
Celery, RabbitMQ, PostgreSQL y SQLModel. Usa `uv` y Docker Compose.

- Consulta [README.md](README.md) para configuración, servicios y ejemplos.
- Consulta [docs/verification.md](docs/verification.md) para cobertura y evidencia.
- Las reglas SDD están en [openspec/config.yaml](openspec/config.yaml).
  Los contratos y decisiones están en `openspec/specs/` y en el cambio pertinente
  de `openspec/changes/`; revisa sus especificaciones, diseño y tareas antes de
  modificar comportamiento.
- Escribe documentación en español. En OpenSpec conserva los encabezados
  estructurales y las palabras SHALL/MUST en inglés.

## Arquitectura y límites

- `domain`: entidades y transiciones; solo biblioteca estándar, sin frameworks.
- `application`: casos de uso y contratos; depende del dominio y biblioteca
  estándar, sin importar presentación, infraestructura ni frameworks.
- `presentation`: rutas HTTP, esquemas de transporte y tareas Celery delgadas.
- `infrastructure`: implementaciones de persistencia, publicación, CSV y archivos.
- `bootstrap`: construye e inyecta dependencias concretas. Usa constructores en
  aplicación y `Depends` en HTTP; conserva SOLID/KISS y repositorios específicos,
  sin contenedores DI ni repositorios genéricos adicionales.
- Mantén separados `Report`, `ReportTable` y los esquemas SQLModel sin tabla de
  entrada/respuesta. Serializa únicamente campos públicos.
- Delimita lecturas y escrituras con `repository.transaction()`; el repositorio
  confirma o revierte al salir, incluso si falla el commit. Publica después de
  confirmar `pending`; genera y guarda archivos fuera de transacciones SQL.
- Usa una sesión síncrona por petición/tarea y ciérrala siempre. Conserva engines
  perezosos por proceso y el manejo de fork; no compartas conexiones abiertas.
- El estado público vive en PostgreSQL; Celery usa RabbitMQ sin backend de
  resultados Redis/RPC. Conserva concurrencia y prefetch 1 para el laboratorio.
  Outbox, recuperación automática y exclusión entre consumidores quedan fuera
  del alcance actual, salvo que se solicite ampliarlo.

## Desarrollo y verificación

Ejecuta desde la raíz usando el Python del proyecto:

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

- PostgreSQL requiere `TEST_DATABASE_URL` explícito con driver
  `postgresql+psycopg` y nombre terminado en `_test`. Sus pruebas hacen DDL:
  usa una base desechable propia, en serie, nunca `DATABASE_URL` como fallback.
  Sin esa variable se omiten; distingue esos skips de pruebas ejecutadas.
- Para cambios del flujo integrado usa
  `uv run --locked python scripts/verify_report_lifecycle.py`; el recorrido del
  mensaje Celery está en `scripts/verify_report_worker.py`. Ambos aceptan
  `--context desktop-linux` sin cambiar el contexto global de Docker.
- Aísla recursos de pruebas de los del usuario y limpia solo los propios.
- Si modificas OpenSpec, valida el cambio afectado con
  `openspec validate <change-id> --strict --no-interactive`.
- Elige verificaciones según el cambio: documentación requiere revisar enlaces,
  comandos y `git diff --check`; no necesita levantar servicios. Para código,
  prueba el comportamiento afectado y amplía la comprobación cuando corresponda.
- Registra comandos, resultados y límites de verificación. Actualiza tareas SDD
  solo con evidencia; preparar una especificación no completa su implementación.

## Cambios y entrega

- Trabaja en una rama y prepara un PR, salvo instrucción distinta del usuario.
  Deja el merge al usuario salvo que lo solicite. Preserva cambios ajenos.
- Mantén cambios acotados; actualiza README cuando cambie el uso público. En el
  PR describe el problema, el resultado y la validación realmente ejecutada.
- Gestiona dependencias con `uv` y actualiza `uv.lock` cuando cambien. No incluyas
  `.env`, credenciales ni archivos CSV generados en los commits.
- Trata el archivado de OpenSpec como etapa aparte de crear el PR; conserva
  los enlaces de documentación cuando muevas o archives especificaciones.
