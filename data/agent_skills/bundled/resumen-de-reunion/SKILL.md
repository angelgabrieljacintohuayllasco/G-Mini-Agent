---
name: resumen-de-reunion
description: Convertir una transcripción, audio transcrito o notas sueltas de una reunión en un resumen con acuerdos, responsables y fechas. Úsala cuando el usuario comparta notas o grabaciones de una reunión o llamada.
---

# Resumen de reunión

## Entrada
- Si el usuario da un audio o video, primero obtén la transcripción.
- Si faltan datos clave (fecha, participantes, tema), dedúcelos del texto o pregunta una sola vez al final, no al principio.

## Estructura de salida

**Reunión:** tema — fecha — participantes

**En una frase:** de qué trató y cuál fue la decisión principal.

**Acuerdos**
- Qué se decidió, redactado como hecho cerrado.

**Tareas**
| Tarea | Responsable | Fecha límite |
|---|---|---|

**Pendientes y riesgos**
- Lo que quedó sin resolver o depende de alguien externo.

**Próxima reunión:** fecha o "no se definió".

## Reglas
- Atribuye una tarea a alguien solo si en la reunión quedó claro; si no, escribe "sin asignar".
- Mantén cifras, montos y fechas exactamente como se dijeron.
- No incluyas charla informal ni repeticiones.
- Si el usuario lo pide, ofrece redactar el correo de seguimiento con estos mismos puntos.
