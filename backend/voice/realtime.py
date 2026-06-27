"""
G-Mini Agent — Real-Time Voice.
Voz en tiempo real usando APIs WebSocket de OpenAI, Google y xAI.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import Any, AsyncGenerator, Callable
import yaml
from pathlib import Path

from loguru import logger

from backend.config import config
from backend.core.avatar_context import build_avatar_context
from backend.core.modes import build_autonomy_context

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    logger.info("websockets no disponible — Real-Time Voice deshabilitada")

try:
    import mss
    from PIL import Image
    HAS_SCREEN_CAPTURE = True
except ImportError:
    HAS_SCREEN_CAPTURE = False
    logger.info("mss/Pillow no disponible — Screen streaming deshabilitado")


class RealTimeVoice:
    """
    Voz en tiempo real via WebSocket.
    Soporta:
    - OpenAI Realtime API (gpt-realtime-1.5, gpt-realtime-mini)
    - Google Gemini Live API (gemini-3.1-flash-live-preview)
    - xAI Voice Agent API (wss://api.x.ai/v1/realtime)
    """

    _cached_providers = None

    @classmethod
    def get_realtime_providers(cls) -> dict:
        """Carga y retorna la configuración de modelos RealTime desde un YAML escalable."""
        if cls._cached_providers is not None:
            return cls._cached_providers
        
        # Ruta al archivo YAML dinámico
        config_path = Path(__file__).resolve().parent.parent.parent / "data" / "realtime_models.yaml"
        
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cls._cached_providers = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Error leyendo realtime_models.yaml: {e}")
                cls._cached_providers = {}
        
        # Si el archivo no existe o está vacío, lo creamos con los valores por defecto
        if not cls._cached_providers:
            cls._cached_providers = {
                "openai": {
                    "models": ["gpt-realtime-1.5", "gpt-realtime-mini"],
                    "default": "gpt-realtime-1.5",
                },
                "google": {
                    # IDs oficiales según documentación Google AI (abril 2026)
                    # Los modelos Google se validan además vía live_api en models.yaml
                    "models": [
                        "gemini-2.5-flash-native-audio-preview-12-2025",
                        "gemini-3.1-flash-live-preview",
                    ],
                    "default": "gemini-3.1-flash-live-preview",
                },
                "xai": {
                    "models": ["voice-agent"],
                    "default": "voice-agent",
                },
            }
            # Guardarlo automáticamente para escalabilidad en producción
            try:
                config_path.parent.mkdir(exist_ok=True, parents=True)
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cls._cached_providers, f, default_flow_style=False, allow_unicode=True)
                logger.info(f"Archivo realtime_models.yaml creado en {config_path}")
            except Exception as e:
                logger.error(f"No se pudo crear realtime_models.yaml: {e}")

        return cls._cached_providers

    @staticmethod
    def resolve_rt_provider(text_provider: str) -> str | None:
        """Dado el provider de texto actual, retorna el provider RT correspondiente o None."""
        if text_provider in RealTimeVoice.get_realtime_providers():
            # Vertex AI backend doesn't need an API key — uses ADC
            if text_provider == "google":
                backend = config.get("providers", "google", "backend", default="ai_studio")
                if backend == "vertex_ai":
                    project_id = config.get("providers", "google", "project_id", default="")
                    if project_id:
                        return text_provider
                else:
                    if config.get_api_key("google_api"):
                        return text_provider
                return None

            api_key_map = {"openai": "openai_api", "xai": "xai_api"}
            key_name = api_key_map.get(text_provider)
            if key_name and config.get_api_key(key_name):
                return text_provider
        return None

    @staticmethod
    def get_rt_capabilities() -> dict:
        """Retorna qué providers RT están disponibles (tienen credenciales)."""
        available = {}
        key_map = {"openai": "openai_api", "xai": "xai_api"}
        for prov, info in RealTimeVoice.get_realtime_providers().items():
            if prov == "google":
                backend = config.get("providers", "google", "backend", default="ai_studio")
                if backend == "vertex_ai":
                    if config.get("providers", "google", "project_id", default=""):
                        available[prov] = info["default"]
                elif config.get_api_key("google_api"):
                    available[prov] = info["default"]
                continue

            key_name = key_map.get(prov)
            if key_name and config.get_api_key(key_name):
                available[prov] = info["default"]
        return available

    # 30 voces disponibles en Google Gemini Live / TTS
    # https://ai.google.dev/gemini-api/docs/speech-generation#voices
    GOOGLE_VOICES = [
        "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda",
        "Orus", "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus",
        "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
        "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima",
        "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
    ]

    # Herramientas agénticas expuestas como function_declarations para Google Live API
    # https://ai.google.dev/gemini-api/docs/live-api/tools
    GOOGLE_RT_TOOLS = [{
        "function_declarations": [
            {
                "name": "screenshot",
                "description": (
                    "Captura la pantalla completa del PC (1440x900). "
                    "Devuelve la imagen como frame de video + texto OCR de la pantalla. "
                    "El cursor del mouse aparece como un punto rojo con cruz en la imagen. "
                    "USO EXCLUSIVO: solo como paso previo inmediato a una interacción real con el escritorio "
                    "(click/type/scroll/delegate) que el usuario te pidió. "
                    "NUNCA la uses para conversar, saludar, responder preguntas ni describir qué puedes hacer: "
                    "esas respuestas son solo con palabras. "
                    "Cuando vayas a interactuar: úsala SIEMPRE antes de click/type/scroll y analiza la imagen "
                    "para identificar coordenadas EXACTAS en píxeles. NUNCA hagas click sin screenshot primero."
                ),
            },
            {
                "name": "delegate_computer_use",
                "description": (
                    "Delega una tarea de interacción de escritorio al sub-agente de computer use, que la ejecuta "
                    "de forma autónoma (clicks, escritura, teclas, scroll, arrastrar, abrir/operar ventanas y apps). "
                    "TÚ eres el coordinador: NO haces clicks ni escribes directamente. Describe la tarea COMPLETA en "
                    "lenguaje natural (app exacta, textos a escribir literalmente, botones a presionar, pasos). "
                    "Ejemplo: task=\"Abrir el Bloc de notas desde el menú inicio y escribir 'Hola mundo'\". "
                    "Si la tarea está en otro monitor, indícalo en 'monitor'. Tras delegar recibirás una captura de verificación. "
                    "Toma screenshot primero para decidir QUÉ delegar y en qué monitor."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task": {"type": "STRING", "description": "Descripción clara y completa de la interacción de UI a realizar"},
                        "monitor": {"type": "INTEGER", "description": "Monitor objetivo: 0=actual/predeterminado, 1=primario, 2=secundario"},
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "set_emotion",
                "description": (
                    "Cambia la expresión facial y corporal de TU PROPIO avatar en pantalla "
                    "(sonreír, entristecerte, sorprenderte, etc.). Tu cara es interna, NO un "
                    "botón ni una ventana: NUNCA uses delegate_computer_use, screenshot ni clicks "
                    "para cambiar tu expresión. Llama a esta herramienta cuando el usuario te pida "
                    "sonreír o mostrar una emoción, o cuando sea natural acompañar tu respuesta con "
                    "una expresión (con moderación). La expresión se desvanece sola hacia neutral. "
                    "Es INSTANTÁNEA: llamarla es TODO lo que hace falta — NO tomes screenshot ni "
                    "verifiques el resultado después; no hay nada que comprobar en pantalla. Tras "
                    "set_emotion no ejecutes ninguna otra herramienta para 'confirmar' la expresión."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "emotion": {
                            "type": "STRING",
                            "enum": ["happy", "sad", "angry", "surprised", "relaxed", "neutral"],
                            "description": "Emoción a expresar en el avatar.",
                        },
                    },
                    "required": ["emotion"],
                },
            },
            {
                "name": "open_application",
                "description": (
                    "Abre una aplicación del PC por nombre. "
                    "Nombres válidos: chrome, firefox, edge, notepad, calculator, paint, explorer, cmd, powershell, spotify, discord, vscode, word, excel, teams, brave. "
                    "NOTA: Usa SOLO nombres de esta lista, no rutas de archivo."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING", "description": "Nombre de la aplicación (ej: chrome, notepad, calculator)"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "browser_navigate",
                "description": (
                    "Navega a una URL en el browser. "
                    "Intenta primero con la extensión de Chrome. Si falla, usa browser-use automáticamente. "
                    "Para buscar en Google: url='https://www.google.com/search?q=tu+busqueda'. "
                    "SIEMPRE usa screenshot después para verificar que la página cargó. "
                    "Si ambos fallan, usa computer use: click barra dirección Chrome (y≈55) + hotkey ctrl+a + type URL + enter."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {"type": "STRING", "description": "URL completa incluyendo https://"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "terminal_run",
                "description": (
                    "Ejecuta un comando en Windows PowerShell y devuelve su salida. "
                    "El PC usa Windows 10/11. Usa sintaxis de Windows: "
                    "'Get-ComputerInfo', 'ping -n 3', 'dir', 'ipconfig', 'systeminfo', etc. "
                    "MUY ÚTIL para obtener info del sistema (modelo de PC, hardware, drivers instalados). "
                    "Ejemplo para info del sistema: 'Get-CimInstance Win32_BaseBoard | Select-Object Product,Manufacturer'."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {"type": "STRING", "description": "Comando a ejecutar en PowerShell de Windows"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "screen_read_text",
                "description": (
                    "Lee todo el texto visible en la pantalla usando OCR (EasyOCR + detección UI). "
                    "Útil para leer resultados de búsqueda, contenido de páginas web, mensajes de error, etc. "
                    "Toma ~30 segundos. Devuelve el texto detectado con posiciones."
                ),
            },
            # ── Browser DOM tools ──────────────────────────────────────
            {
                "name": "browser_click",
                "description": (
                    "Hace click en un elemento del browser por selector CSS. "
                    "Usar cuando la página ya está cargada y conoces el selector. "
                    "Distinto de 'click' (coordenadas de pantalla): este usa selectores del DOM."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del elemento (ej: #btn-submit, .nav-link, a[href='/login'])"},
                        "force": {"type": "BOOLEAN", "description": "Si true, ignora overlays y fuerza el click. Default: false"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_type",
                "description": (
                    "Escribe texto en un input/textarea del browser por selector CSS. "
                    "Por defecto limpia el campo antes de escribir."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del input"},
                        "text": {"type": "STRING", "description": "Texto a escribir"},
                        "clear": {"type": "BOOLEAN", "description": "Limpiar campo antes de escribir. Default: true"},
                    },
                    "required": ["selector", "text"],
                },
            },
            {
                "name": "browser_fill",
                "description": "Rellena un campo de formulario del browser por selector CSS. Similar a browser_type pero usa fill() nativo.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del campo"},
                        "text": {"type": "STRING", "description": "Valor a establecer"},
                    },
                    "required": ["selector", "text"],
                },
            },
            {
                "name": "browser_select",
                "description": "Selecciona una opción en un <select> del browser por selector CSS y valor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del <select>"},
                        "value": {"type": "STRING", "description": "Valor de la opción a seleccionar"},
                    },
                    "required": ["selector", "value"],
                },
            },
            {
                "name": "browser_press",
                "description": "Presiona una tecla en el browser (ej: Enter, Tab, Escape, ArrowDown).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "key": {"type": "STRING", "description": "Tecla a presionar (ej: Enter, Tab, Escape)"},
                    },
                    "required": ["key"],
                },
            },
            {
                "name": "browser_hover",
                "description": "Pasa el mouse por encima de un elemento del browser por selector CSS.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del elemento"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_extract",
                "description": (
                    "Extrae el contenido de texto de un elemento del browser por selector CSS. "
                    "Útil para leer textos, párrafos, listas, tablas de una página web."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS (default: body)"},
                    },
                },
            },
            {
                "name": "browser_scroll",
                "description": "Hace scroll dentro del browser. Dirección: up, down, left, right.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "direction": {"type": "STRING", "description": "Dirección: up, down, left, right"},
                        "amount": {"type": "INTEGER", "description": "Cantidad de unidades de scroll (default: 3)"},
                    },
                    "required": ["direction"],
                },
            },
            {
                "name": "browser_snapshot",
                "description": "Obtiene el estado actual del DOM del browser: URL, título y accessibility tree. Más rápido que screenshot.",
            },
            {
                "name": "browser_screenshot",
                "description": "Toma una captura de pantalla del browser (solo la ventana del browser, no toda la pantalla).",
            },
            {
                "name": "browser_page_info",
                "description": "Obtiene información de la página actual del browser: URL, título, estado de carga.",
            },
            {
                "name": "browser_state",
                "description": "Consulta el estado general del browser: perfil activo, URL, readiness, backend conectado.",
            },
            {
                "name": "browser_go_back",
                "description": "Navega atrás en el historial del browser (equivalente al botón atrás).",
            },
            {
                "name": "browser_go_forward",
                "description": "Navega adelante en el historial del browser (equivalente al botón adelante).",
            },
            {
                "name": "browser_remove_overlays",
                "description": "Elimina overlays, modales, banners de cookies y popups de la página actual del browser.",
            },
            {
                "name": "browser_eval",
                "description": "Ejecuta JavaScript en la página actual del browser y devuelve el resultado.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "script": {"type": "STRING", "description": "Código JavaScript a ejecutar"},
                    },
                    "required": ["script"],
                },
            },
            {
                "name": "browser_wait_for",
                "description": "Espera a que un elemento aparezca en la página del browser.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "selector": {"type": "STRING", "description": "Selector CSS del elemento a esperar"},
                        "timeout_ms": {"type": "INTEGER", "description": "Timeout en ms (default: 15000)"},
                        "state": {"type": "STRING", "description": "Estado esperado: visible, hidden, attached, detached (default: visible)"},
                    },
                    "required": ["selector"],
                },
            },
            # ── Tab management ─────────────────────────────────────────
            {
                "name": "browser_tabs",
                "description": "Lista todas las pestañas abiertas en el browser con su URL y título.",
            },
            {
                "name": "browser_new_tab",
                "description": "Abre una nueva pestaña en el browser, opcionalmente con una URL.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {"type": "STRING", "description": "URL a abrir en la nueva pestaña (opcional)"},
                    },
                },
            },
            {
                "name": "browser_switch_tab",
                "description": "Cambia a otra pestaña del browser por índice (0-based).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "index": {"type": "INTEGER", "description": "Índice de la pestaña (0 = primera)"},
                    },
                    "required": ["index"],
                },
            },
            {
                "name": "browser_close_tab",
                "description": "Cierra una pestaña del browser por índice. Si no se especifica, cierra la activa.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "index": {"type": "INTEGER", "description": "Índice de la pestaña a cerrar (opcional)"},
                    },
                },
            },
            # ── Desktop extras ─────────────────────────────────────────
            {
                "name": "wait",
                "description": "Espera una cantidad de segundos antes de continuar.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "seconds": {"type": "NUMBER", "description": "Segundos a esperar (ej: 1, 2.5, 5)"},
                    },
                    "required": ["seconds"],
                },
            },
            # ── Generación Multimedia ──────────────────────────────────
            {
                "name": "generate_image",
                "description": (
                    "Genera una imagen con IA usando Google Imagen o Gemini. "
                    "El agente tiene modelos de generación de imágenes configurados. "
                    "Usa esta herramienta cuando el usuario pida crear, generar, dibujar, diseñar una imagen. "
                    "El prompt debe ser descriptivo y en inglés para mejores resultados. "
                    "La imagen se guarda automáticamente en disco y se muestra al usuario."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {"type": "STRING", "description": "Descripción detallada de la imagen a generar (preferiblemente en inglés)"},
                        "aspect_ratio": {"type": "STRING", "description": "Relación de aspecto: 1:1, 16:9, 9:16, 4:3, 3:4 (default: 1:1)"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "generate_video",
                "description": (
                    "Genera un video con IA usando Google Veo. "
                    "Usa esta herramienta cuando el usuario pida crear, generar, hacer un video o clip. "
                    "El prompt debe ser descriptivo y en inglés para mejores resultados. "
                    "NOTA: La generación de video toma varios minutos. El video se guarda en disco."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {"type": "STRING", "description": "Descripción detallada del video a generar (preferiblemente en inglés)"},
                        "aspect_ratio": {"type": "STRING", "description": "Relación de aspecto: 16:9, 9:16, 1:1 (default: 16:9)"},
                        "duration_seconds": {"type": "INTEGER", "description": "Duración en segundos: 5 o 8 (default: 5)"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "generate_music",
                "description": (
                    "Genera música o audio con IA usando Google Lyria. "
                    "Usa esta herramienta cuando el usuario pida crear, generar, componer música, una canción, audio musical, o melodía. "
                    "El prompt debe describir el estilo, mood, instrumentos, género, tempo deseado. "
                    "La música se guarda como archivo de audio en disco."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {"type": "STRING", "description": "Descripción del estilo de música (género, mood, instrumentos, tempo, etc.)"},
                    },
                    "required": ["prompt"],
                },
            },
            # ── MCP (Model Context Protocol) ─────────────────────────
            {
                "name": "mcp_call_tool",
                "description": (
                    "Llama a una herramienta de un servidor MCP configurado y activo. "
                    "Usa esta herramienta cuando el usuario pida interactuar con un servicio MCP "
                    "(ej: Chrome DevTools, browser-tools-mcp, MCPControl, etc.). "
                    "Los servidores disponibles y sus tools se detallan en tu contexto de sistema. "
                    "Pasa el server_id, el nombre exacto de la tool, y los argumentos requeridos."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "server_id": {"type": "STRING", "description": "ID del servidor MCP (ej: 'chrome-devtools', 'browser-tools-mcp')"},
                        "tool": {"type": "STRING", "description": "Nombre exacto de la herramienta MCP a ejecutar"},
                        "arguments": {"type": "OBJECT", "description": "Argumentos de la herramienta MCP en formato JSON"},
                    },
                    "required": ["server_id", "tool"],
                },
            },
        ],
    }]

    def __init__(self):
        self._ws: Any = None
        self._active = False
        self._provider: str = "none"
        self._on_audio_callback: Callable | None = None
        self._on_text_callback: Callable | None = None
        self._on_user_text_callback: Callable | None = None
        self._on_tool_call_callback: Callable | None = None
        self._on_turn_complete_callback: Callable | None = None
        self._task: asyncio.Task | None = None
        self._speaking = False  # True while model is outputting audio
        self._voice: str = "Aoede"  # Voz por defecto
        self._input_transcription_buffer: str = ""  # Acumula fragmentos inputTranscription
        self._model_turn_had_text: bool = False  # Track si modelTurn tuvo text parts
        self._executing_tool: bool = False  # True durante ejecución de tool (silencia mic)
        # Anti-duplicado de turnos (bug conocido de Live API native-audio + function call:
        # el servidor re-genera la MISMA respuesta como 2º turno sin nuevo input del usuario.
        # Refs: livekit/agents#2884, #3870; discuss.ai.google.dev #135700). Detectamos el
        # turno fantasma porque arranca SIN input de usuario desde el último turnComplete
        # (los docs dicen que enviar audio durante un turno interrumpe, no crea un turno nuevo)
        # y suprimimos su audio/texto/done para que el usuario no lo oiga ni vea dos veces.
        self._user_spoke_since_turn: bool = True  # ¿hubo input de usuario desde el último turno?
        self._turn_in_progress: bool = False      # ¿estamos dentro de un turno del modelo?
        self._phantom_turn: bool = False           # el turno actual es un duplicado a suprimir
        self._session_resumption_handle: str | None = None  # Handle para session resumption de Google RT
        self._conversation_history: list[dict] | None = None  # Historial para inyectar en Google RT
        self._last_bargein_log: float = 0.0  # Timestamp del último log de barge-in (debounce)
        self._last_flushed_input: str = ""  # Último texto de usuario emitido (anti-duplicación)
        self._history_pending: bool = False  # True si hay historial pendiente de inyectar tras setupComplete
        self._mcp_context: str = ""  # Contexto MCP inyectado externamente para el system prompt RT
        self._google_ready: bool = False  # True tras setupComplete + historial inyectado (gate para audio)
        self._screen_streaming: bool = False  # True while screen streaming is active
        self._screen_stream_task: asyncio.Task | None = None  # Task for screen capture loop
        self._reconnecting: bool = False  # True durante auto-reconexión
        self._goaway_received: bool = False  # True cuando se recibe GoAway del servidor
        self._on_error_callback: Callable | None = None  # Errores fatales (quota, auth, modelo inválido)
        self._on_interrupt_callback: Callable | None = None  # Barge-in: avisa al frontend que vacíe el buffer de audio
        self._on_ready_callback: Callable | None = None  # Sesión lista para escuchar (setupComplete): el front da el cue audible

    async def start_session(
        self,
        provider: str = "openai",
        on_audio: Callable | None = None,
        on_text: Callable | None = None,
        on_user_text: Callable | None = None,
        on_tool_call: Callable | None = None,
        voice: str = "",
        on_turn_complete: Callable | None = None,
        conversation_history: list[dict] | None = None,
        on_error: Callable | None = None,
        on_interrupt: Callable | None = None,
        on_ready: Callable | None = None,
    ) -> bool:
        """
        Inicia una sesión de voz en tiempo real.
        provider: "openai", "google", "xai"
        conversation_history: lista de {role, content} para inyectar contexto previo (Google RT).
        """
        if not HAS_WEBSOCKETS:
            logger.error("websockets no instalado")
            return False

        self._on_audio_callback = on_audio
        self._on_text_callback = on_text
        self._on_user_text_callback = on_user_text
        self._on_tool_call_callback = on_tool_call
        self._on_turn_complete_callback = on_turn_complete
        self._on_error_callback = on_error
        self._on_interrupt_callback = on_interrupt
        self._on_ready_callback = on_ready
        self._provider = provider
        self._conversation_history = conversation_history
        if voice:
            self._voice = voice

        try:
            if provider == "openai":
                return await self._connect_openai()
            elif provider == "google":
                return await self._connect_google()
            elif provider == "xai":
                return await self._connect_xai()
            else:
                logger.error(f"Provider RT no soportado: {provider}")
                return False
        except Exception as e:
            logger.error(f"Error iniciando RT voice: {e}")
            return False

    async def _connect_openai(self) -> bool:
        """Conecta a OpenAI Realtime API."""
        api_key = config.get_api_key("openai_api")
        if not api_key:
            logger.error("API key de OpenAI no disponible para RT")
            return False

        model = self.get_realtime_providers()["openai"]["default"]
        url = f"wss://api.openai.com/v1/realtime?model={model}"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        self._ws = await websockets.connect(url, extra_headers=headers)
        self._active = True

        # Configurar sesión
        await self._ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": "Eres G-Mini Agent. Responde en español.",
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                },
            },
        }))

        # Iniciar listener
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("OpenAI Realtime conectado")
        return True

    def _get_google_system_prompt(self) -> str:
        """System prompt para Google Live API (rol coordinador).
        
        Si hay contexto MCP disponible (_mcp_context), se inyecta al final
        para que el modelo sepa qué servidores MCP están activos y sus tools.
        """
        base = (
            "Eres G-Mini Agent, un asistente de IA que COORDINA acciones sobre el PC del usuario. "
            "Responde SIEMPRE en español de forma concisa y natural.\n\n"
            "INICIO DE SESIÓN:\n"
            "Al iniciar la sesión NO saludas ni dices nada proactivamente. "
            "Esperas en silencio a que el usuario hable o escriba primero.\n\n"
            "CAPACIDADES DE VIDEO:\n"
            "Cuando el streaming de pantalla está activo (~1fps) ves el escritorio en tiempo real. "
            "Puedes describir lo que ves y reaccionar.\n\n"
            "ROL Y LÍMITES — MUY IMPORTANTE:\n"
            "Eres el COORDINADOR. Tú solo puedes OBSERVAR (screenshot, screen_read_text) y ANALIZAR. "
            "NO tienes clicks, escritura, teclas ni scroll directos. "
            "Para CUALQUIER interacción con la interfaz del escritorio (clicks, escribir, teclas, scroll, arrastrar, "
            "abrir/operar ventanas o apps) DEBES delegar con la herramienta 'delegate_computer_use', describiendo la "
            "tarea completa en lenguaje natural. El sub-agente de computer use la ejecuta de forma autónoma.\n\n"
            "FLUJO PARA TAREAS DE ESCRITORIO:\n"
            "  1. screenshot → analiza qué hay y en qué monitor está el objetivo.\n"
            "  2. delegate_computer_use(task=\"descripción completa\", monitor=N) — el sub-agente hace todos los clicks/typing.\n"
            "  3. Recibirás una captura de verificación. Comprueba el resultado; si no se logró, vuelve a delegar con más detalle.\n"
            "Ejemplo: usuario dice 'abre el bloc de notas y escribe Hola mundo' → "
            "delegate_computer_use(task=\"Abrir el Bloc de notas desde el menú inicio y escribir literalmente 'Hola mundo' en el área de edición\").\n\n"
            "MONITORES:\n"
            "- Si el objetivo no se ve en la captura actual (ej: 'abre WhatsApp' y el icono no está), usa screenshot(monitor=1), "
            "screenshot(monitor=2)… para localizarlo, y luego delega con ese 'monitor'.\n\n"
            "NAVEGACIÓN WEB:\n"
            "- Para URLs usa 'browser_navigate' (intenta extensión + browser-use). Para interactuar con el DOM usa las "
            "herramientas browser_* (browser_click, browser_type, browser_fill, browser_press…).\n"
            "- Si el control web estructurado falla, delega la interacción al sub-agente con delegate_computer_use.\n\n"
            "OTRAS CAPACIDADES DIRECTAS PERMITIDAS:\n"
            "- open_application para abrir apps de Windows; terminal_run para comandos PowerShell; generate_image/video/music; wait.\n"
            "- mcp_call_tool para ejecutar herramientas de servidores MCP configurados (ver sección MCP abajo).\n\n"
            "REGLAS GENERALES:\n"
            "1. NO uses herramientas cuando el usuario solo conversa, saluda, pregunta o quiere que le "
            "expliques QUÉ PUEDES HACER. En esos casos responde SOLO con palabras, sin tomar screenshot "
            "ni ejecutar ninguna herramienta. Ejemplo: 'dime qué puedes hacer' → respondes hablando, NO "
            "tomas captura de pantalla. El screenshot es exclusivo como paso previo a una interacción real "
            "con el escritorio que el usuario te pidió.\n"
            "2. No anuncies cada paso; reporta el resultado al terminar.\n"
            "3. Nunca afirmes haber hecho clicks tú mismo: la interacción la realiza el sub-agente vía delegate_computer_use."
        )
        # Autonomía + permisos + disciplina de acción (mismos selectores que la ruta de texto).
        try:
            base += "\n\n" + build_autonomy_context()
        except Exception:
            pass
        # Contexto del avatar (3D/2D/desactivado) segun config del usuario.
        # En audio nativo NO se usan tags de texto [happy] (el modelo los leeria en
        # voz alta); en su lugar la expresion facial se controla con la herramienta
        # set_emotion (ver bloque siguiente).
        try:
            base += "\n\n" + build_avatar_context()
        except Exception:
            pass
        # Instruccion de control de expresion (solo si las emociones estan activas;
        # _get_rt_tools expone set_emotion bajo la misma condicion).
        if config.get("character", "emotions_enabled", default=False):
            base += (
                "\n\nEXPRESIÓN FACIAL DE TU AVATAR:\n"
                "Tu avatar tiene cara y cuerpo que pueden mostrar emociones. Para sonreír "
                "o reflejar un estado de ánimo, llama a la herramienta set_emotion(emotion=...). "
                "Tu cara es INTERNA: NUNCA uses delegate_computer_use, screenshot ni clicks para "
                "cambiar tu expresión (no hay ningún botón de sonrisa en pantalla). "
                "Si el usuario te pide sonreír o mostrar una emoción, usa set_emotion. "
                "set_emotion es INSTANTÁNEA y NO requiere verificación: después de llamarla NO tomes "
                "screenshot ni ninguna otra acción para 'comprobar' la expresión. Pedir 'sonríe' = "
                "SOLO set_emotion(emotion=\"happy\"), nada más (sin captura de pantalla). "
                "El screenshot es EXCLUSIVO para tareas de interacción con el escritorio, jamás para "
                "conversar ni para expresar emociones. "
                "Emociones válidas: happy, sad, angry, surprised, relaxed, neutral. Úsalas con moderación."
            )
        # Inyectar contexto MCP si está disponible (modo pre-cargadas)
        if self._mcp_context:
            base += "\n\n" + self._mcp_context
        return base

    def _get_rt_tools(self) -> list[dict]:
        """Devuelve la lista de tools para la sesión RT.
        
        Si hay contexto MCP (_mcp_context no vacío), incluye mcp_call_tool.
        Si no hay MCP, excluye mcp_call_tool para no confundir al modelo.
        Siempre incluye google_search al final.
        """
        base_tools = self.GOOGLE_RT_TOOLS
        # Tools a excluir segun contexto/config.
        excluded: set[str] = set()
        if not self._mcp_context:
            excluded.add("mcp_call_tool")  # sin contexto MCP no tiene sentido
        if not config.get("character", "emotions_enabled", default=False):
            excluded.add("set_emotion")  # avatar sin emociones configuradas
        if excluded:
            filtered_decls = [
                d for d in base_tools[0]["function_declarations"]
                if d.get("name") not in excluded
            ]
            base_tools = [{"function_declarations": filtered_decls}]
        return base_tools + [{"google_search": {}}]

    async def _connect_google(self) -> bool:
        """Conecta a Google Gemini Live API (WebSocket directo).

        Soporta dos backends:
        - AI Studio: usa API key en la URL (generativelanguage.googleapis.com)
        - Vertex AI: usa Bearer token OAuth (aiplatform.googleapis.com)
        """
        backend = config.get("providers", "google", "backend", default="ai_studio")

        if backend == "vertex_ai":
            return await self._connect_google_vertex()

        # ── AI Studio (API key) ──
        api_key = config.get_api_key("google_api")
        if not api_key:
            logger.error("API key de Google no disponible para RT")
            return False

        url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"

        self._ws = await websockets.connect(url)
        self._active = True

        model = self.get_realtime_providers()["google"]["default"]
        setup_msg: dict[str, Any] = {
            "setup": {
                "model": f"models/{model}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}},
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": self._get_google_system_prompt()}],
                },
                "outputAudioTranscription": {},
                "inputAudioTranscription": {},
                "tools": self._get_rt_tools(),
            },
        }

        # ── Context Window Compression: extiende sesiones audio+video de 2min a ilimitado ──
        # Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#context-window-compression
        setup_msg["setup"]["contextWindowCompression"] = {
            "slidingWindow": {},
        }

        # ── Session Resumption: SIEMPRE habilitado para recibir handles y poder reconectar ──
        # Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#session-resumption
        # Los tokens son válidos 2 horas tras la última sesión.
        if self._session_resumption_handle:
            setup_msg["setup"]["sessionResumption"] = {
                "handle": self._session_resumption_handle,
            }
            logger.info(f"Google RT: reanudando sesión con handle {self._session_resumption_handle[:20]}...")
        else:
            # Habilitar sessionResumption sin handle para sesiones nuevas (así recibimos handles)
            setup_msg["setup"]["sessionResumption"] = {}
            # historyConfig: SOLO para sesiones nuevas (sin handle de reanudación).
            # Al reanudar, Google restaura el contexto automáticamente — NO inyectar historial de nuevo
            # o Google devuelve 1007 "invalid argument" al recibir clientContent duplicado.
            # Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#session-resumption
            if self._conversation_history:
                setup_msg["setup"]["historyConfig"] = {
                    "initialHistoryInClientContent": True,
                }

        await self._ws.send(json.dumps(setup_msg))

        # Historial pendiente SOLO si es sesión nueva (al reanudar, el contexto ya está en el servidor)
        self._history_pending = bool(self._conversation_history and not self._session_resumption_handle)
        # _google_ready siempre empieza en False — se activa ÚNICAMENTE al recibir setupComplete
        # (no setear True aquí aunque no haya historial, para evitar que screen stream envíe antes de setupComplete)
        self._google_ready = False

        self._task = asyncio.create_task(self._listen_loop())
        logger.info(f"Google Gemini Live conectado (modelo: {model})")
        return True

    # Vertex AI Live API uses different model IDs than AI Studio.
    # Only gemini-live-2.5-flash-native-audio is available on Vertex AI as of June 2026.
    VERTEX_LIVE_MODEL = "gemini-live-2.5-flash-native-audio"

    async def _connect_google_vertex(self) -> bool:
        """Conecta a Google Gemini Live API via Vertex AI (google-genai SDK).

        Usa el SDK que maneja autenticación automáticamente (ADC o credentials_file).
        El modelo en Vertex AI Live API es gemini-live-2.5-flash-native-audio (no los preview de AI Studio).
        """
        project_id = config.get("providers", "google", "project_id", default="")
        location = config.get("providers", "google", "location", default="global")
        credentials_file = config.get("providers", "google", "credentials_file", default="")

        if not project_id:
            logger.error("Vertex AI Live API requiere project_id configurado")
            return False

        # Live API NO funciona en "global" — necesita región específica
        live_location = location if location != "global" else "us-central1"

        # Obtener access token via ADC
        try:
            import google.auth
            import google.auth.transport.requests
            import os

            if credentials_file:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_req = google.auth.transport.requests.Request()
            credentials.refresh(auth_req)
            access_token = credentials.token
        except Exception as exc:
            logger.error(f"No se pudo obtener token OAuth para Vertex AI Live API: {exc}")
            return False

        model = self.VERTEX_LIVE_MODEL
        model_path = f"projects/{project_id}/locations/{live_location}/publishers/google/models/{model}"

        url = (
            f"wss://{live_location}-aiplatform.googleapis.com/ws/"
            f"google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
        )

        headers = {"Authorization": f"Bearer {access_token}"}
        self._ws = await websockets.connect(url, additional_headers=headers)
        self._active = True

        setup_msg: dict[str, Any] = {
            "setup": {
                "model": model_path,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}},
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": self._get_google_system_prompt()}],
                },
                "outputAudioTranscription": {},
                "inputAudioTranscription": {},
                "tools": self._get_rt_tools(),
            },
        }

        setup_msg["setup"]["contextWindowCompression"] = {"slidingWindow": {}}
        if self._session_resumption_handle:
            setup_msg["setup"]["sessionResumption"] = {"handle": self._session_resumption_handle}
        else:
            setup_msg["setup"]["sessionResumption"] = {}
            if self._conversation_history:
                setup_msg["setup"]["historyConfig"] = {"initialHistoryInClientContent": True}

        await self._ws.send(json.dumps(setup_msg))

        self._history_pending = bool(self._conversation_history and not self._session_resumption_handle)
        self._google_ready = False

        self._task = asyncio.create_task(self._listen_loop())
        logger.info(f"Google Gemini Live (Vertex AI) conectado (modelo: {model}, location: {live_location}, model_path: {model_path})")
        return True

    async def _inject_conversation_history(self) -> None:
        """Envía historial previo a Google RT via clientContent para que tenga contexto."""
        if not self._conversation_history or not self._ws:
            return

        # Tomar los últimos N mensajes (máx 20) para no saturar la ventana
        history = self._conversation_history[-20:]

        # Mapear roles: user→user, assistant→model
        turns = []
        for msg in history:
            role = "model" if msg.get("role") == "assistant" else "user"
            content = msg.get("content", "")
            if content.strip():
                turns.append({
                    "role": role,
                    "parts": [{"text": content}],
                })

        if not turns:
            return

        try:
            await self._ws.send(json.dumps({
                "clientContent": {
                    "turns": turns,
                    "turnComplete": True,
                },
            }))
            logger.info(f"Google RT: {len(turns)} turnos de historial inyectados como contexto")
        except Exception as e:
            logger.warning(f"Google RT: error inyectando historial: {e}")

    async def _connect_xai(self) -> bool:
        """Conecta a xAI Voice Agent API."""
        api_key = config.get_api_key("xai_api")
        if not api_key:
            logger.error("API key de xAI no disponible para RT")
            return False

        url = "wss://api.x.ai/v1/realtime"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        self._ws = await websockets.connect(url, extra_headers=headers)
        self._active = True

        # Configurar sesión (formato xAI Voice Agent API)
        await self._ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "voice": "Eve",
                "instructions": "Eres G-Mini Agent. Responde en español.",
                "turn_detection": {"type": "server_vad"},
                "audio": {
                    "input": {"format": {"type": "audio/pcm", "rate": 16000}},
                    "output": {"format": {"type": "audio/pcm", "rate": 16000}},
                },
            },
        }))

        self._task = asyncio.create_task(self._listen_loop())
        logger.info("xAI Voice Agent conectado")
        return True

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Envía un chunk de audio del micrófono. Interrumpe respuesta activa (barge-in)."""
        if not self._active or not self._ws:
            return
        # Google RT: no enviar audio hasta setupComplete + historial inyectado
        # Ref: https://ai.google.dev/api/live — clientes DEBEN esperar setupComplete
        if self._provider == "google" and not self._google_ready:
            return
        # No enviar audio durante ejecución de tools para evitar VAD interruption
        if self._executing_tool:
            return

        # Barge-in: if model is speaking and user sends audio, interrupt
        if self._speaking:
            await self.interrupt_response()

        try:
            b64_audio = base64.b64encode(audio_chunk).decode("utf-8")

            if self._provider == "openai":
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64_audio,
                }))
            elif self._provider == "google":
                # Gemini Live API: realtimeInput.audio con mimeType y data
                # https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket
                await self._ws.send(json.dumps({
                    "realtimeInput": {
                        "audio": {
                            "data": b64_audio,
                            "mimeType": "audio/pcm;rate=16000",
                        },
                    },
                }))
            elif self._provider == "xai":
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64_audio,
                }))
        except Exception as e:
            logger.error(f"Error enviando audio RT: {e}")

    async def send_text(self, text: str) -> None:
        """
        Envía texto como entrada al modelo vía Live API (realtimeInput.text).
        Documentación: https://ai.google.dev/gemini-api/docs/live-guide#sending-text-message
        Especificaciones: entrada texto admitida, salida audio + transcripción.
        """
        if not self._active or not self._ws:
            return
        if self._provider != "google":
            return  # Solo Google Live API soporta realtimeInput.text
        # Esperar a que la conexión esté lista (setupComplete recibido)
        if not self._google_ready:
            import asyncio as _asyncio
            for _ in range(50):  # hasta 5s de espera
                await _asyncio.sleep(0.1)
                if self._google_ready:
                    break
            if not self._google_ready:
                logger.warning("Google RT send_text: timeout esperando setupComplete, descartando texto")
                return
        try:
            self._user_spoke_since_turn = True  # input de texto: próximo turno es legítimo
            await self._ws.send(json.dumps({"realtimeInput": {"text": text}}))
            logger.info(f"Google RT: texto enviado ({len(text)} chars): {text[:80]!r}")
        except Exception as e:
            logger.error(f"Error enviando texto RT: {e}")

    async def interrupt_response(self) -> None:
        """Interrumpe la respuesta del modelo durante barge-in."""
        if not self._active or not self._ws:
            return
        self._speaking = False
        try:
            if self._provider == "openai":
                await self._ws.send(json.dumps({
                    "type": "response.cancel",
                }))
                # Clear any pending audio in input buffer
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.clear",
                }))
                logger.info("OpenAI RT: respuesta interrumpida (barge-in)")
            elif self._provider == "google":
                # Gemini Live: sending new input implicitly interrupts
                # Debounce: solo loguear si han pasado >2s desde el último log
                import time as _time
                now = _time.monotonic()
                if now - self._last_bargein_log > 2.0:
                    logger.debug("Google RT: barge-in (input implícito interrumpe)")
                    self._last_bargein_log = now
            elif self._provider == "xai":
                await self._ws.send(json.dumps({
                    "type": "response.cancel",
                }))
                logger.info("xAI RT: respuesta interrumpida (barge-in)")
        except Exception as e:
            logger.error(f"Error interrumpiendo respuesta RT: {e}")

    async def _listen_loop(self) -> None:
        """Loop de recepción de mensajes del WebSocket."""
        try:
            async for message in self._ws:
                if not self._active:
                    break

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.warning("Mensaje WS no-JSON recibido")
        except Exception as e:
            if self._active:
                error_str = str(e).lower()
                # Detectar errores fatales: quota, auth, modelo inválido — no tiene sentido reconectar
                is_fatal = any(kw in error_str for kw in (
                    "quota", "billing", "exceeded", "permission", "unauthorized",
                    "api key", "invalid_argument", "not found", "does not exist",
                ))
                if self._goaway_received or self._reconnecting:
                    logger.info(f"RT listen loop: conexión cerrada tras GoAway/reconexión: {e}")
                else:
                    logger.error(f"RT listen loop error: {e}")
                # Notificar al frontend si hay error fatal (quota, auth, modelo inválido)
                if is_fatal and self._on_error_callback:
                    try:
                        await self._on_error_callback(str(e))
                    except Exception:
                        pass
                # ── Auto-reconexión para Google RT ──
                # Solo si NO es error fatal y tenemos handle de reanudación.
                # Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#session-resumption
                if (self._provider == "google" and self._session_resumption_handle
                        and not self._reconnecting and not is_fatal):
                    await self._auto_reconnect_google()
                    return  # _auto_reconnect_google maneja el nuevo listen loop
        finally:
            # Solo limpiar el estado si esta tarea sigue siendo el loop principal.
            # Si _auto_reconnect_google creó uno nuevo, self._task apuntará al nuevo,
            # así que la tarea vieja sale silenciosamente sin corromper el estado.
            is_main_task = (self._task is None or self._task == asyncio.current_task())
            if is_main_task:
                was_active = self._active
                self._active = False
                if was_active and self._on_turn_complete_callback:
                    try:
                        await self._on_turn_complete_callback()
                    except Exception:
                        pass

    async def _auto_reconnect_google(self) -> None:
        """Auto-reconecta a Google RT tras desconexión, preservando callbacks y screen stream.
        Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#session-resumption
        Los tokens de reanudación son válidos durante 2 horas.
        """
        max_retries = 3
        was_screen_streaming = self._screen_streaming
        was_goaway = self._goaway_received
        was_speaking = self._speaking

        for attempt in range(1, max_retries + 1):
            try:
                self._reconnecting = True
                self._google_ready = False
                self._goaway_received = False

                # Cerrar WS anterior sin resetear callbacks ni handle
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

                # Detener screen stream task (se reiniciará tras reconexión)
                if self._screen_streaming:
                    self._screen_streaming = False
                    if self._screen_stream_task and not self._screen_stream_task.done():
                        self._screen_stream_task.cancel()
                        try:
                            await self._screen_stream_task
                        except asyncio.CancelledError:
                            pass
                    self._screen_stream_task = None

                # Backoff: si GoAway recibido, delay mínimo (desconexión esperada).
                # Sin GoAway: exponencial 0.5s, 1s, 2s.
                if was_goaway and attempt == 1:
                    wait = 0.1  # GoAway → desconexión esperada, reconectar rápido
                else:
                    wait = 0.5 * (2 ** (attempt - 1))
                logger.info(f"Google RT: auto-reconectando (intento {attempt}/{max_retries}, esperando {wait}s)...")
                await asyncio.sleep(wait)

                api_key = config.get_api_key("google_api")
                if not api_key:
                    logger.error("Google RT: API key no disponible para reconexión")
                    break

                url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
                self._ws = await websockets.connect(url)
                self._active = True

                model = self.get_realtime_providers()["google"]["default"]
                setup_msg: dict[str, Any] = {
                    "setup": {
                        "model": f"models/{model}",
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}},
                            },
                        },
                        "systemInstruction": {
                            "parts": [{"text": self._get_google_system_prompt()}],
                        },
                        "outputAudioTranscription": {},
                        "inputAudioTranscription": {},
                        "tools": self.GOOGLE_RT_TOOLS + [{"google_search": {}}],
                        "contextWindowCompression": {
                            "slidingWindow": {},
                        },
                    },
                }

                # sessionResumption: con handle si está disponible, vacío si no (sesión fresca)
                if self._session_resumption_handle:
                    setup_msg["setup"]["sessionResumption"] = {
                        "handle": self._session_resumption_handle,
                    }
                    self._history_pending = False  # Al reanudar, el servidor tiene el contexto
                    logger.info(f"Google RT: reconectando con handle {self._session_resumption_handle[:20]}...")
                else:
                    setup_msg["setup"]["sessionResumption"] = {}
                    # Sesión fresca: inyectar historial del chat para que el modelo
                    # sepa qué estábamos hablando (el servidor ya no tiene contexto)
                    if self._conversation_history:
                        setup_msg["setup"]["historyConfig"] = {
                            "initialHistoryInClientContent": True,
                        }
                        self._history_pending = True
                        logger.info("Google RT: reconectando como sesión nueva — se inyectará historial del chat")
                    else:
                        self._history_pending = False
                        logger.info("Google RT: reconectando como sesión nueva (sin historial)")

                await self._ws.send(json.dumps(setup_msg))
                self._google_ready = False
                self._reconnecting = False

                # Lanzar nuevo listen loop
                self._task = asyncio.create_task(self._listen_loop())
                logger.info(f"Google RT: reconexión exitosa (intento {attempt})")

                # Esperar a que setupComplete active _google_ready antes de reanudar streams/texto
                for _ in range(100):
                    await asyncio.sleep(0.1)
                    if self._google_ready:
                        break

                if self._google_ready:
                    # Reiniciar screen streaming si estaba activo
                    if was_screen_streaming:
                        await self.start_screen_stream()
                        logger.info("Google RT: screen streaming reiniciado tras reconexión")
                    
                    # Si el modelo estaba hablando cuando se cortó, pedir que continúe
                    if was_speaking:
                        logger.info("Google RT: solicitando continuar respuesta cortada tras reconexión")
                        await self.send_text("(La conexión se reinició. Por favor, continúa exactamente donde te quedaste en tu última respuesta)")
                return

            except Exception as e:
                logger.error(f"Google RT: error en auto-reconexión intento {attempt}: {e}")
                # Si falló con handle, limpiar para intentar sesión fresca en el siguiente intento
                if self._session_resumption_handle:
                    logger.warning("Google RT: limpiando handle de reanudación para reintentar como sesión nueva")
                    self._session_resumption_handle = None
                if attempt == max_retries:
                    logger.error("Google RT: auto-reconexión agotada, sesión terminada")
                    self._reconnecting = False
                    self._active = False
                    if self._on_turn_complete_callback:
                        try:
                            await self._on_turn_complete_callback()
                        except Exception:
                            pass

    def _mark_model_turn_start(self) -> None:
        """Marca el inicio de un turno del modelo y decide si es un turno fantasma.

        Un turno que arranca SIN input de usuario desde el último turnComplete es un
        duplicado generado por el servidor (bug conocido de native-audio + function call).
        Consume el flag de input para que cuente solo para este turno.
        """
        if self._turn_in_progress:
            return
        self._turn_in_progress = True
        self._phantom_turn = not self._user_spoke_since_turn
        self._user_spoke_since_turn = False  # consumido por este turno
        if self._phantom_turn:
            logger.warning(
                "Google RT: turno fantasma detectado (sin input de usuario desde el último "
                "turnComplete) — suprimiendo audio/texto duplicado"
            )

    async def _handle_message(self, data: dict) -> None:
        """Procesa un mensaje del WebSocket de voz."""
        msg_type = data.get("type", "")

        # OpenAI events (response.audio.delta, response.audio_transcript.delta)
        # xAI events (response.output_audio.delta, response.output_audio_transcript.delta)
        if msg_type in ("response.audio.delta", "response.output_audio.delta"):
            self._speaking = True
            audio_b64 = data.get("delta", "")
            if audio_b64 and self._on_audio_callback:
                audio_bytes = base64.b64decode(audio_b64)
                await self._on_audio_callback(audio_bytes)

        elif msg_type in ("response.audio.done", "response.output_audio.done", "response.done"):
            self._speaking = False
            if self._on_turn_complete_callback:
                await self._on_turn_complete_callback()

        elif msg_type in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            text = data.get("delta", "")
            if text and self._on_text_callback:
                await self._on_text_callback(text)

        # Google Gemini Live: setupComplete — servidor listo para recibir datos
        elif "setupComplete" in data:
            logger.info("Google RT: setupComplete recibido")
            # Ahora es seguro inyectar historial de conversación previo
            if self._history_pending:
                self._history_pending = False
                await self._inject_conversation_history()
            # Marcar como listo para recibir audio
            self._google_ready = True
            logger.debug("Google RT: listo para recibir audio")
            # Avisar al frontend para que dé un cue audible (pitido "ya te escucho").
            if self._on_ready_callback:
                try:
                    await self._on_ready_callback()
                except Exception as _ready_err:
                    logger.debug(f"on_ready callback falló: {_ready_err}")
            return

        # Google Gemini Live events (serverContent)
        elif "serverContent" in data:
            content = data["serverContent"]
            logger.debug(f"Google RT serverContent keys: {list(content.keys())}")

            # Interrupción por VAD (barge-in del usuario)
            if content.get("interrupted") is True:
                self._speaking = False
                # El barge-in es input del usuario: cuenta como "habló" para el próximo turno.
                self._user_spoke_since_turn = True
                self._turn_in_progress = False
                self._phantom_turn = False
                # Vaciar el buffer de audio del frontend: el servidor canceló la generación,
                # pero el cliente tiene chunks encolados y seguiría "hablando" si no se limpia.
                # Ref Live API: "stop playing audio and clear queued playback" al interrumpir.
                if self._on_interrupt_callback:
                    try:
                        await self._on_interrupt_callback()
                    except Exception as exc:
                        logger.debug(f"on_interrupt callback falló: {exc}")
                # No cerrar la burbuja de streaming aquí — _flush_input_transcription
                # la cerrará cuando llegue el próximo modelTurn, preservando el texto parcial
                logger.debug("Google RT: generación interrumpida por VAD")
                return

            # Audio y texto del modelo
            if "modelTurn" in content:
                self._mark_model_turn_start()
                # Emitir transcripción acumulada del usuario antes de mostrar respuesta del modelo
                await self._flush_input_transcription()
                self._speaking = True
                had_text = False
                for part in content["modelTurn"].get("parts", []):
                    if "inlineData" in part:
                        audio_b64 = part["inlineData"].get("data", "")
                        # Turno fantasma: NO reproducir su audio (sería la respuesta repetida).
                        if audio_b64 and self._on_audio_callback and not self._phantom_turn:
                            audio_bytes = base64.b64decode(audio_b64)
                            await self._on_audio_callback(audio_bytes)
                    elif "text" in part:
                        had_text = True
                        if self._on_text_callback and not self._phantom_turn:
                            await self._on_text_callback(part["text"])
                if had_text:
                    self._model_turn_had_text = True

            # Transcripción de audio de salida del modelo
            # Usar outputTranscription como fuente de texto para el chat cuando
            # modelTurn solo tiene audio (inlineData) sin text parts.
            if "outputTranscription" in content:
                self._mark_model_turn_start()
                text = content["outputTranscription"].get("text", "")
                if text:
                    if self._model_turn_had_text:
                        logger.debug(f"Google RT outputTranscription (ignorado, modelTurn ya tenía texto): {text!r}")
                    elif self._phantom_turn:
                        logger.debug(f"Google RT outputTranscription (ignorado, turno fantasma): {text!r}")
                    else:
                        logger.debug(f"Google RT outputTranscription (usando como texto del agente): {text!r}")
                        if self._on_text_callback:
                            await self._on_text_callback(text)

            # Transcripción de audio de entrada del usuario (llega en fragmentos)
            if "inputTranscription" in content:
                text = content["inputTranscription"].get("text", "")
                if text:
                    self._user_spoke_since_turn = True  # el usuario habló: próximo turno es legítimo
                    self._input_transcription_buffer += text
                    logger.debug(f"Google RT inputTranscription chunk: {text!r} (buffer={self._input_transcription_buffer!r})")

            # Turno completo
            if content.get("turnComplete") is True:
                self._speaking = False
                self._model_turn_had_text = False
                await self._flush_input_transcription()
                self._last_flushed_input = ""  # Reset anti-duplicación para el próximo turno
                was_phantom = self._phantom_turn
                # Cerrar el turno; el flag de input NO se resetea aquí (lo consume el inicio
                # del próximo turno) para no marcar como fantasma una respuesta a un barge-in.
                self._turn_in_progress = False
                self._phantom_turn = False
                # Turno fantasma: NO cerrar burbuja ni persistir (sería el mensaje duplicado).
                if not was_phantom and self._on_turn_complete_callback:
                    await self._on_turn_complete_callback()

        # Google Gemini Live: tool call (function calling)
        # https://ai.google.dev/gemini-api/docs/live-api/tools
        elif "toolCall" in data:
            await self._flush_input_transcription()
            tool_call = data["toolCall"]
            self._executing_tool = True
            # audioStreamEnd solo es correcto cuando el audio se pausa >1s (tools
            # lentos: screenshot, delegate_computer_use, browser_*...). Para tools
            # INSTANTÁNEOS y locales (set_emotion, ~10ms) NO se envía: hacerlo hace
            # flush + resume casi inmediato del stream continuo del mic, lo que bajo
            # VAD automática crea un TURNO FANTASMA y el modelo genera una respuesta
            # DUPLICADA (p.ej. saluda dos veces). Ref VAD automática:
            # https://ai.google.dev/gemini-api/docs/live-guide
            _INSTANT_RT_TOOLS = {"set_emotion"}
            _fc_names = {fc.get("name", "") for fc in tool_call.get("functionCalls", [])}
            _all_instant = bool(_fc_names) and _fc_names.issubset(_INSTANT_RT_TOOLS)
            if not _all_instant:
                try:
                    await self._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                    logger.info("RT: audioStreamEnd enviado antes de tool execution")
                except Exception:
                    pass
            if self._on_tool_call_callback:
                await self._on_tool_call_callback(tool_call)

        # Google Gemini Live: session resumption handle update
        elif "sessionResumptionUpdate" in data:
            update = data["sessionResumptionUpdate"]
            new_handle = update.get("newHandle")
            if new_handle:
                self._session_resumption_handle = new_handle
                logger.debug(f"Google RT: session resumption handle actualizado ({new_handle[:20]}...)")

        # Google Gemini Live: GoAway — servidor avisa que va a cerrar la conexión pronto
        # Ref: https://ai.google.dev/gemini-api/docs/live-api/session-management#goaway-message
        # La conexión morirá con 1007 en segundos. No reconectamos aquí porque el
        # servidor mata AMBAS conexiones (vieja y nueva) si aún está en periodo GoAway.
        # En su lugar, marcamos la flag para que _listen_loop maneje 1007 como esperado
        # y auto-reconecte reactivamente con mínimo delay.
        elif "goAway" in data:
            time_left = data["goAway"].get("timeLeft", "desconocido")
            logger.info(f"Google RT: GoAway recibido — conexión cerrará pronto (timeLeft={time_left}), auto-reconexión preparada")
            self._goaway_received = True

    async def _flush_input_transcription(self) -> None:
        """Emite la transcripción acumulada del usuario y limpia el buffer."""
        text = self._input_transcription_buffer.strip()
        if text and self._on_user_text_callback:
            # Anti-duplicación: no re-emitir si es el mismo texto que ya se emitió
            if text == self._last_flushed_input:
                logger.debug(f"Google RT: transcripción duplicada ignorada: {text!r}")
                self._input_transcription_buffer = ""
                return
            self._last_flushed_input = text
            logger.info(f"Google RT: emitiendo transcripción del usuario: {text!r}")
            await self._on_user_text_callback(text)
        self._input_transcription_buffer = ""

    async def send_tool_response(self, function_responses: list[dict]) -> None:
        """Envía respuestas de herramientas al WebSocket (Google Live API)."""
        if not self._active or not self._ws:
            self._executing_tool = False
            return
        try:
            # https://ai.google.dev/gemini-api/docs/live-api/tools
            payload = json.dumps({
                "toolResponse": {
                    "functionResponses": function_responses,
                },
            })
            logger.info(f"RT send_tool_response: {len(function_responses)} funcs, payload_size={len(payload)} bytes")
            if len(payload) > 50000:
                logger.warning(f"RT tool response payload muy grande ({len(payload)} bytes), puede causar error 1007")
            await self._ws.send(payload)
        except Exception as e:
            logger.error(f"Error enviando tool response RT: {e}")
        finally:
            self._executing_tool = False
            logger.info("RT: _executing_tool=False, mic reanudado")

    async def send_image(self, image_base64: str) -> None:
        """Envía una imagen como frame de video al modelo (Google Live API realtimeInput.video)."""
        if not self._active or not self._ws or self._provider != "google":
            return
        try:
            b64_data = image_base64
            if b64_data.startswith("data:"):
                b64_data = b64_data.split(",", 1)[-1]
            await self._ws.send(json.dumps({
                "realtimeInput": {
                    "video": {
                        "data": b64_data,
                        "mimeType": "image/jpeg",
                    },
                },
            }))
            logger.info(f"RT send_image: imagen enviada ({len(b64_data)} chars base64)")
        except Exception as e:
            logger.error(f"Error enviando imagen RT: {e}")

    # ── Screen streaming ──────────────────────────────────────────

    async def start_screen_stream(self) -> bool:
        """Inicia captura de pantalla continua y envío al modelo vía realtimeInput.video (~1fps)."""
        if not HAS_SCREEN_CAPTURE:
            logger.error("Screen streaming no disponible: mss/Pillow no instalados")
            return False
        if self._provider != "google":
            logger.warning("Screen streaming solo soportado con Google Live API")
            return False
        if not self._active or not self._ws:
            logger.warning("Screen streaming: sesión RT no activa")
            return False
        if self._screen_streaming:
            logger.debug("Screen streaming ya activo")
            return True

        self._screen_streaming = True
        self._screen_stream_task = asyncio.create_task(self._screen_stream_loop())
        logger.info("Screen streaming iniciado (~1fps)")
        # Esperar a que la sesión esté lista (setupComplete) antes de enviar la notificación
        if not self._google_ready:
            for _ in range(100):  # hasta 10s de espera
                await asyncio.sleep(0.1)
                if self._google_ready:
                    break
        # Notificar al modelo que ahora recibirá fotogramas de la pantalla
        if self._google_ready and self._active and self._ws:
            try:
                await self._ws.send(json.dumps({
                    "realtimeInput": {
                        "text": (
                            "[SISTEMA] El usuario ha activado el streaming de pantalla. "
                            "A partir de ahora recibirás fotogramas de su escritorio a ~1fps "
                            "como entrada de video (realtimeInput.video). "
                            "Puedes ver y describir lo que el usuario está haciendo en su PC. "
                            "Avisa brevemente al usuario que ahora puedes ver su pantalla."
                        )
                    }
                }))
            except Exception as _e:
                logger.debug(f"Screen stream start notification error: {_e}")
        return True

    async def stop_screen_stream(self) -> None:
        """Detiene la captura de pantalla continua."""
        self._screen_streaming = False
        if self._screen_stream_task and not self._screen_stream_task.done():
            self._screen_stream_task.cancel()
            try:
                await self._screen_stream_task
            except asyncio.CancelledError:
                pass
        self._screen_stream_task = None
        logger.info("Screen streaming detenido")
        # Notificar al modelo que el streaming de pantalla se detuvo
        if self._active and self._ws:
            try:
                await self._ws.send(json.dumps({
                    "realtimeInput": {
                        "text": "[SISTEMA] El usuario ha desactivado el streaming de pantalla. Ya no recibirás fotogramas de video."
                    }
                }))
            except Exception:
                pass

    async def _screen_stream_loop(self) -> None:
        """Loop que captura la pantalla y envía frames JPEG al modelo a ~1fps.
        Ref: https://ai.google.dev/gemini-api/docs/live-guide#sending-video
        Formato: realtimeInput.video = Blob { data: base64, mimeType: 'image/jpeg' }
        """
        # Esperar a que la conexión esté lista (setupComplete recibido)
        if not self._google_ready:
            for _ in range(100):  # hasta 10s de espera
                await asyncio.sleep(0.1)
                if self._google_ready:
                    break
            if not self._google_ready:
                logger.warning("Screen stream: timeout esperando _google_ready, abortando")
                self._screen_streaming = False
                return
        sct = None
        try:
            sct = mss.mss()
            while self._screen_streaming and self._active and self._ws:
                t0 = time.monotonic()
                try:
                    # Capturar pantalla completa (monitor 0 = all monitors, 1 = primary)
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    screenshot = sct.grab(monitor)

                    # Convertir a JPEG comprimido
                    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                    # Redimensionar para reducir tamaño (max 1280px de ancho)
                    max_w = 1280
                    if img.width > max_w:
                        ratio = max_w / img.width
                        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=60)
                    frame_bytes = buf.getvalue()
                    b64_data = base64.b64encode(frame_bytes).decode("utf-8")

                    # Enviar como video frame
                    await self._ws.send(json.dumps({
                        "realtimeInput": {
                            "video": {
                                "data": b64_data,
                                "mimeType": "image/jpeg",
                            },
                        },
                    }))
                    logger.debug(f"Screen stream: frame enviado ({len(frame_bytes)} bytes JPEG)")

                except Exception as e:
                    logger.warning(f"Screen stream frame error: {e}")

                # Esperar hasta completar ~1 segundo por frame
                elapsed = time.monotonic() - t0
                sleep_time = max(0.0, 1.0 - elapsed)
                await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Screen stream loop error: {e}")
        finally:
            if sct:
                try:
                    sct.close()
                except Exception:
                    pass
            self._screen_streaming = False

    @property
    def is_screen_streaming(self) -> bool:
        return self._screen_streaming

    async def stop_session(self) -> None:
        """Detiene la sesión de voz en tiempo real."""
        self._active = False

        # Detener screen streaming si está activo
        if self._screen_streaming:
            await self.stop_screen_stream()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        # Reset state para evitar contaminación en la próxima sesión
        self._speaking = False
        self._input_transcription_buffer = ""
        self._model_turn_had_text = False
        self._executing_tool = False
        self._last_bargein_log = 0.0
        self._last_flushed_input = ""
        self._history_pending = False
        self._google_ready = False
        self._reconnecting = False
        self._goaway_received = False
        self._conversation_history = None
        self._user_spoke_since_turn = True
        self._turn_in_progress = False
        self._phantom_turn = False
        self._on_audio_callback = None
        self._on_text_callback = None
        self._on_user_text_callback = None
        self._on_tool_call_callback = None
        self._on_turn_complete_callback = None
        self._on_ready_callback = None
        # Limpiar handle: stop explícito = sesión nueva la próxima vez.
        # _auto_reconnect_google() NO pasa por stop_session(), así que no afecta reconexión automática.
        self._session_resumption_handle = None

        logger.info("RT Voice sesión terminada")

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def provider(self) -> str:
        return self._provider
