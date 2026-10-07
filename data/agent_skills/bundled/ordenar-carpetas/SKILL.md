---
name: ordenar-carpetas
description: Ordenar una carpeta desordenada (Descargas, Escritorio, documentos) agrupando archivos por tipo o fecha sin perder nada. Úsala cuando el usuario pida organizar, limpiar u ordenar carpetas o archivos.
---

# Ordenar carpetas sin perder archivos

## Principios
- Nunca borres: mueve. Lo dudoso va a una carpeta `Revisar`.
- No toques carpetas del sistema, de programas ni de proyectos de código (las que tienen `.git`, `package.json`, `venv`).
- Trabaja en dos fases: primero el plan, luego mover solo con la aprobación del usuario.

## Fase 1: inventario y plan
1. Lista la carpeta con file_list (sin entrar a subcarpetas grandes salvo que el usuario lo pida).
2. Agrupa por tipo:
   - Documentos: pdf, docx, xlsx, pptx, txt, csv
   - Imágenes: jpg, jpeg, png, webp, heic
   - Videos: mp4, mov, mkv
   - Audio: mp3, wav, m4a, ogg
   - Comprimidos: zip, rar, 7z
   - Instaladores: exe, msi
3. Muestra al usuario un resumen: cuántos archivos van a cada carpeta, duplicados aparentes (mismo nombre con "(1)") y archivos muy grandes.
4. Pregunta si aprueba el plan o prefiere ordenar por fecha (`2026-10`).

## Fase 2: mover
1. Crea las carpetas de destino.
2. Mueve con un comando de terminal por lote (PowerShell `Move-Item -LiteralPath`), nunca uno por uno con clics.
3. Si un nombre ya existe en el destino, agrega un sufijo en vez de sobrescribir.
4. Al final, lista la carpeta otra vez y reporta: archivos movidos por destino y lo que quedó en `Revisar`.
