# G-Mini Agent

> **Super-agente de IA autónomo para Windows** que ve tu pantalla, hace clic, escribe, habla y ejecuta tareas completas de principio a fin — como lo haría un humano experto.

No es un chatbot. Es un agente operativo: ve la pantalla (OCR + OmniParser), controla mouse/teclado/navegador/Android, razona con cualquier LLM (cloud o local), habla y escucha, opera 24/7 en background, delega en sub-agentes y se recupera de errores.

**Filosofía:** *"Si un humano puede hacerlo en una computadora, G-Mini Agent debe poder hacerlo."*

- **Versión:** 0.1.0
- **Stack:** Electron (UI + overlay/avatar 3D) + Python FastAPI/Socket.IO (cerebro)
- **Plataforma:** Windows 10/11 (Linux parcial)

---

## ✨ Capacidades

| Capacidad | Cómo |
|-----------|------|
| **Ver** | Captura `mss`, OCR multi-motor (Tesseract / EasyOCR / PaddleOCR), detección de UI con **OmniParser** (YOLO + Florence-2) |
| **Pensar** | Router multi-proveedor con fallback, planificación multi-paso, critic gate, DAG executor, goal engine |
| **Actuar** | Control de escritorio (PyAutoGUI), navegador (extensión Chrome nativa + `browser-use`), Android (ADB) |
| **Hablar** | TTS (MeloTTS offline / ElevenLabs / Gemini TTS), STT (Faster-Whisper), voz en tiempo real + lipsync |
| **Delegar** | Sub-agentes especializados en paralelo, cada uno con su modo y modelo asignado |
| **Persistir** | Tareas 24/7, scheduler con cron, checkpoints, self-healing, rollback |
| **Comunicar** | Gateway multi-canal: WhatsApp, Telegram, Discord, Slack — control remoto desde el celular |
| **Adaptarse** | 11 modos (Programador, Marketero, Pentester, Gamer, Creador…) + modos custom |

---

## 🤖 Modelos soportados

Catálogo centralizado en [`data/models.yaml`](data/models.yaml) (fuente única de verdad para frontend + backend). Configura las API keys y elige modelo desde el selector de la UI.

### LLM / Chat

| Proveedor | Modelos actuales |
|-----------|------------------|
| **OpenAI** | `gpt-5.4`, `gpt-5.4-pro`, `gpt-5-mini`, `gpt-5-nano`, `gpt-4.1`, `gpt-5.3-codex`, `gpt-5.2-codex` |
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` |
| **Google** | `gemini-3.5-flash` (latest estable), `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite`, `gemini-2.5-pro/flash/flash-lite` |
| **xAI (Grok)** | `grok-4-1-fast-reasoning`, `grok-4-1-fast-non-reasoning`, `grok-4`, `grok-code-fast-1` |
| **DeepSeek** | `deepseek-chat` (V3.2), `deepseek-reasoner` |
| **Groq** | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b`, `gemma2-9b-it` |
| **Mistral** | `mistral-large/medium/small-latest`, `codestral-latest` |
| **Perplexity** | `sonar-pro`, `sonar` |
| **Cohere** | `command-r-plus`, `command-r`, `command-light` |
| **OpenRouter** | acceso federado (gpt-4o, claude-3.5-sonnet, llama-3.1-405b…) |
| **Local** | **Ollama** y **LM Studio** (modelos detectados automáticamente) |

### Multimedia (Google)

- **Imagen:** `gemini-3.1-flash-image-preview`, `gemini-3-pro-image-preview`, `imagen-4.0` / `imagen-3.0`
- **Video:** `veo-3.1`, `veo-3.0`, `veo-2.0`
- **Música:** `lyria-3-pro-preview`

### Visión en tiempo real / Computer Use

- **Live API (audio/video):** `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio`
- **Computer Use nativo:** `gemini-2.5-computer-use` (Gemini 3 ya lo incluye integrado)

> Por defecto el agente coordinador usa `gemini-3.1-pro-preview`. Los sub-agentes reciben modelo según el tipo de tarea (ver `model_assignments` en [`config.default.yaml`](config.default.yaml)).

---

## 🚀 Inicio rápido

### Requisitos
- **Python 3.11+**
- **Node.js 20+**
- (Opcional) Tesseract OCR para visión sin descargar pesos

### Opción 1 — Un solo click (Windows)
```bat
start.bat
```
Crea el venv, instala dependencias Python + Node, y arranca todo. Electron lanza el backend como proceso hijo automáticamente.

### Opción 2 — Manual
```bash
# Backend (Python)
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements.txt

# Frontend (Electron) — lanza el backend solo
cd electron
npm install
npm start
```

Backend en `http://127.0.0.1:8765` (FastAPI + Socket.IO).

---

## ⌨️ Atajos globales

| Atajo | Acción |
|-------|--------|
| `Alt+G` | Mostrar / ocultar ventana |
| `Alt+Shift+G` | Toggle overlay (avatar flotante) |
| `Ctrl+Shift+Q` | Salir |

El **kill switch** de automatización es configurable en `automation.kill_switch_hotkey` (`config.user.yaml`).

---

## 🏗️ Arquitectura

```
G-Mini-Agent/
├── backend/                    # Python — FastAPI + Socket.IO (ASGI)
│   ├── main.py                 # Entry point: AgentCore + Gateway + Scheduler
│   ├── config.py               # YAML + Windows Credential Manager (keyring)
│   ├── api/                    # REST + WebSocket (routes, schemas, ws handler)
│   ├── core/                   # ~60 módulos: agent, planner, modes, subagents,
│   │                           #   scheduler, gateways, memoria LTM + knowledge
│   │                           #   graph, cost tracker/optimizer, DAG, goals,
│   │                           #   analytics, self-healing, rollback, macros,
│   │                           #   ETL, RLHF, canvas, nodes, skills, MCP runtime
│   ├── providers/              # base, openai_compat, anthropic, google,
│   │                           #   cohere, google_media, router (con fallback)
│   ├── vision/                 # engine (captura+OCR), ui_detector (OmniParser), verifier
│   ├── automation/             # pc_controller, adb, browser (extensión + browser-use),
│   │                           #   chrome_profiles, editor_bridge, recovery
│   ├── voice/                  # engine (TTS/STT), realtime, simulated_realtime
│   ├── security/               # rbac, audit, sandbox, injection_detector,
│   │                           #   ethical, rate_limiter, virustotal
│   └── utils/
├── electron/                   # Frontend — Electron 30
│   └── src/
│       ├── index.html / overlay.html / skin.html
│       └── js/
│           ├── app.js, websocket.js, chat.js, settings.js, code.js, history.js
│           ├── voiceRealtime.js
│           └── skins/          # Avatar: VRM 3D, sprites 2D, energy ball, GLB
│               └── (three.js + @pixiv/three-vrm)
├── data/
│   ├── models.yaml             # Catálogo de modelos (fuente única)
│   ├── prompts/                # System prompts por rol/modo
│   ├── skills/                 # Skills bundled (web-search, ffmpeg, calendar…)
│   └── skins/                  # Avatares 2D / 3D
├── config.default.yaml         # Config base (se copia a config.user.yaml)
├── start.bat                   # Inicio Windows
└── README.md
```

### Sub-sistemas destacados

- **Sistema de Modos** — 11 personalidades predefinidas + modos custom (YAML) con system prompt, permisos y restricciones propias.
- **Sub-agentes** — orquestador multi-agente; cada sub-agente recibe el modelo óptimo por tarea (programación → Claude, razonamiento → DeepSeek, computer use → Gemini…).
- **Gateway multi-canal** — WhatsApp Web (bridge Node), Telegram, Discord, Slack; sesiones aisladas, activación por mención en grupos, aprobaciones tokenizadas.
- **Visión + Computer Use** — screenshot → OCR/OmniParser → razonamiento → acción, con verificación visual y reintentos.
- **Seguridad** — RBAC (5 roles), audit log, sandbox de ejecución, detección de inyección de prompts, filtro ético, rate limiting, escaneo VirusTotal.
- **Avatar flotante** — personaje 3D (VRM con animaciones Mixamo), 2D o "energy ball", con lipsync y emociones, en overlay transparente.

---

## 🔑 Seguridad — API Keys

- **NUNCA** se guardan en archivos.
- Almacenadas en **Windows Credential Manager** vía `keyring`.
- Se configuran desde **Settings (UI)** o el endpoint REST:

```bash
curl -X POST http://127.0.0.1:8765/api/api-keys \
  -H "Content-Type: application/json" \
  -d '{"vault_name": "anthropic_api", "api_key": "sk-ant-..."}'
```

- `config.user.yaml` contiene **solo preferencias** (está en `.gitignore`).

---

## 🛡️ Autonomía configurable

| Nivel de permisos | Comportamiento |
|-------------------|----------------|
| **Asistido** | Sugiere acciones, el usuario aprueba una por una |
| **Supervisado** | Pide confirmación para acciones críticas (pagos, publicaciones, borrados) |
| **Libre** | Ejecuta todo sin confirmar (máxima autonomía) |

Un *critic gate* puntúa cada acción y, bajo umbral, simula primero (dry-run) antes de ejecutar. Presupuestos por día/mes/tarea/sub-agente con auto-downgrade de modelos bajo presión de costo.

---

## 📦 Estado de desarrollo

Proyecto estructurado en **18 fases** (ver [`G-MINI-ROADMAP-FASES.md`](G-MINI-ROADMAP-FASES.md)). Resumen:

- **Fases 1–4** (chat multi-LLM, visión + control PC/Android, voz, modos + sub-agentes): operativas.
- **Fases 6, 10–18** (gateway, seguridad, memoria, RPA, analytics, resiliencia, ETL, agentes autónomos, productividad, offline/sync): completas.
- **Pendiente:** streaming realtime completo (OpenAI/Gemini/Grok Live), conectores CRM (HubSpot/Salesforce), packaging NSIS firmado.

Definición completa del producto: [`G-MINI-AGENT-DEFINICION.md`](G-MINI-AGENT-DEFINICION.md).

---

## 🤝 Contribuir

1. Fork → branch (`git checkout -b feature/mi-cambio`)
2. Commit → Push → Pull Request
3. **Estilo:** Python (black, isort, mypy) · JS (eslint, prettier)

---

## 📄 Licencia

MIT — ver [LICENSE](LICENSE).

---

## ⭐ Star History

<a href="https://star-history.com/#angelgabrieljacintohuayllasco/G-Mini-Agent&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=angelgabrieljacintohuayllasco/G-Mini-Agent&type=Date" />
 </picture>
</a>
