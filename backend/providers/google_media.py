"""
G-Mini Agent — Generación multimedia con Google (Imagen, Veo, Lyria).
Soporta generación de imágenes, videos y música usando el SDK google-genai.

Docs oficiales:
  - Imagen:  https://ai.google.dev/gemini-api/docs/imagen
  - Gemini Image (Nano Banana): https://ai.google.dev/gemini-api/docs/image-generation
  - Veo:    https://ai.google.dev/gemini-api/docs/video
  - Lyria:  https://ai.google.dev/gemini-api/docs/music-generation
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import config

# Directorio de salida para archivos generados
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated"

# Flag de cancelación para operaciones de larga duración (video polling, music gen)
_cancel_requested = False


def cancel_media_generation():
    """Señala a las operaciones de media en curso que deben cancelarse."""
    global _cancel_requested
    _cancel_requested = True


def _check_cancel():
    """Verifica y resetea el flag de cancelación. Retorna True si se pidió cancelar."""
    global _cancel_requested
    if _cancel_requested:
        _cancel_requested = False
        return True
    return False


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _is_vertex() -> bool:
    return config.get("providers", "google", "backend", default="ai_studio") == "vertex_ai"


def _get_client(force_location: str | None = None):
    """Obtiene un cliente google-genai configurado (AI Studio o Vertex AI).

    force_location: override location for media models that only exist in specific regions.
    """
    from google import genai
    import os

    if _is_vertex():
        project_id = config.get("providers", "google", "project_id", default="")
        location = force_location or config.get("providers", "google", "location", default="global")
        credentials_file = config.get("providers", "google", "credentials_file", default="")

        if not project_id:
            raise ValueError("Vertex AI requiere project_id configurado.")
        if credentials_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file

        # Media models (Veo, Lyria, Imagen) only exist in us-central1, not in "global"
        if location == "global":
            location = "us-central1"

        return genai.Client(vertexai=True, project=project_id, location=location)

    vault = config.get("providers", "google", "api_key_vault", default="google_api")
    api_key = config.get_api_key(vault) or ""
    if not api_key:
        raise ValueError("No hay API key de Google configurada.")
    return genai.Client(api_key=api_key)


def _get_ai_studio_client():
    """Obtiene un cliente AI Studio (API key). Retorna None si no hay API key."""
    from google import genai

    vault = config.get("providers", "google", "api_key_vault", default="google_api")
    api_key = config.get_api_key(vault) or ""
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def _get_configured_model(media_type: str) -> str | None:
    """Lee el modelo configurado en generative_models del config."""
    key_map = {"image": "image_model", "video": "video_model", "music": "music_model"}
    key = key_map.get(media_type)
    if key:
        return config.get("generative_models", key, default=None)
    return None


# ── Imagen ────────────────────────────────────────────────────────


async def generate_image(
    prompt: str,
    model: str | None = None,
    aspect_ratio: str = "1:1",
    num_images: int = 1,
) -> dict[str, Any]:
    """
    Genera imagen(es) con Google.

    - Modelos 'imagen-*' usan client.models.generate_images()   (API de Imagen)
    - Modelos 'gemini-*' usan client.models.generate_content()   (Nano Banana)
      con response_modalities=["IMAGE","TEXT"] e image_config para aspect_ratio.
    """
    model = model or _get_configured_model("image") or "gemini-2.5-flash-image"
    client = _get_client()
    out_dir = _ensure_output_dir()
    ts = int(time.time())

    logger.info(f"[media] Generando imagen: model={model}, prompt={prompt[:80]}...")

    loop = asyncio.get_running_loop()

    if model.startswith("imagen-"):
        # ── API de Imagen dedicada ──
        from google.genai import types

        img_config = types.GenerateImagesConfig(
            number_of_images=min(num_images, 4),
            aspect_ratio=aspect_ratio,
            person_generation="allow_adult",
        )

        def _sync_gen():
            return client.models.generate_images(
                model=model,
                prompt=prompt,
                config=img_config,
            )

        response = await loop.run_in_executor(None, _sync_gen)

        saved_files = []
        if response.generated_images:
            for i, gen_img in enumerate(response.generated_images):
                if gen_img.image and gen_img.image.image_bytes:
                    ext = "png"
                    if gen_img.image.mime_type and "jpeg" in gen_img.image.mime_type:
                        ext = "jpg"
                    fname = f"img_{ts}_{i}.{ext}"
                    fpath = out_dir / fname
                    fpath.write_bytes(gen_img.image.image_bytes)
                    saved_files.append({
                        "path": str(fpath),
                        "filename": fname,
                        "base64": base64.b64encode(gen_img.image.image_bytes).decode(),
                        "mime_type": gen_img.image.mime_type or f"image/{ext}",
                    })
                    logger.info(f"[media] Imagen guardada: {fpath}")

        return {
            "success": len(saved_files) > 0,
            "model": model,
            "message": f"{len(saved_files)} imagen(es) generada(s) con {model}",
            "count": len(saved_files),
            "files": saved_files,
        }

    else:
        # ── Gemini image models (Nano Banana) via generate_content ──
        from google.genai import types

        gen_config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
            ),
        )

        def _sync_gen():
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=gen_config,
            )

        response = await loop.run_in_executor(None, _sync_gen)

        saved_files = []
        text_parts = []
        img_idx = 0

        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        # Saltar partes de "pensamiento" (thought images)
                        if getattr(part, "thought", False):
                            continue
                        if part.inline_data and part.inline_data.data:
                            mime = part.inline_data.mime_type or "image/png"
                            ext = "png" if "png" in mime else "jpg"
                            fname = f"img_{ts}_{img_idx}.{ext}"
                            fpath = out_dir / fname
                            img_bytes = part.inline_data.data
                            fpath.write_bytes(img_bytes)
                            saved_files.append({
                                "path": str(fpath),
                                "filename": fname,
                                "base64": base64.b64encode(img_bytes).decode(),
                                "mime_type": mime,
                            })
                            img_idx += 1
                            logger.info(f"[media] Imagen guardada: {fpath}")
                        elif part.text:
                            text_parts.append(part.text)

        return {
            "success": len(saved_files) > 0,
            "model": model,
            "message": f"{len(saved_files)} imagen(es) generada(s) con {model}",
            "count": len(saved_files),
            "files": saved_files,
            "text": "\n".join(text_parts) if text_parts else None,
        }


def _download_video(client, video, fpath: Path) -> None:
    """Descarga un video generado a disco. Maneja diferencias AI Studio vs Vertex AI."""
    # 1. Si ya tiene bytes en memoria, guardar directo
    if getattr(video, "video_bytes", None):
        fpath.write_bytes(video.video_bytes)
        return

    # 2. AI Studio: client.files.download() funciona
    if not _is_vertex():
        client.files.download(file=video)
        video.save(str(fpath))
        return

    # 3. Vertex AI: client.files.download() no soportado.
    #    Descargar desde GCS URI o via AI Studio client como fallback.
    uri = getattr(video, "uri", None)

    if uri and uri.startswith("gs://"):
        try:
            from google.cloud import storage as gcs

            parts = uri.replace("gs://", "").split("/", 1)
            bucket_name, blob_name = parts[0], parts[1]
            gcs_client = gcs.Client()
            bucket = gcs_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.download_to_filename(str(fpath))
            return
        except Exception as gcs_exc:
            logger.warning(f"[media] GCS download falló ({gcs_exc}), intentando AI Studio fallback...")

    # 4. Fallback: usar AI Studio client para descargar
    ai_client = _get_ai_studio_client()
    if ai_client:
        try:
            ai_client.files.download(file=video)
            video.save(str(fpath))
            return
        except Exception as ai_exc:
            logger.warning(f"[media] AI Studio fallback download falló: {ai_exc}")

    # 5. Último recurso: descargar via HTTP autenticado desde URI
    if uri and uri.startswith("http"):
        import google.auth
        import google.auth.transport.requests
        import requests

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        resp = requests.get(uri, headers={"Authorization": f"Bearer {creds.token}"}, timeout=120)
        resp.raise_for_status()
        fpath.write_bytes(resp.content)
        return

    raise RuntimeError(f"No se pudo descargar el video (uri={uri})")


# ── Video ─────────────────────────────────────────────────────────


async def generate_video(
    prompt: str,
    model: str | None = None,
    aspect_ratio: str = "16:9",
    duration_seconds: int = 8,
) -> dict[str, Any]:
    """
    Genera video con Google Veo.
    Flujo oficial: generate_videos() → polling → client.files.download() → video.save()
    Ref: https://ai.google.dev/gemini-api/docs/video
    """
    default_veo = "veo-3.1-generate-001" if _is_vertex() else "veo-3.1-generate-preview"
    model = model or _get_configured_model("video") or default_veo
    client = _get_client()
    out_dir = _ensure_output_dir()
    ts = int(time.time())

    logger.info(f"[media] Generando video: model={model}, prompt={prompt[:80]}...")

    loop = asyncio.get_running_loop()

    from google.genai import types

    vid_config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        number_of_videos=1,
        person_generation="allow_adult",
    )

    def _sync_gen():
        return client.models.generate_videos(
            model=model,
            prompt=prompt,
            config=vid_config,
        )

    operation = await loop.run_in_executor(None, _sync_gen)

    # Polling hasta que complete (máximo 6 minutos — Veo puede tardar hasta 6 min en peaks)
    max_wait = 360
    poll_interval = 10
    elapsed = 0

    _cancel_requested and _check_cancel()  # Reset any stale cancel flag
    while not operation.done and elapsed < max_wait:
        logger.info(f"[media] Video generando... ({elapsed}s)")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if _check_cancel():
            logger.info("[media] Video generación cancelada por el usuario")
            return {
                "success": False,
                "model": model,
                "message": "Generación de video cancelada",
                "count": 0,
                "files": [],
            }

        def _sync_poll():
            return client.operations.get(operation)

        operation = await loop.run_in_executor(None, _sync_poll)

    if not operation.done:
        return {
            "success": False,
            "model": model,
            "message": f"Timeout: el video no se generó en {max_wait}s",
            "error": f"Timeout: el video no se generó en {max_wait}s",
            "count": 0,
            "files": [],
        }

    if operation.error:
        return {
            "success": False,
            "model": model,
            "message": str(operation.error),
            "error": str(operation.error),
            "count": 0,
            "files": [],
        }

    # Descargar y guardar videos localmente
    saved_files = []
    response = operation.response
    if response and response.generated_videos:
        for i, gen_vid in enumerate(response.generated_videos):
            if gen_vid.video:
                fname = f"vid_{ts}_{i}.mp4"
                fpath = out_dir / fname
                try:
                    await loop.run_in_executor(
                        None, lambda gv=gen_vid: _download_video(client, gv.video, fpath)
                    )
                    saved_files.append({
                        "path": str(fpath),
                        "filename": fname,
                        "mime_type": "video/mp4",
                    })
                    logger.info(f"[media] Video descargado y guardado: {fpath}")
                except Exception as dl_exc:
                    logger.error(f"[media] Error descargando video: {dl_exc}")
                    uri = getattr(gen_vid.video, "uri", None)
                    if uri:
                        saved_files.append({
                            "uri": uri,
                            "mime_type": "video/mp4",
                            "download_error": str(dl_exc),
                        })

    return {
        "success": len(saved_files) > 0,
        "model": model,
        "message": f"{len(saved_files)} video(s) generado(s) con {model}",
        "count": len(saved_files),
        "files": saved_files,
    }


# ── Música ────────────────────────────────────────────────────────


def _lyria_vertex_rest(prompt: str, model: str, project_id: str) -> dict:
    """Llama a Lyria en Vertex AI via REST API (endpoint /interactions, NO generateContent).

    Vertex AI Doc: POST v1beta1/projects/{ID}/locations/global/interactions
    Lyria requiere location=global y usa la API de 'interactions', no generateContent.
    """
    import google.auth
    import google.auth.transport.requests
    import requests as _requests

    creds, _ = google.auth.default()
    creds.refresh(google.auth.transport.requests.Request())

    url = (
        f"https://aiplatform.googleapis.com/v1beta1"
        f"/projects/{project_id}/locations/global/interactions"
    )
    payload = {
        "model": model,
        "input": [{"type": "text", "text": prompt}],
    }
    resp = _requests.post(
        url,
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": project_id,
        },
        json=payload,
        timeout=180,  # Lyria puede tardar 60-90s
    )
    resp.raise_for_status()
    return resp.json()


async def generate_music(
    prompt: str,
    model: str | None = None,
    duration_seconds: int = 30,
) -> dict[str, Any]:
    """
    Genera música con Google Lyria 3.
    Modelos: lyria-3-pro-preview (canciones completas), lyria-3-clip-preview (30s clips).

    En Vertex AI usa el endpoint REST /interactions (location=global).
    En AI Studio usa generate_content() estándar.

    Refs:
      - Vertex AI: https://cloud.google.com/vertex-ai/generative-ai/docs/music/overview
      - AI Studio: https://ai.google.dev/gemini-api/docs/music-generation
    """
    model = model or _get_configured_model("music") or "lyria-3-pro-preview"
    out_dir = _ensure_output_dir()
    ts = int(time.time())

    logger.info(f"[media] Generando música: model={model}, prompt={prompt[:80]}...")

    loop = asyncio.get_running_loop()
    saved_files = []
    lyrics_parts = []

    # ── Vertex AI: usar REST API /interactions ──
    if _is_vertex():
        project_id = config.get("providers", "google", "project_id", default="")
        try:
            data = await loop.run_in_executor(
                None, lambda: _lyria_vertex_rest(prompt, model, project_id)
            )
            audio_idx = 0
            for output in data.get("outputs", []):
                out_type = output.get("type", "")
                if out_type == "audio" and output.get("data"):
                    import base64 as _b64
                    audio_bytes = _b64.b64decode(output["data"])
                    mime = output.get("mime_type", "") or "audio/mp3"
                    ext = "mp3"
                    if "wav" in mime:
                        ext = "wav"
                    fname = f"music_{ts}_{audio_idx}.{ext}"
                    fpath = out_dir / fname
                    fpath.write_bytes(audio_bytes)
                    saved_files.append({
                        "path": str(fpath),
                        "filename": fname,
                        "mime_type": mime or "audio/mpeg",
                    })
                    audio_idx += 1
                    logger.info(f"[media] Música guardada: {fpath}")
                elif out_type == "text" and output.get("text"):
                    lyrics_parts.append(output["text"])

            return {
                "success": len(saved_files) > 0,
                "model": model,
                "message": f"{len(saved_files)} pista(s) generada(s) con {model}",
                "count": len(saved_files),
                "files": saved_files,
                "lyrics": "\n".join(lyrics_parts) if lyrics_parts else None,
            }
        except Exception as vertex_exc:
            logger.warning(f"[media] Lyria Vertex AI REST falló: {vertex_exc}")
            # Fallback a AI Studio
            ai_client = _get_ai_studio_client()
            if not ai_client:
                raise ValueError(
                    f"Lyria falló en Vertex AI ({vertex_exc}) y no hay API key para fallback."
                ) from vertex_exc
            logger.info("[media] Intentando Lyria via AI Studio fallback...")
            client = ai_client
    else:
        client = _get_client()

    # ── AI Studio: usar generate_content() estándar ──
    def _sync_gen():
        return client.models.generate_content(
            model=model,
            contents=prompt,
        )

    response = await loop.run_in_executor(None, _sync_gen)

    if response.candidates:
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                audio_idx = 0
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        mime = part.inline_data.mime_type or "audio/mp3"
                        ext = "mp3"
                        if "wav" in mime:
                            ext = "wav"
                        elif "ogg" in mime:
                            ext = "ogg"
                        fname = f"music_{ts}_{audio_idx}.{ext}"
                        fpath = out_dir / fname
                        fpath.write_bytes(part.inline_data.data)
                        saved_files.append({
                            "path": str(fpath),
                            "filename": fname,
                            "mime_type": mime,
                        })
                        audio_idx += 1
                        logger.info(f"[media] Música guardada: {fpath}")
                    elif part.text:
                        lyrics_parts.append(part.text)

    return {
        "success": len(saved_files) > 0,
        "model": model,
        "message": f"{len(saved_files)} pista(s) generada(s) con {model}",
        "count": len(saved_files),
        "files": saved_files,
        "lyrics": "\n".join(lyrics_parts) if lyrics_parts else None,
    }
