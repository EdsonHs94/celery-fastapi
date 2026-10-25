# Spec Delta

## Purpose

Permitir aprender el procesamiento asíncrono solicitando un reporte de ventas ficticias, consultando su estado, observando reintentos limitados y descargando su resultado sin mantener abierta la petición original.

## ADDED Requirements

### Requirement: Solicitud asíncrona de reporte

El sistema SHALL aceptar `POST /reports` con un objeto JSON vacío, registrar un reporte identificable y encargar su generación antes de responder `202 Accepted` con `id`, `status_url` y `download_url`, sin esperar la creación del CSV.

#### Scenario: Solicitud aceptada
- **WHEN** el cliente envía `{}` y están disponibles la persistencia y el transporte de tareas
- **THEN** recibe `202` con un UUID y las rutas `/reports/{id}` y `/reports/{id}/download`
- **AND** el reporte existe y se puede consultar aunque todavía no haya terminado

#### Scenario: Solicitud sin worker activo
- **WHEN** el cliente solicita un reporte con el transporte disponible y el procesador detenido
- **THEN** recibe `202` y el reporte permanece `pending`
- **AND** al arrancar el procesador, este puede recibir el trabajo pendiente

#### Scenario: Cuerpo inválido
- **WHEN** el cliente envía un cuerpo que no es un objeto JSON vacío
- **THEN** recibe `422` y no se crea ni publica un reporte

### Requirement: Consulta del ciclo de vida

El sistema SHALL responder a `GET /reports/{id}` con el estado persistido, la cantidad de intentos iniciados, las fechas disponibles y una URL de descarga solo cuando esté completado. Los estados públicos SHALL ser `pending`, `processing`, `retrying`, `completed` y `failed`.

#### Scenario: Consulta de un reporte pendiente
- **WHEN** el cliente consulta un reporte que todavía no ha comenzado
- **THEN** recibe `200`, estado `pending`, cero intentos y ninguna URL de descarga disponible

#### Scenario: Consulta durante la ejecución
- **WHEN** un intento ha comenzado y todavía no ha terminado ni programado un reintento
- **THEN** la consulta devuelve `processing` y la cantidad de intentos iniciados

#### Scenario: Consulta de un reporte completado
- **WHEN** el archivo se guardó correctamente y la finalización quedó registrada
- **THEN** la consulta devuelve `completed`, fecha de finalización y URL de descarga
- **AND** la respuesta no expone rutas internas, credenciales ni trazas de excepciones

### Requirement: Identificador desconocido o inválido

El sistema SHALL responder `404` al consultar o descargar un UUID de reporte inexistente, y `422` cuando el identificador no sea un UUID válido.

#### Scenario: Reporte inexistente
- **WHEN** se consulta o descarga un reporte con UUID válido que no fue registrado
- **THEN** se recibe `404` sin crear registros ni tareas

#### Scenario: Identificador mal formado
- **WHEN** el identificador de cualquiera de las rutas no tiene formato UUID
- **THEN** se recibe `422`

### Requirement: Reporte ficticio reproducible

El sistema SHALL producir un CSV UTF-8 con encabezado `sale_id,customer,total,currency` y exactamente 100 ventas ficticias. Cada ejecución SHALL producir el mismo contenido para esta prueba, sin depender de fuentes externas.

#### Scenario: Contenido generado
- **WHEN** termina correctamente la generación
- **THEN** el CSV contiene un encabezado y 100 filas de ventas identificadas del 1 al 100
- **AND** los importes son decimales con dos posiciones y la moneda es `PEN`

#### Scenario: Reproducibilidad entre reportes
- **WHEN** se completan dos reportes distintos
- **THEN** sus CSV tienen contenido idéntico aunque sus identificadores y nombres de archivo sean distintos

### Requirement: Descarga del resultado

El sistema SHALL permitir descargar un reporte completado mediante `GET /reports/{id}/download` con `200`, tipo `text/csv` y disposición de descarga. SHALL responder `409` si el reporte existe pero no está completado, y `410` si figura completado pero su archivo ya no existe.

#### Scenario: Descarga disponible
- **WHEN** el reporte está `completed` y su archivo existe
- **THEN** se entrega el CSV como archivo `report-{id}.csv`

#### Scenario: Descarga prematura o fallida
- **WHEN** se intenta descargar un reporte `pending`, `processing`, `retrying` o `failed`
- **THEN** se recibe `409` sin entregar archivos parciales

#### Scenario: Archivo perdido
- **WHEN** se intenta descargar un reporte completado cuyo archivo fue eliminado
- **THEN** se recibe `410` con un error comprensible

### Requirement: Demostración configurable de reintento

La prueba SHALL habilitar por defecto una simulación que hace fallar temporalmente el primer intento de cada reporte y permite que el siguiente continúe. La simulación SHALL poder desactivarse por configuración y estar identificada mediante un comentario educativo explícito junto al punto que provoca el fallo.

#### Scenario: Simulación habilitada
- **WHEN** comienza el primer intento con la simulación activa
- **THEN** se provoca el fallo antes de generar el archivo y el estado pasa a `retrying`
- **AND** el reintento se programa con una espera de cinco segundos
- **AND** el segundo intento puede completar el reporte con dos intentos registrados

#### Scenario: Simulación deshabilitada
- **WHEN** la simulación se desactiva y no hay fallos reales
- **THEN** el reporte se completa en un solo intento

#### Scenario: Intención educativa documentada
- **WHEN** se inspeccionan la simulación y la guía local
- **THEN** se explica que el fallo es intencional, demuestra reintentos y debe desactivarse fuera de la prueba

### Requirement: Reintentos limitados y fallo terminal

El sistema SHALL permitir como máximo dos reintentos después del intento inicial para errores transitorios reconocidos, con cinco segundos de espera. Al agotar los tres intentos, o ante un error no recuperable, SHALL persistir `failed`, fecha de finalización y un error público sin detalles internos.

#### Scenario: Error transitorio persistente
- **WHEN** la generación falla temporalmente en el intento inicial y en sus dos reintentos
- **THEN** el reporte queda `failed` con tres intentos iniciados y no se publica un cuarto intento

#### Scenario: Error no recuperable
- **WHEN** la generación encuentra un error no clasificado como transitorio
- **THEN** el reporte queda `failed` sin programar un reintento automático

### Requirement: Fallo de aceptación del encargo

El sistema SHALL responder `503` si no puede persistir la solicitud o si falla su publicación. Si el registro ya existe y es posible actualizarlo, SHALL marcarlo `failed` con error de publicación. El sistema SHALL reservar `202` para solicitudes cuya publicación haya retornado satisfactoriamente.

#### Scenario: Persistencia no disponible
- **WHEN** no se puede confirmar el registro inicial
- **THEN** se recibe `503` y no se intenta publicar el trabajo

#### Scenario: Fallo detectado al publicar
- **WHEN** se confirmó el registro y la publicación devuelve un error
- **THEN** se recibe `503` con el identificador del reporte ya creado
- **AND** se intenta persistir `failed` con el error público `publication_failed`
- **AND** la respuesta no afirma que la tarea fue aceptada

### Requirement: Encargo repetido de un reporte completado

El sistema SHALL tratar como operación sin efectos la recepción posterior de un encargo para un reporte ya completado, conservando su archivo, cantidad de intentos y fechas finales.

#### Scenario: Reentrega después de completar
- **WHEN** llega de nuevo el identificador de un reporte que ya está `completed`
- **THEN** no se genera otro archivo ni se modifican sus datos finales

### Requirement: Ejecución local documentada

La prueba SHALL ofrecer una configuración local reproducible que permita iniciar todos sus servicios, observar el transporte y los logs del procesador, solicitar un reporte, consultar su estado y descargarlo siguiendo una guía de comandos.

#### Scenario: Recorrido desde un entorno limpio
- **WHEN** se siguen los pasos documentados de configuración, arranque y solicitud
- **THEN** se puede observar el fallo educativo, esperar el reintento y descargar un CSV de 100 filas

#### Scenario: Inspección del seguimiento
- **WHEN** se revisan los logs de publicación, recepción, reintento y finalización
- **THEN** se puede correlacionar cada etapa mediante el identificador del reporte
