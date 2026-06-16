"""
G-Mini Agent — Simulated Real-Time Voice.
Pipeline STT → LLM → TTS para modelos que no soportan voz nativa en tiempo real.
Usa Faster-Whisper para transcripción, el modelo de texto actual para generación,
y MeloTTS / ElevenLabs para síntesis de voz.
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
from typing import Any, Callable, Coroutine

from loguru import logger

from backend.config import config
from backend.providers.base import LLMMessage
from backend.core.emotion_tags import EmotionTagFilter
from backend.api.websocket_handler import emit_emotion


class SimulatedRealtimeVoice:
    """
    Voz simulada en tiempo real:
      Mic PCM16 16 kHz → Detección de silencio → STT → LLM (streaming) → TTS → Audio PCM16 24 kHz → Frontend.

    Usa las mismas callbacks que RealTimeVoice para que el frontend no necesite cambios.
    """

    # ── Configuración VAD / Silencio ──────────────────────

    # RMS normalizado bajo el cual se considera silencio (0.0–1.0 de Int16 rango)
    _SILENCE_RMS_THRESHOLD = 300  # ~0.9% de 32768

    # Cuántos ms continuos de silencio se requieren para considerar que el usuario dejó de hablar
    _SILENCE_TRIGGER_MS = 1200

    # Mínimo de audio acumulado (ms) antes de intentar transcribir (evitar fragmentos de <0.5s)
    _MIN_AUDIO_MS = 500

    # Tamaño de chunk para enviar audio TTS al frontend (24kHz PCM16 mono, ~100ms)
    _TTS_CHUNK_SAMPLES = 6000  # 250ms a 24kHz — chunks mas grandes reducen gaps entre buffers

    # ── Sample rates ──────────────────────────────────────

    _INPUT_SAMPLE_RATE = 16000   # Mic capture rate (from frontend)
    _OUTPUT_SAMPLE_RATE = 24000  # Playback rate (matching frontend AudioContext)
    _MELOTTS_SAMPLE_RATE = 22050  # MeloTTS output rate

    def __init__(self):
        self._active = False
        self._processing = False  # True mientras ejecuta STT→LLM→TTS
        self._audio_buffer = bytearray()  # PCM16 16kHz mono acumulado

        # Tracking de silencio
        self._silence_frames = 0
        self._has_speech = False  # True si hemos detectado voz en el buffer actual

        # Callbacks (misma interfaz que RealTimeVoice)
        self._on_audio: Callable | None = None
        self._on_text: Callable | None = None
        self._on_user_text: Callable | None = None
        self._on_turn_complete: Callable | None = None

        # Dependencias inyectadas en start_session
        self._voice_engine: Any = None
        self._model_router: Any = None
        self._memory: Any = None
        self._system_prompt: str = ""
        self._planner: Any = None   # ActionPlanner para ejecutar [ACTION:...]
        self._sio: Any = None       # Socket.IO server para emitir eventos al frontend
        self._sid: str = ""         # Session ID del cliente conectado

        # Task de procesamiento en background
        self._process_task: asyncio.Task | None = None

    # ── Ciclo de vida ─────────────────────────────────────

    async def start_session(
        self,
        voice_engine,
        model_router,
        memory,
        system_prompt: str = "",
        voice_prompt: str = "",
        on_audio: Callable[[bytes], Coroutine] | None = None,
        on_text: Callable[[str], Coroutine] | None = None,
        on_user_text: Callable[[str], Coroutine] | None = None,
        on_turn_complete: Callable[[], Coroutine] | None = None,
        planner: Any = None,
        sio: Any = None,
        sid: str = "",
    ) -> bool:
        """Inicia la sesión simulada de realtime voice."""
        if not voice_engine or not voice_engine.stt_available:
            logger.error("SimulatedRT: STT no disponible — no se puede iniciar")
            return False

        if not voice_engine.tts_available:
            logger.warning("SimulatedRT: TTS no disponible — las respuestas serán solo texto")

        if not model_router:
            logger.error("SimulatedRT: ModelRouter no disponible")
            return False

        self._voice_engine = voice_engine
        self._model_router = model_router
        self._memory = memory
        self._on_audio = on_audio
        self._on_text = on_text
        self._on_user_text = on_user_text
        self._on_turn_complete = on_turn_complete
        self._planner = planner
        self._sio = sio
        self._sid = sid

        self._active = True
        self._processing = False
        self._audio_buffer.clear()
        self._silence_frames = 0
        self._has_speech = False

        # System prompt: base del agente + instrucciones de voz (ambos configurables por el usuario)
        parts = [p for p in (system_prompt, voice_prompt) if p]
        self._system_prompt = "\n\n".join(parts)

        logger.info(
            f"SimulatedRT: sesión iniciada "
            f"(STT: {voice_engine.stt_available}, TTS: {voice_engine.tts_engine_name}, "
            f"planner={'OK' if planner else 'NO'})"
        )
        logger.trace(
            f"SimulatedRT SYSTEM PROMPT [len={len(self._system_prompt)}]:\n"
            f"--- SYSTEM PROMPT START ---\n{self._system_prompt}\n--- SYSTEM PROMPT END ---"
        )
        logger.debug(
            f"SimulatedRT: system_prompt_len={len(system_prompt)}, "
            f"voice_prompt_len={len(voice_prompt) if voice_prompt else 0}, "
            f"combined_len={len(self._system_prompt)}, "
            f"has_mcp_context={'mcpcontrol' in self._system_prompt.lower()}, "
            f"has_action_examples={'[ACTION:' in self._system_prompt}"
        )
        return True

    async def stop_session(self) -> None:
        """Detiene la sesión simulada."""
        self._active = False

        if self._process_task and not self._process_task.done():
            self._process_task.cancel()
            try:
                await self._process_task
            except (asyncio.CancelledError, Exception):
                pass
            self._process_task = None

        self._audio_buffer.clear()
        self._processing = False
        self._has_speech = False
        self._silence_frames = 0
        logger.info("SimulatedRT: sesión detenida")

    # ── Recepción de audio ────────────────────────────────

    async def send_audio(self, audio_chunk: bytes) -> None:
        """
        Recibe un chunk de audio PCM16 16kHz mono del micrófono.
        Acumula y detecta silencio para disparar el pipeline.
        """
        if not self._active:
            return

        # Si estamos procesando (STT→LLM→TTS), ignorar audio entrante
        # para evitar que el agente se escuche a sí mismo (barge-in básico)
        if self._processing:
            return

        self._audio_buffer.extend(audio_chunk)

        # Calcular RMS del chunk para detección de voz/silencio
        rms = self._calculate_rms(audio_chunk)

        if rms > self._SILENCE_RMS_THRESHOLD:
            # Hay voz
            self._has_speech = True
            self._silence_frames = 0
        else:
            # Silencio
            if self._has_speech:
                # Calcular duración del chunk en ms (PCM16 = 2 bytes/sample, mono)
                chunk_duration_ms = (len(audio_chunk) / 2) / self._INPUT_SAMPLE_RATE * 1000
                self._silence_frames += 1
                accumulated_silence_ms = self._silence_frames * chunk_duration_ms

                if accumulated_silence_ms >= self._SILENCE_TRIGGER_MS:
                    # Suficiente silencio — verificar que haya audio útil
                    buffer_duration_ms = (len(self._audio_buffer) / 2) / self._INPUT_SAMPLE_RATE * 1000

                    if buffer_duration_ms >= self._MIN_AUDIO_MS:
                        # Lanzar procesamiento en background
                        audio_data = bytes(self._audio_buffer)
                        self._audio_buffer.clear()
                        self._has_speech = False
                        self._silence_frames = 0
                        self._process_task = asyncio.create_task(
                            self._process_utterance(audio_data)
                        )
                    else:
                        # Muy poco audio, descartar (probablemente ruido)
                        self._audio_buffer.clear()
                        self._has_speech = False
                        self._silence_frames = 0

    # ── Pipeline STT → LLM → TTS ─────────────────────────

    async def _process_utterance(self, audio_pcm16: bytes) -> None:
        """Pipeline completo: transcribe → genera → sintetiza → emite."""
        self._processing = True
        try:
            # ── 1. STT: PCM16 16kHz → texto ──────────────
            wav_bytes = self._pcm16_to_wav(audio_pcm16, self._INPUT_SAMPLE_RATE)
            user_text = await self._voice_engine.transcribe(wav_bytes)
            user_text = user_text.strip()

            if not user_text:
                logger.debug("SimulatedRT: STT no detectó texto — descartando")
                return

            logger.info(f"SimulatedRT STT: {user_text!r}")

            # Notificar transcripción del usuario al frontend
            if self._on_user_text:
                await self._on_user_text(user_text)

            # Agregar a memoria
            if self._memory:
                try:
                    self._memory.add_user_message(user_text)
                    await self._memory.persist_message("user", user_text)
                except Exception as exc:
                    logger.warning(f"SimulatedRT: no se pudo persistir mensaje usuario: {exc}")

            # ── 2. Bucle agentico: generar → ejecutar → confirmar/continuar ──
            # Antes era de un solo turno: generaba, ejecutaba acciones y se iba a idle
            # SIN confirmar que termino. El usuario tenia que decir "ya terminaste?".
            # Ahora, tras ejecutar acciones, se hace otro turno para que el modelo vea
            # el resultado y confirme (o continue con mas pasos). Acotado por max_turns
            # para evitar bucles infinitos.
            voice_max_tokens = int(config.get("voice", "max_tokens", default=8192))
            max_turns = int(config.get("voice", "max_agentic_turns", default=5))

            for iteration in range(1, max_turns + 1):
                if not self._active:
                    return

                messages = (
                    self._build_messages(user_text)
                    if iteration == 1
                    else self._build_messages_base()
                )
                logger.info(f"SimulatedRT: turno agentico {iteration}/{max_turns}")

                raw_response = await self._generate_and_speak(messages, max_tokens=voice_max_tokens)
                if not self._active:
                    return

                # ── Ejecutar acciones [ACTION:...] del LLM ──
                actions_ran = await self._execute_llm_actions(raw_response)

                # Si el turno NO produjo acciones, el modelo dio su respuesta final
                # (la confirmacion / contestacion) → terminamos el bucle.
                if not actions_ran:
                    break
            else:
                logger.warning(
                    f"SimulatedRT: alcanzado max_agentic_turns={max_turns}; "
                    "cerrando turno para evitar bucle infinito"
                )

            # Señalar fin de turno
            if self._on_turn_complete:
                await self._on_turn_complete()

        except asyncio.CancelledError:
            logger.info("SimulatedRT: procesamiento cancelado")
        except Exception as exc:
            logger.error(f"SimulatedRT pipeline error: {exc}", exc_info=True)
            # Intentar notificar al frontend del error
            if self._on_text:
                try:
                    await self._on_text(f"\n[Error de voz simulada: {exc}]")
                except Exception:
                    pass
            if self._on_turn_complete:
                try:
                    await self._on_turn_complete()
                except Exception:
                    pass
        finally:
            self._processing = False

    async def _flush_message_bubble(self) -> None:
        """Cierra la burbuja de chat en streaming actual (emite done=True) para que
        el siguiente turno del bucle agentico empiece en una burbuja NUEVA. Sin esto,
        el texto de varios turnos ("Voy a abrir..." + "Listo, terminé.") se mezclaba
        en una sola burbuja. Burbujas vacias se descartan solas en el frontend."""
        if not self._sio or not self._sid:
            return
        try:
            from backend.api.websocket_handler import emit_message_done
            await emit_message_done(self._sid)
        except Exception as exc:
            logger.debug(f"SimulatedRT: no se pudo cerrar burbuja: {exc}")

    async def _generate_and_speak(self, messages: list[LLMMessage], *, max_tokens: int) -> str:
        """Un turno de generacion: stream del LLM → emite texto visible (ocultando
        bloques [ACTION:...]) → TTS progresivo → persiste el texto limpio en memoria.
        Retorna la respuesta RAW (con bloques de accion) para parsear acciones.

        NOTA: max_tokens alto (8192) es CRITICO con modelos de razonamiento
        (gemini-3.x thinking). Los tokens de pensamiento consumen el presupuesto
        de max_output_tokens; con 1024 el modelo "pensaba" ~700-1100 tokens y la
        salida visible (incluyendo bloques [ACTION:...]) se truncaba a la mitad.
        """
        # Cerrar cualquier burbuja anterior para que este turno tenga la suya propia.
        await self._flush_message_bubble()

        raw_response = ""
        visible_response = ""
        tts_pending = ""  # SOLO texto visible (sin [ACTION:...]) aun no sintetizado

        emotions_enabled = config.get("character", "emotions_enabled", default=False)
        emotion_filter = EmotionTagFilter() if emotions_enabled else None

        async for text_chunk in self._model_router.generate(
            messages=messages,
            model=self._model_router.get_current_model(),
            provider_name=self._model_router.get_current_provider_name(),
            temperature=0.7,
            max_tokens=max_tokens,
            stream=True,
        ):
            if not self._active:
                return raw_response  # Sesión detenida durante generación

            raw_response += text_chunk

            # Texto visible = respuesta acumulada SIN bloques [ACTION:...]
            next_visible_response = self._strip_action_blocks(raw_response)
            if not next_visible_response.startswith(visible_response):
                logger.warning(
                    "SimulatedRT: delta visible no monotónica; se reenviará texto limpio completo "
                    f"(prev_len={len(visible_response)}, next_len={len(next_visible_response)})"
                )
                visible_response = ""
                tts_pending = ""
                if emotion_filter:
                    emotion_filter = EmotionTagFilter()
            visible_delta = next_visible_response[len(visible_response):]
            visible_response = next_visible_response

            if emotion_filter:
                visible_delta, emotion = emotion_filter.feed(visible_delta)
                if emotion and self._sio and self._sid:
                    await emit_emotion(self._sid, emotion)

            if visible_delta and self._on_text:
                await self._on_text(visible_delta)

            # TTS SOLO sobre texto visible. ANTES se extraian oraciones del buffer RAW;
            # el codigo dentro de [ACTION:create_file(content="...")] (lleno de . ; \n)
            # se partia en fragmentos sueltos que ya no contenian el marcador [ACTION:,
            # asi que el strip por-fragmento no los reconocia y el TTS leia el codigo.
            tts_pending += visible_delta
            sentences, tts_pending = self._extract_complete_sentences(tts_pending)
            for sentence in sentences:
                await self._synthesize_and_emit(sentence)

        if emotion_filter:
            rest = emotion_filter.flush()
            if rest:
                if self._on_text:
                    await self._on_text(rest)
                tts_pending += rest

        # Sintetizar el texto visible restante
        if tts_pending.strip():
            await self._synthesize_and_emit(tts_pending.strip())

        # Log completo de la respuesta raw del LLM
        logger.trace(
            f"SimulatedRT LLM RAW RESPONSE [len={len(raw_response)}]:\n"
            f"--- RAW RESPONSE START ---\n{raw_response}\n--- RAW RESPONSE END ---"
        )
        logger.debug(
            f"SimulatedRT: LLM response len={len(raw_response)}, "
            f"has_action_blocks={'[ACTION:' in raw_response}, "
            f"has_mcp_call={'mcp_call_tool' in raw_response}, "
            f"model={self._model_router.get_current_model()}, "
            f"provider={self._model_router.get_current_provider_name()}"
        )

        # Persistir respuesta del agente
        clean_response = self._strip_action_blocks(raw_response).strip()
        if raw_response != clean_response:
            import re as _re_strip
            stripped_blocks = _re_strip.findall(r'\[ACTION:[^\]]*\]', raw_response)
            loose_actions = _re_strip.findall(r'\bACTION\s*:\s*\w+[^\n]*', raw_response, flags=_re_strip.IGNORECASE)
            logger.warning(
                f"SimulatedRT: bloques ACTION removidos de la respuesta visible "
                f"(raw_len={len(raw_response)}, clean_len={len(clean_response)}, "
                f"complete_blocks={stripped_blocks}, loose_patterns={loose_actions})"
            )
        if clean_response and self._memory:
            try:
                self._memory.add_assistant_message(clean_response)
                await self._memory.persist_message("assistant", clean_response)
            except Exception as exc:
                logger.warning(f"SimulatedRT: no se pudo persistir respuesta agente: {exc}")

        return raw_response

    async def _execute_llm_actions(self, full_response: str) -> bool:
        """Parsea y ejecuta acciones [ACTION:...] de la respuesta del LLM.
        Emite eventos al frontend. Si hay imagen (screenshot), hace segundo turno LLM
        con la imagen para que el modelo pueda describirla.
        Retorna True si se ejecutaron acciones (para que el bucle agentico continue),
        False si no habia acciones (el modelo dio su respuesta final)."""
        if not self._planner or not self._sio or not self._sid:
            if "[ACTION:" in full_response:
                logger.warning(
                    "SimulatedRT: respuesta contiene [ACTION:...] pero planner/sio/sid no disponibles — "
                    f"planner={'OK' if self._planner else 'NONE'}, "
                    f"sio={'OK' if self._sio else 'NONE'}, "
                    f"sid={self._sid!r}"
                )
            return False

        logger.debug(
            f"SimulatedRT _execute_llm_actions: response_len={len(full_response)}, "
            f"contains_ACTION={'[ACTION:' in full_response}, "
            f"contains_mcp={'mcp_call_tool' in full_response}"
        )

        try:
            actions = self._planner.parse_actions(full_response)
        except Exception as exc:
            logger.error(f"SimulatedRT: error parseando acciones: {exc}", exc_info=True)
            return False

        if not actions:
            if "[ACTION:" in full_response or "ACTION:" in full_response:
                import re as _re_dbg
                potential = _re_dbg.findall(r'(?:ACTION|action)[:\s][^\n]{0,200}', full_response)
                logger.warning(
                    f"SimulatedRT: respuesta contiene patrones ACTION pero parser retornó vacío. "
                    f"Patrones encontrados: {potential}"
                )
            return False

        logger.info(f"SimulatedRT: ejecutando {len(actions)} acción(es) del LLM")

        from backend.core.planner import set_planner_socket
        from backend.api.websocket_handler import emit_screenshot, emit_media

        set_planner_socket(self._sio, self._sid)

        try:
            await self._sio.emit("agent:executing", {"active": True}, to=self._sid)
            results = await self._planner.execute_actions(actions)
        except Exception as exc:
            logger.error(f"SimulatedRT: error ejecutando acciones: {exc}", exc_info=True)
            results = []
        finally:
            await self._sio.emit("agent:executing", {"active": False}, to=self._sid)

        # Recopilar imágenes de acciones visuales para segundo turno LLM
        captured_images: list[str] = []

        for idx, result in enumerate(results or []):
            action_name = str(result.get("action", ""))
            success = result.get("success", False)
            data = result.get("data") or {}
            message = str(result.get("message", "")).strip()

            # Persistir la accion como tarjeta en la conversacion (con duracion exacta)
            # para que se conserve y se re-renderice como card al recargar el historial.
            action_obj = actions[idx] if idx < len(actions) else None
            if self._memory and action_obj is not None:
                try:
                    await self._memory.persist_message(
                        "display",
                        message or action_name,
                        message_type="action",
                        metadata={
                            "tool_name": action_name,
                            "params": action_obj.params,
                            "success": bool(success),
                            "duration_ms": result.get("duration_ms"),
                            "result_preview": message[:200],
                        },
                    )
                except Exception as exc:
                    logger.debug(f"SimulatedRT: no se pudo persistir tarjeta de accion: {exc}")

            # Screenshot: emitir al chat Y guardar imagen para LLM
            if (
                action_name in {"screenshot", "browser_screenshot", "adb_screenshot"}
                and success
                and isinstance(data, dict)
            ):
                raw_b64 = str(data.get("image_base64") or "").strip()
                # Limpiar prefijo data: si viene incluido (evitar doble prefijo)
                if raw_b64.startswith("data:") and "," in raw_b64:
                    raw_b64 = raw_b64.split(",", 1)[1]
                if raw_b64 and len(raw_b64) > 100:
                    captured_images.append(raw_b64)
                    dims = data.get("screen_dimensions") or {}
                    dims_text = ""
                    if isinstance(dims, dict) and dims.get("sent_w") and dims.get("sent_h"):
                        dims_text = f" sent={dims.get('sent_w')}x{dims.get('sent_h')}"
                    try:
                        await emit_screenshot(self._sid, raw_b64)
                        logger.info(
                            "SimulatedRT: screenshot emitido al frontend "
                            f"(action={action_name}, b64_chars={len(raw_b64)}{dims_text})"
                        )
                    except Exception as exc:
                        logger.error(f"SimulatedRT: error emitiendo screenshot: {exc}")

            # Media generada (imagen/video/musica): emitir como reproductor inline.
            # En modo texto esto lo hace agent.py; en voz simulada hay que hacerlo aqui
            # o NO aparece ninguna preview de la imagen/cancion/video generado.
            if (
                action_name in ("generate_image", "generate_video", "generate_music")
                and isinstance(data, dict)
            ):
                media_type = (
                    "image" if action_name == "generate_image"
                    else "video" if action_name == "generate_video"
                    else "audio"
                )
                for gen_file in (data.get("files") or []):
                    fname = str(gen_file.get("filename") or "")
                    if not fname:
                        continue
                    try:
                        await emit_media(self._sid, media_type, fname, f"/api/media/{fname}")
                        logger.info(f"SimulatedRT: media emitida ({media_type}, {fname})")
                    except Exception as exc:
                        logger.error(f"SimulatedRT: error emitiendo media {fname}: {exc}")

            # NOTA: el resultado de cada accion ya se muestra como tarjeta con icono
            # SVG (agent:action / agent:action_result que emite el planner). NO emitir
            # filas de texto "✅ **mcp_call_tool**: ..." — eran redundantes y se veian mal.

        # ── Segundo turno LLM si hay imágenes capturadas ──────────────────
        # El modelo recibe la imagen y genera descripción/análisis → TTS
        # STT permanece bloqueado (self._processing=True) durante todo este bloque
        if captured_images and self._model_router:
            # Cerrar la burbuja del texto previo ("voy a tomar captura") para que la
            # descripcion visual aparezca en su propia burbuja, no mezclada.
            await self._flush_message_bubble()
            analysis_images = captured_images[-1:]
            if len(captured_images) > 1:
                logger.info(
                    "SimulatedRT: multiples screenshots en un turno; se usara la ultima imagen "
                    f"para el analisis visual (capturadas={len(captured_images)})"
                )
            provider_name = self._model_router.get_current_provider_name()
            model_name = self._model_router.get_current_model()
            logger.info(
                "SimulatedRT: segundo turno visual preparado "
                f"(provider={provider_name}, model={model_name}, images={len(analysis_images)}, "
                f"b64_chars={[len(img) for img in analysis_images]})"
            )
            logger.info("SimulatedRT: segundo turno LLM con imagen para análisis visual")
            try:
                # Construir mensaje de feedback incluyendo la imagen
                # La version efectiva del prompt se redefine abajo para asegurar
                # que la imagen viaje como contexto visual real y no quede ambigua.
                vision_prompt = (
                    "Sistema: Acción 'screenshot' completada. Aquí está la imagen capturada. "
                    "Continúa respondiendo al usuario basándote en esta imagen según lo que te pidió."
                )
                vision_prompt = (
                    "Analiza la captura de pantalla adjunta y responde al ultimo pedido del usuario "
                    "basandote solo en esta imagen. Describe la app, sitio o ventana principal, "
                    "el contenido visible mas importante y cualquier texto claramente legible que sea relevante. "
                    "Si algo no se distingue con certeza, dilo explicitamente. "
                    "Responde en 1 a 3 frases completas y no dejes la ultima frase inconclusa."
                )
                vision_msg = LLMMessage(
                    role="user",
                    content=vision_prompt,
                    images=analysis_images,
                )
                # No persistir este prompt auxiliar como mensaje de usuario:
                # solo sirve para el segundo turno visual actual.

                # Construir historial de mensajes + imagen
                vision_messages = self._build_messages_base() + [vision_msg]

                vision_response = ""
                vision_sentence_buffer = ""
                vision_chunk_count = 0

                # max_tokens alto: modelos thinking consumen presupuesto pensando;
                # 512 truncaba la descripcion visual (de ahi el parche de "repair" abajo).
                vision_max_tokens = int(config.get("voice", "vision_max_tokens", default=4096))
                async for chunk in self._model_router.generate(
                    messages=vision_messages,
                    model=model_name,
                    provider_name=provider_name,
                    temperature=0.7,
                    max_tokens=vision_max_tokens,
                    stream=True,
                ):
                    if not self._active:
                        break
                    vision_chunk_count += 1
                    vision_response += chunk
                    vision_sentence_buffer += chunk

                    if self._on_text:
                        await self._on_text(chunk)

                    sentences, vision_sentence_buffer = self._extract_complete_sentences(vision_sentence_buffer)
                    for sentence in sentences:
                        await self._synthesize_and_emit(sentence)

                if self._looks_incomplete_response(vision_response):
                    logger.warning(
                        "SimulatedRT: la respuesta visual parece incompleta; solicitando cierre "
                        f"(chars={len(vision_response.strip())}, tail={vision_response.strip()[-120:]!r})"
                    )
                    repair_prompt = (
                        "Tu respuesta anterior quedo incompleta. Completala ahora usando la misma captura. "
                        "No repitas todo; termina la idea en 1 o 2 frases completas."
                    )
                    repair_msg = LLMMessage(
                        role="user",
                        content=repair_prompt,
                        images=analysis_images,
                    )
                    repair_max_tokens = int(config.get("voice", "vision_repair_max_tokens", default=2048))
                    async for chunk in self._model_router.generate(
                        messages=vision_messages + [LLMMessage(role="assistant", content=vision_response)] + [repair_msg],
                        model=model_name,
                        provider_name=provider_name,
                        temperature=0.4,
                        max_tokens=repair_max_tokens,
                        stream=True,
                    ):
                        if not self._active:
                            break
                        vision_chunk_count += 1
                        vision_response += chunk
                        vision_sentence_buffer += chunk

                        if self._on_text:
                            await self._on_text(chunk)

                        sentences, vision_sentence_buffer = self._extract_complete_sentences(vision_sentence_buffer)
                        for sentence in sentences:
                            await self._synthesize_and_emit(sentence)

                if vision_sentence_buffer.strip():
                    await self._synthesize_and_emit(vision_sentence_buffer.strip())

                if vision_response.strip() and self._memory:
                    try:
                        self._memory.add_assistant_message(vision_response.strip())
                        await self._memory.persist_message("assistant", vision_response.strip())
                    except Exception:
                        pass

                logger.info("SimulatedRT: análisis visual completado")

                logger.info(
                    "SimulatedRT: analisis visual completado "
                    f"(chunks={vision_chunk_count}, chars={len(vision_response.strip())}, "
                    f"tail={vision_response.strip()[-120:]!r})"
                )
            except Exception as exc:
                logger.error(f"SimulatedRT: error en segundo turno LLM con imagen: {exc}", exc_info=True)
            # El analisis visual ya se hablo; el bucle agentico hara un turno mas
            # para que el modelo confirme/continue.
            return True

        # Feedback de acciones no-visuales al contexto del LLM — el siguiente turno
        # del bucle agentico lo lee para confirmar la finalizacion o continuar.
        # SOLO en memoria (no se persiste a la DB): las tarjetas de accion ya se
        # guardaron arriba como historial visible; persistir este texto crudo lo
        # mostraria como una burbuja de "usuario" fea al recargar la conversacion.
        if results:
            feedback_lines = []
            for r in results:
                status = "OK" if r.get("success") else "ERROR"
                duration = r.get("duration_ms")
                dur_text = f" ({float(duration) / 1000:.1f}s)" if duration is not None else ""
                feedback_lines.append(
                    f"[{status}] {r.get('action', '?')}{dur_text}: {str(r.get('message', ''))[:120]}"
                )
            feedback_text = "Resultado de acciones:\n" + "\n".join(feedback_lines)
            if self._memory:
                try:
                    self._memory.add_user_message(feedback_text)
                except Exception as exc:
                    logger.debug(f"SimulatedRT: no se pudo agregar feedback al contexto: {exc}")

        # Se ejecutaron acciones → el bucle agentico continua (confirmacion/siguiente paso).
        return True

    async def _synthesize_and_emit(self, text: str) -> None:
        """Sintetiza una oración con TTS y emite los chunks de audio al frontend."""
        if not text.strip() or not self._on_audio:
            return

        # Filtrar bloques de acción [ACTION:...] — no se leen por voz
        clean = self._strip_action_blocks(text)
        if not clean.strip():
            return

        if not self._voice_engine or not self._voice_engine.tts_available:
            return

        try:
            audio_wav = await self._voice_engine.synthesize(clean)
            if not audio_wav:
                return

            # Convertir WAV a PCM16 24kHz (formato que espera el frontend)
            pcm16_24k = self._wav_to_pcm16_24k(audio_wav)
            if not pcm16_24k:
                return

            # Enviar en chunks de ~100ms para streaming suave
            chunk_bytes = self._TTS_CHUNK_SAMPLES * 2  # 2 bytes per sample (PCM16)
            offset = 0
            while offset < len(pcm16_24k) and self._active:
                end = min(offset + chunk_bytes, len(pcm16_24k))
                chunk = pcm16_24k[offset:end]
                await self._on_audio(chunk)
                offset = end
                # Pausa proporcional al chunk para sincronizar envío con reproducción.
                # Mantiene _processing=True mientras el agente "habla", evitando
                # que el STT escuche ecos del TTS (barge-in simple).
                # Usar ~220ms para chunks de 250ms: deja 30ms de margen para procesamiento.
                await asyncio.sleep(0.22)

        except Exception as exc:
            logger.error(f"SimulatedRT TTS error: {exc}")

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _strip_action_blocks(text: str) -> str:
        """Elimina bloques [ACTION:...] y variantes para que NO se lean por TTS ni se
        muestren en el chat.

        Usa el MISMO parser de brackets balanceados que el planner
        (`_extract_action_matches`, que respeta comillas y escapes). El regex anterior
        `\\[ACTION:[^\\]]*\\]` se cortaba en el primer ']' interno: un bloque como
        `[ACTION:create_file(content="... arr=[1,2,3] ...")]` quedaba a medio remover y
        el codigo (HTML/CSS/JS, lleno de ']') terminaba leido por el TTS.
        """
        import re
        cleaned = text
        try:
            from backend.core.planner import _extract_action_matches
            for _type, _params, raw_action in _extract_action_matches(cleaned):
                cleaned = cleaned.replace(raw_action, "", 1)
        except Exception:
            # Fallback al regex simple si el import o el parseo fallan
            cleaned = re.sub(r'\[ACTION:[^\]]*\]', '', cleaned)
        # Remover cualquier bloque incompleto al final (el LLM aun no cerro el ']').
        # DOTALL para abarcar codigo multilinea con ']' internos.
        cleaned = re.sub(r'\[ACTION:.*$', '', cleaned, flags=re.DOTALL)
        # Variante sin corchetes que algunos modelos generan: "ACTION: screenshot"
        cleaned = re.sub(r'\bACTION\s*:\s*\w+', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _build_messages_base(self) -> list[LLMMessage]:
        """Construye historial de mensajes sin agregar nuevo mensaje user al final."""
        messages = [LLMMessage(role="system", content=self._system_prompt)]
        if self._memory and hasattr(self._memory, "messages"):
            recent = self._memory.messages[-20:]
            for msg in recent:
                role = msg.get("role", "user") if isinstance(msg, dict) else getattr(msg, "role", "user")
                content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                if role in ("user", "assistant") and content:
                    messages.append(LLMMessage(role=role, content=content))
        return messages

    def _build_messages(self, user_text: str) -> list[LLMMessage]:
        """Construye el array de mensajes para el LLM."""
        messages = self._build_messages_base()

        if not messages or messages[-1].content != user_text:
            messages.append(LLMMessage(role="user", content=user_text))

        logger.debug(
            f"SimulatedRT _build_messages: total={len(messages)}, "
            f"system_len={len(messages[0].content) if messages else 0}, "
            f"user_text={user_text!r}"
        )
        logger.trace(
            f"SimulatedRT MESSAGES PAYLOAD [{len(messages)} msgs]:\n" +
            "\n".join(
                f"  [{i}] role={m.role} len={len(m.content)} "
                f"preview={m.content[:120]!r}..."
                for i, m in enumerate(messages)
            )
        )

        return messages

    @staticmethod
    def _extract_complete_sentences(text: str) -> tuple[list[str], str]:
        """
        Extrae oraciones completas del buffer de texto.
        Retorna (oraciones_completas, texto_restante).
        """
        # Delimitadores de oración para TTS progresivo
        # Nota: ':' eliminado para no partir bloques [ACTION:...] a la mitad
        delimiters = ".!?;\n"
        sentences = []
        last_split = 0

        for i, char in enumerate(text):
            if char in delimiters:
                sentence = text[last_split:i + 1].strip()
                if len(sentence) > 3:  # Ignorar fragmentos muy cortos
                    sentences.append(sentence)
                last_split = i + 1

        remainder = text[last_split:]
        return sentences, remainder

    @staticmethod
    def _looks_incomplete_response(text: str) -> bool:
        """Heuristica simple para detectar respuestas visuales cortadas."""
        normalized = str(text or "").strip()
        if not normalized:
            return False

        if normalized.endswith(("...", "…")):
            return True

        if normalized.count("**") % 2 != 0:
            return True

        if normalized[-1] in ".!?)]}\"'":
            return False

        unfinished_tokens = {
            "a", "al", "ante", "bajo", "con", "contra", "de", "del", "desde",
            "durante", "en", "entre", "hacia", "hasta", "para", "por", "segun",
            "sin", "sobre", "tras", "y", "o", "que", "como", "cuando", "donde",
            "el", "la", "los", "las", "un", "una", "unos", "unas", "tu", "su",
        }
        last_token = normalized.rstrip(")]}\"'").split()[-1].lower()
        return last_token in unfinished_tokens

    @staticmethod
    def _calculate_rms(audio_chunk: bytes) -> float:
        """Calcula el RMS de un chunk de audio PCM16."""
        if len(audio_chunk) < 2:
            return 0.0
        num_samples = len(audio_chunk) // 2
        try:
            samples = struct.unpack(f"<{num_samples}h", audio_chunk[:num_samples * 2])
            sum_sq = sum(s * s for s in samples)
            return math.sqrt(sum_sq / num_samples) if num_samples > 0 else 0.0
        except struct.error:
            return 0.0

    @staticmethod
    def _pcm16_to_wav(pcm16_data: bytes, sample_rate: int) -> bytes:
        """Empaqueta datos PCM16 mono en un archivo WAV en memoria."""
        num_samples = len(pcm16_data) // 2
        data_size = num_samples * 2
        file_size = 36 + data_size

        buf = io.BytesIO()
        # RIFF header
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", file_size))
        buf.write(b"WAVE")
        # fmt chunk
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))       # chunk size
        buf.write(struct.pack("<H", 1))        # PCM format
        buf.write(struct.pack("<H", 1))        # mono
        buf.write(struct.pack("<I", sample_rate))
        buf.write(struct.pack("<I", sample_rate * 2))  # byte rate
        buf.write(struct.pack("<H", 2))        # block align
        buf.write(struct.pack("<H", 16))       # bits per sample
        # data chunk
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(pcm16_data[:data_size])

        return buf.getvalue()

    @staticmethod
    def _wav_to_pcm16_24k(wav_bytes: bytes) -> bytes | None:
        """
        Extrae PCM16 de un WAV y lo resamplea a 24 kHz.
        Soporta WAV de cualquier sample rate (típicamente 22050 de MeloTTS).
        """
        if len(wav_bytes) < 44:
            return None

        try:
            # Parsear header WAV mínimo
            if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
                # No es WAV — podría ser MP3 u otro formato, devolver tal cual
                logger.warning("SimulatedRT: audio no es WAV, intentando como PCM16 raw")
                return wav_bytes

            # Leer sample rate del fmt chunk
            fmt_offset = wav_bytes.find(b"fmt ")
            if fmt_offset < 0:
                return None

            src_sample_rate = struct.unpack_from("<I", wav_bytes, fmt_offset + 12)[0]
            bits_per_sample = struct.unpack_from("<H", wav_bytes, fmt_offset + 22)[0]
            channels = struct.unpack_from("<H", wav_bytes, fmt_offset + 10)[0]

            # Encontrar data chunk
            data_offset = wav_bytes.find(b"data")
            if data_offset < 0:
                return None

            data_size = struct.unpack_from("<I", wav_bytes, data_offset + 4)[0]
            raw_data = wav_bytes[data_offset + 8:data_offset + 8 + data_size]

            # Convertir a mono si es estéreo
            if channels == 2 and bits_per_sample == 16:
                samples = struct.unpack(f"<{len(raw_data) // 2}h", raw_data)
                mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
                raw_data = struct.pack(f"<{len(mono)}h", *mono)

            # Resamplear de src_sample_rate a 24000 Hz (interpolación lineal)
            target_rate = 24000
            if src_sample_rate == target_rate:
                return raw_data

            src_samples = struct.unpack(f"<{len(raw_data) // 2}h", raw_data)
            src_len = len(src_samples)
            ratio = src_sample_rate / target_rate
            target_len = int(src_len / ratio)

            resampled = []
            for i in range(target_len):
                src_pos = i * ratio
                idx = int(src_pos)
                frac = src_pos - idx

                if idx + 1 < src_len:
                    # Interpolación lineal
                    sample = src_samples[idx] * (1 - frac) + src_samples[idx + 1] * frac
                else:
                    sample = src_samples[min(idx, src_len - 1)]

                resampled.append(max(-32768, min(32767, int(sample))))

            return struct.pack(f"<{len(resampled)}h", *resampled)

        except Exception as exc:
            logger.error(f"SimulatedRT: error convirtiendo WAV a PCM16 24k: {exc}")
            return None

    # ── Properties ────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active
