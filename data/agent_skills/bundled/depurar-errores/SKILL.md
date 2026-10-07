---
name: depurar-errores
description: Encontrar y corregir la causa real de un error en un programa, script o configuración. Úsala cuando el usuario reporte un error, un traceback, algo que "dejó de funcionar" o un comportamiento raro en su código o PC.
---

# Depurar errores

## 1. Reproducir
- Consigue el mensaje de error completo (traceback, código de salida, log) y el comando o paso exacto que lo produce.
- Ejecuta ese paso tú mismo si es seguro. Si no se reproduce, pregunta qué cambió (versión, archivo, red, permisos).

## 2. Aislar
- Lee el error de abajo hacia arriba: la última línea dice qué falló; las de arriba, dónde.
- Abre el archivo y la línea exacta. Revisa también lo que llama a esa función.
- Revisa cambios recientes: `git status`, `git diff`, `git log -5`.

## 3. Hipótesis
- Escribe una hipótesis concreta ("la variable llega vacía porque el .env no se carga") y cómo comprobarla.
- Comprueba una hipótesis a la vez: imprime el valor, corre un caso mínimo o revisa la config.

## 4. Corregir
- Cambia lo mínimo que explica el error. No reescribas módulos enteros.
- Si hay tests, agrega uno que falle antes del cambio y pase después.
- No borres datos, no hagas `git reset --hard` ni desinstales nada sin pedir permiso.

## 5. Verificar y explicar
- Vuelve a correr el paso que fallaba y muestra la salida.
- Explica en dos líneas: causa y corrección. Si encontraste otro problema en el camino, menciónalo aparte sin arreglarlo.
