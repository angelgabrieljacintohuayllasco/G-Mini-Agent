---
name: escribir-skills
description: Cómo escribir una buena skill propia con skill_author después de resolver una tarea repetible. Úsala antes de proponer o guardar una skill nueva.
---

# Escribir una skill propia

Una skill es una receta que tu yo futuro seguirá sin recordar esta conversación. Guárdala solo si la tarea:
- costó varios pasos o intentos, y
- es probable que el usuario la vuelva a pedir (con otros datos).

## Antes de guardar
1. Pregunta al usuario si quiere guardarla. Explica en una frase para qué servirá.
2. Busca si ya existe una parecida en "Skills disponibles"; si existe, mejora esa (overwrite=true) en vez de crear otra.

## Contenido
- **name:** kebab-case corto y descriptivo (`exportar-ventas-sunat`, no `skill1`).
- **description:** cuándo usarla y qué logra, en una o dos frases. Es lo único que verás para decidir si cargarla.
- **instructions:** pasos numerados y concretos: qué abrir, qué comando correr, qué verificar. Incluye los errores que encontraste y cómo se resolvieron.
- **files (opcional):** scripts o plantillas que se reutilizan, en `scripts/`, `templates/` o `references/`.

## Reglas
- Generaliza: reemplaza nombres, rutas personales y montos de este caso por parámetros ("la carpeta que indique el usuario").
- Nunca guardes contraseñas, tokens, claves ni datos personales.
- Incluye un paso final de verificación con evidencia (un archivo que existe, una salida esperada).
- Mantén el texto breve: si pasa de una página, mueve el detalle a `references/`.
