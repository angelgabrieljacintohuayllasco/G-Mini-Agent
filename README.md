# G-Mini Agent

[![tests](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/actions/workflows/tests.yml/badge.svg)](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/actions/workflows/tests.yml)

Agente de IA de escritorio para Windows que ve tu pantalla, usa el mouse, el
teclado y el navegador, recuerda lo que le cuentas, habla y sigue trabajando
cuando cierras la ventana. Funciona con el proveedor de IA que prefieras:
Google Cloud (Vertex AI), OpenAI, Anthropic, Gemini, tu suscripción de Claude o
ChatGPT, o modelos locales.

- Escritorio: Electron + núcleo en Python (FastAPI y Socket.IO).
- Servidor: el mismo núcleo sin pantalla para un VPS o una Raspberry Pi.
- Licencia MIT.

## Descargar

Instalador para Windows 10 y 11 y AppImage para Linux en
[Releases](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/releases/latest).
La primera vez que la abres, la app prepara su propio Python con los
componentes del núcleo (unos 500 MB) y no toca el que ya tengas.

## Qué hace

| Área | Qué incluye |
|---|---|
| Ver y actuar | Capturas y OCR (el que trae Windows, sin instalar nada), control de mouse y teclado con un sub-agente de computer use (Gemini, Claude, OpenAI o cualquier modelo con visión), navegador (extensión propia y browser-use), Android por ADB, terminal y archivos con permisos. |
| Pensar | 31 proveedores con un solo router y fallback real, sub-agentes con el modelo adecuado para cada tarea, planificación paso a paso, modo plan (propone sin ejecutar). |
| Recordar | Memoria de largo plazo con embeddings: perfil del usuario en cada conversación, recuerdos relacionados con cada mensaje y modo aprendiz que anota preferencias y lecciones al terminar cada turno. |
| Aprender | Skills en formato estándar SKILL.md (incluidas, importadas desde GitHub o escritas por el propio agente con tu aprobación) y skills con herramientas ejecutables. |
| Conectarse | Servidores MCP, conectores de datos (clima, dólar oficial del BCRP, feriados, Wikipedia, RSS, lector web, GitHub, PyPI/npm) y gateway con WhatsApp, Telegram, Discord y Slack. |
| Hablar | Voces neuronales de Edge (gratis), OpenAI, Gemini, ElevenLabs o MeloTTS local; reconocimiento con Whisper; conversación en tiempo real con Gemini Live. |
| Trabajar 24/7 | Tareas programadas, tareas en segundo plano por API, consolidación de memoria con el agente inactivo y avisos por el gateway. |
| Tener nombre | Le pones el nombre y la personalidad que quieras en el asistente inicial o en el chat (`/nombre`). |

## Proyectos relacionados

| Repositorio | Qué es |
|---|---|
| [G-Mini-Agent-Server](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server) | CLI `gmini` para hablar con un G-Mini desde la terminal (otra PC, un VPS, scripts) e imagen Docker del núcleo en modo servidor |
| [G-Mini-Agent-Extension](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Extension) | Extensión para Chrome, Edge, Brave, Opera, Vivaldi y Firefox: el agente usa tu navegador con tus sesiones, conectado solo a tu app local |

## Proveedores de IA

| Tipo | Proveedores |
|---|---|
| Con API key | OpenAI, Anthropic, Google AI Studio, xAI, DeepSeek, Groq, Mistral, Perplexity, OpenRouter, Cohere, Moonshot, Qwen, Z.ai, MiniMax, Cerebras, SambaNova, Venice, Together, Fireworks, DeepInfra, NVIDIA NIM, Hugging Face, GitHub Models, Azure OpenAI |
| Google Cloud | Vertex AI con tu sesión de `gcloud` (ADC) o una cuenta de servicio: sin API key |
| Tu suscripción | Claude Pro/Max vía Claude Code (`claude -p`), ChatGPT vía Codex CLI (`codex exec`) y tu cuenta de Google vía Gemini CLI (`gemini -p`), aislados de tu configuración personal del CLI |
| Local | Ollama, LM Studio y cualquier servidor compatible con OpenAI (vLLM, llama.cpp, LocalAI) |

El catálogo de modelos está en [`data/models.yaml`](data/models.yaml). Las keys se
guardan en el almacén de credenciales del sistema (en Windows, el Administrador
de credenciales), nunca en la config.

## Desde el código (Windows)

Requisitos: Python 3.11 o superior (probado con 3.13) y Node.js 20 o superior.

```bat
start.bat
```

Crea el entorno de Python, instala las dependencias y abre la app; Electron
arranca el núcleo por su cuenta. Manual:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements.txt
cd electron
npm install
npm start
```

La primera vez aparece un asistente que configura idioma, nombre del agente,
proveedor, modelo, autonomía, voz y si quieres que te conozca mejor.

## Modo servidor y API remota

En Linux, como servicio que sigue corriendo 24/7 (sin sudo; instala Python en
tu home y lo deja en `systemctl --user`):

```bash
curl -fsSL https://raw.githubusercontent.com/angelgabrieljacintohuayllasco/G-Mini-Agent/main/deploy/linux/install-server.sh | bash -s -- --tailscale
```

`--tailscale` escucha solo en la IP de Tailscale; sin opciones queda en
`127.0.0.1` y `--host 0.0.0.0` lo abre a la red. `--voz` agrega Whisper para
dispositivos con micrófono y la palabra de activación. Volver a correrlo
actualiza.
A mano, en cualquier sistema:

```bash
pip install -r backend/requirements-server.txt
python -m backend.main --headless --host 0.0.0.0 --port 8765
```

Sin pantalla no hay visión ni control de escritorio, pero sí terminal,
archivos, skills, conectores, MCP, memoria, voz y tareas. Fuera de `127.0.0.1`
todas las rutas exigen token: usa Tailscale, una VPN o un proxy con TLS para
exponerlo.

- `GMINI_HOME=/ruta` guarda config, bases y archivos generados fuera de la
  carpeta del programa (instalaciones de solo lectura y contenedores).
- Las API keys se leen de `GMINI_KEY_<NOMBRE>` (por ejemplo
  `GMINI_KEY_OPENAI_API`), del almacén de credenciales del sistema o, si el
  servidor no tiene uno, de `data/runtime/secrets.json` con permisos 600.
  También sirven `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` y
  las demás variables estándar de cada proveedor.
- La API `/api/v1` ([contrato](docs/protocol/remote-api-v1.md)) ofrece
  emparejamiento de dispositivos con código, chat en JSON o streaming SSE,
  sesiones, voz (TTS, STT y turno completo), tareas en segundo plano,
  aprobaciones remotas y un WebSocket para clientes y dispositivos (caras
  OLED, LEDs, relés y sensores).
- Tu G-Mini de escritorio puede emparejarse con el del servidor (código de 6
  dígitos, `POST /api/remote-servers/pair`) y delegarle tareas largas o que
  deben seguir cuando apagas la PC: se lo pides en el chat y usa
  `remote_delegate`.

## Comandos del chat

| Comando | Qué hace |
|---|---|
| `/ayuda` | Lista los comandos, incluidos los tuyos |
| `/nuevo` | Conversación nueva |
| `/modo [nombre]` | Lista o cambia el modo (programador, investigador, marketero...) |
| `/plan <tarea>` y `/ejecutar` | Plan sin ejecutar nada y luego ejecutarlo |
| `/skills`, `/skill <nombre> [tarea]` | Ver skills o usar una concreta |
| `/recuerdos [tema]`, `/olvidar <id>` | Ver o borrar lo que recuerda |
| `/nombre <nuevo>` | Cambiarle el nombre al agente |
| `/costos`, `/detener` | Gasto del día y del mes; detener lo que esté haciendo |

Cada archivo `data/commands/<nombre>.md` es un comando propio; `$ARGUMENTS` se
reemplaza por lo que escribas después. Vienen `/resumir`, `/traducir` y
`/revisar-codigo`.

## Seguridad

- Token de sesión por arranque y validación de `Host` en el núcleo local; los
  WebSockets rechazan orígenes web ajenos.
- Niveles de permisos (asistido, supervisado, libre) aplicados por igual al
  chat, la voz y los sub-agentes; las acciones sensibles piden aprobación.
- Ejecución de código en sandbox (falla cerrado sin Docker), lista de
  comandos permitidos, lectura bloqueada de almacenes de credenciales y
  escritura bloqueada sobre la propia config, el código y las skills del
  agente.
- Todo lo que llega de páginas, archivos y herramientas se trata como datos,
  no como instrucciones; la memoria solo aprende de lo que escribe el usuario.
- Hooks propios antes y después de cada acción (`hooks.pre_action`,
  `hooks.post_action`) para auditar o bloquear.

## Configuración

`config.default.yaml` trae los valores por defecto; lo que cambias en la app se
guarda en `config.user.yaml` (fuera de git). Atajos globales: `Alt+G` muestra u
oculta la ventana, `Alt+Shift+G` el avatar flotante y `Ctrl+Shift+Q` sale.

## Desarrollo

```bat
pip install -r backend\requirements-dev.txt
python -m pytest
```

Para correr los tests sin visión, automatización ni modelos locales basta con
`backend\requirements-ci.txt`; es lo que usa la integración continua en
Windows y Linux con Python 3.11 y 3.13.

El instalador se arma con `npm run dist:win` (o `dist:linux`) dentro de
`electron/`; al subir un tag `vX.Y.Z`, GitHub Actions publica ambos en
Releases.

| Carpeta | Contenido |
|---|---|
| `backend/core` | Agente, planner, policy, memoria, aprendizaje, skills, scheduler, gateway |
| `backend/providers` | Router, proveedores y catálogo (`registry.py`) |
| `backend/connectors` | Conectores de datos |
| `backend/voice` | TTS, STT y voz en tiempo real |
| `backend/api` | REST, Socket.IO y API remota v1 |
| `backend/security` | Autenticación local, sandbox, auditoría |
| `electron/` | Interfaz, overlay y avatar |
| `data/` | Prompts, catálogo de modelos, skills y comandos incluidos |

Los tests no llaman APIs reales: la memoria usa embeddings locales y una base
temporal.

## Licencia

MIT. Ver [LICENSE](LICENSE).

<a href="https://star-history.com/#angelgabrieljacintohuayllasco/G-Mini-Agent&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date" />
   <img alt="Historial de estrellas" src="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date" />
 </picture>
</a>
