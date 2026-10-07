---
name: reporte-semanal
description: Armar un reporte semanal o mensual a partir de datos (Excel, CSV, notas, ventas, tareas) con cifras clave, comparación y próximos pasos. Úsala cuando el usuario pida un reporte, un resumen de resultados o un balance de la semana o el mes.
---

# Reporte de periodo

## Datos
1. Identifica las fuentes (archivos, carpetas, hojas) y el periodo exacto, con fechas de inicio y fin.
2. Lee los datos completos; si son tablas grandes, calcula con un script de Python en vez de sumar a mano.
3. Busca el periodo anterior para comparar. Si no existe, dilo.

## Cálculos
- Totales del periodo y variación contra el anterior, en valor y en porcentaje.
- Los 3 a 5 elementos que más aportaron (productos, clientes, tareas) y los que más cayeron.
- Revisa que las sumas cuadren con el total del archivo antes de presentar.

## Formato
**Periodo:** del ... al ...

**Resumen:** 2 o 3 frases con lo más importante y la cifra principal.

**Cifras clave**
| Indicador | Este periodo | Anterior | Variación |
|---|---|---|---|

**Lo destacado** — viñetas con causa probable si se conoce.

**Próximos pasos** — 2 a 4 acciones concretas.

## Reglas
- Montos con moneda y separador de miles; porcentajes con un decimal.
- No inventes causas: si es una suposición, márcala como tal.
- Si el usuario lo pide, guarda el reporte como archivo (Markdown, Excel o PDF) y dile dónde quedó.
