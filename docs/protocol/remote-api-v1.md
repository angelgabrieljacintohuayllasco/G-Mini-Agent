# G-Mini Remote API v1

Contrato entre el núcleo de G-Mini Agent (escritorio o servidor) y sus clientes:
CLI, extensión del navegador, dispositivos de domótica (ESP32, Arduino por
USB, Raspberry Pi) y otras instancias de G-Mini.

- Versión del protocolo: `1`. Los cambios dentro de la v1 son solo aditivos.
- Codificación: JSON UTF-8. Fechas en ISO 8601 con zona (`2026-10-07T00:00:00Z`).
- Puerto por defecto: `8765`. En escritorio el núcleo escucha en `127.0.0.1`;
  en modo servidor puede escuchar en `0.0.0.0` (usar Tailscale/VPN o un proxy
  con TLS para exponerlo fuera de la red local).
- Modo servidor: `python -m backend.main --headless --host 0.0.0.0 --port 8765`
  (sin visión ni control de escritorio; terminal, archivos, skills, conectores,
  MCP, memoria, voz por la API y tareas siguen disponibles).

## 1. Autenticación

Todas las rutas, salvo `GET /api/v1/health` y `POST /api/v1/pairing/claim`,
exigen:

```
Authorization: Bearer <token>
```

| Tipo de token | Quién lo obtiene | Cómo |
|---|---|---|
| `session` | La app de escritorio y la CLI en la misma máquina | Se genera al arrancar y se guarda en `data/runtime/session_token` (solo lectura para el usuario) |
| `device` | Dispositivos compañeros y otras PCs | Emparejamiento con código de 6 dígitos |
| `api` | Scripts e integraciones | Se crea en Ajustes > Dispositivos > Tokens de API |

### 1.1 Emparejamiento

1. Un cliente ya autenticado pide un código:

   `POST /api/v1/pairing` (scope `admin`) → `201`

   ```json
   {"label": "Companion del escritorio", "device_type": "esp32", "scopes": ["chat", "voice", "node"]}
   ```

   Respuesta:

   ```json
   {"code": "482913", "expires_at": "2026-10-07T00:05:00Z",
    "qr_payload": "gmini://pair?host=100.71.131.70&port=8765&code=482913"}
   ```

2. El dispositivo nuevo canjea el código (sin token):

   `POST /api/v1/pairing/claim`

   ```json
   {"code": "482913", "device_name": "Companion sala", "device_type": "esp32", "platform": "esp32-s3"}
   ```

   Respuesta `200`:

   ```json
   {"token": "gm_dev_...", "device_id": "dev_7f3a...", "server_name": "tv-server",
    "agent_name": "G-Mini", "scopes": ["chat", "voice", "node"]}
   ```

   - El código es de un solo uso y vence a los 5 minutos.
   - Límite: 5 intentos por minuto por IP; al quinto fallo el código se invalida.

3. Revocar: `DELETE /api/v1/devices/{device_id}`. Listar: `GET /api/v1/devices`.

4. Tokens de API para scripts: `POST /api/v1/tokens` (scope `admin`) `{"label": "respaldo nocturno", "scopes": ["chat", "tasks"]}` → `201 {"token": "gm_api_...", "id": "api_...", "name": "...", "scopes": [...]}`. El token se muestra una sola vez; se lista y se revoca como un dispositivo.

### 1.2 Alcances (scopes)

| Scope | Permite |
|---|---|
| `chat` | Conversar y leer sus propias sesiones |
| `voice` | TTS, STT y turnos de voz |
| `tasks` | Encolar tareas en segundo plano y consultarlas |
| `node` | Registrarse como nodo y exponer superficies (cara, LEDs, relés, sensores) |
| `admin` | Gestionar dispositivos, tokens y configuración |

Los tokens `session` tienen todos los scopes.

## 2. REST

### `GET /api/v1/health` (sin auth)

```json
{"ok": true, "protocol": 1, "version": "0.2.0", "mode": "desktop", "name": "G-Mini", "requires_auth": true}
```

`mode` es `desktop` o `server`. `name` es el nombre que el usuario le dio al agente.

### `GET /api/v1/me`

```json
{"kind": "device", "device_id": "dev_7f3a", "device_name": "Companion sala", "scopes": ["chat", "voice"],
 "agent": {"name": "G-Mini", "language": "es", "voice": "es-PE-CamilaNeural"}}
```

### `POST /api/v1/chat`

```json
{"message": "Recuérdame comprar pan a las 6", "session_id": null, "stream": false,
 "attachments": [{"name": "foto.jpg", "mime_type": "image/jpeg", "data_base64": "..."}]}
```

Respuesta sin streaming:

```json
{"session_id": "ses_20261007_000501_ab12", "reply": "Listo, te aviso a las 18:00.",
 "actions": [{"action": "schedule_create_job", "params": {}, "success": true, "message": "Tarea creada"}],
 "notices": ["Acciones ejecutadas: ..."], "approval_pending": false}
```

`session_id`: vacío o `null` sigue la conversación actual, `"new"` abre una
nueva y el id de una conversación existente la retoma (`404 not_found` si no
existe; `409 busy` si el agente está en medio de otro turno).

`reply` es el último mensaje del agente en el turno (su conclusión), sin el
marcado interno `[ACTION:...]`. `notices` trae avisos del sistema y
`approval_pending: true` indica que hay acciones esperando aprobación.

Con `"stream": true` (o `Accept: text/event-stream`) la respuesta es SSE:

```
event: start          data: {"session_id": "..."}
event: chunk          data: {"text": "Listo, "}
event: action         data: {"action": "schedule_create_job", "params": {...}, "id": "a1b2"}
event: action_result  data: {"action": "schedule_create_job", "success": true, "message": "...", "id": "a1b2"}
event: state          data: {"status": "thinking", "emotion": "neutral"}
event: notice         data: {"kind": "system", "text": "..."}
event: approval       data: {"pending": true, "summary": "...", "kind": "approval"}
event: done           data: {"session_id": "...", "reply": "Listo, te aviso a las 18:00.", "actions": [...]}
event: error          data: {"code": "provider_unavailable", "message": "..."}
```

Si el agente está ocupado con otra conversación la respuesta es `409 busy`
(el cliente puede reintentar o encolar una tarea).

### Aprobaciones

`POST /api/v1/approvals` `{"approve": true}` aprueba (o con `false` cancela)
las acciones sensibles que el agente dejó pendientes. `404` si no hay ninguna.

### Sesiones

- `GET /api/v1/sessions?limit=20` → `{"items": [{"id", "title", "updated_at", "message_count"}]}`
- `GET /api/v1/sessions/{id}/messages?limit=100` → `{"items": [{"role", "content", "created_at"}]}`

### Tareas en segundo plano (trabajador 24/7)

- `POST /api/v1/tasks`

  ```json
  {"prompt": "Revisa mi correo y resume lo urgente", "schedule": {"cron": "0 8 * * *", "timezone": "America/Lima"},
   "notify": ["telegram"], "title": "Resumen de correo"}
  ```

  `schedule` es opcional (sin él la tarea corre una vez, ya). Formas válidas:
  `{"cron", "timezone"}` y `{"interval_seconds"}` (mínimo 60). `{"at"}` todavía
  no está disponible (`422`). `notify` acepta destinos del gateway como
  `"telegram:<chat_id>"`. Si el agente está ocupado, la tarea espera hasta 10 min.
  Respuesta `202`: `{"task_id": "tsk_...", "status": "queued"}` (o `scheduled`).

- `GET /api/v1/tasks/{id}` →
  `{"task_id", "title", "prompt", "status": "queued|scheduled|running|done|failed|cancelled", "result", "error", "runs", "created_at", "started_at", "finished_at", "next_run_at"}`
  (las programadas vuelven a `scheduled` tras cada corrida; `result` es el de la última).
- `GET /api/v1/tasks?status=running`
- `DELETE /api/v1/tasks/{id}` cancela (y desactiva su programación).

### Voz (para dispositivos con micrófono y parlante)

- `POST /api/v1/voice/tts` `{"text": "Hola", "voice": null, "format": "wav"}` → audio (`audio/wav`, PCM16 mono).
- `POST /api/v1/voice/stt` con cuerpo `audio/wav` (recomendado 16 kHz mono PCM16) → `{"text": "..."}`.
- `POST /api/v1/voice/wake` con un `audio/wav` corto (menos de 4 s) → `{"wake": true, "phrase": "oye g-mini", "command": "qué hora es", "transcript": "..."}`. Sirve para dispositivos con su propio detector de voz: si `wake` es true, `command` trae lo pedido después de la palabra de activación (puede venir vacío).
- `POST /api/v1/voice/turn?session_id=&reply_format=wav` con cuerpo `audio/wav` →

  ```json
  {"transcript": "qué clima hace", "reply": "Soleado, 19 grados.", "session_id": "...",
   "emotion": "happy", "audio_mime": "audio/wav", "audio_base64": "UklGR..."}
  ```

  Es el ciclo completo (STT → agente → TTS) en una sola llamada, pensado para
  microcontroladores. El audio de respuesta respeta `reply_format`
  (`wav` 16 kHz por defecto, o `pcm16` crudo).

### Estado del agente

`GET /api/v1/agent/state` → `{"status": "idle|listening|thinking|acting|speaking", "emotion": "neutral", "busy": false}`

## 3. WebSocket `/api/v1/ws`

Autenticación: `Authorization: Bearer` en el handshake, o `?token=` en la URL
para clientes que no pueden enviar cabeceras. Sin token válido (o desde un
Origin web ajeno) el servidor cierra con código `4401`. Cada frame es un objeto
JSON con `type`; los frames de una respuesta llevan el `id` del `chat` que la pidió.

### Cliente → servidor

| type | Campos | Uso |
|---|---|---|
| `hello` | `client`, `version`, `device_name` | Primer mensaje |
| `chat` | `id`, `text`, `session_id?` | Enviar mensaje |
| `cancel` | — | Detener la respuesta en curso |
| `ping` | — | Mantener viva la conexión (cada 25 s) |
| `node.register` | `surfaces[]`, `platform`, `meta{}` | Exponer capacidades del dispositivo |
| `node.result` | `request_id`, `ok`, `data` o `error` | Respuesta a una invocación |
| `node.event` | `event`, `data` | Evento espontáneo (botón pulsado, lectura de sensor) |

### Servidor → cliente

| type | Campos | Uso |
|---|---|---|
| `ready` | `agent_name`, `session_id`, `protocol` | Respuesta a `hello` |
| `chunk` | `id`, `text` | Fragmento de respuesta |
| `action` / `action_result` | `action`, `params` / `action`, `success`, `message` | Progreso |
| `node.registered` | `surfaces` | Confirmación de `node.register` |
| `state` | `status`, `emotion` | Para animar caras y LEDs |
| `done` | `id`, `reply`, `session_id` | Fin de la respuesta |
| `error` | `code`, `message` | Error |
| `notify` | `title`, `body`, `priority` | Aviso para mostrar en el dispositivo |
| `node.invoke` | `request_id`, `surface`, `params` | El agente usa una capacidad del dispositivo |
| `pong` | — | |

`status` recorre `idle → listening → thinking → acting → speaking → idle`.
`emotion` ∈ `neutral, happy, sad, surprised, angry, thinking, sleepy, love, error`.

## 4. Superficies de nodo (capacidades)

Nombres estándar para dispositivos compañeros (pantallas, parlantes, placas de
domótica). Un dispositivo solo declara las que implementa; el usuario puede
desactivar cada una desde Ajustes > Dispositivos.

| Superficie | Parámetros | Devuelve |
|---|---|---|
| `display.face` | `expression`, `text?` | `{ok}` |
| `display.text` | `text`, `seconds?` | `{ok}` |
| `tts.speak` | `text` | `{ok}` |
| `led.set` | `color` (`#rrggbb`), `effect?` | `{ok}` |
| `relay.set` | `channel`, `on` | `{ok}` |
| `sensor.read` | `name` | `{value, unit}` |
| `system.info` | — | `{firmware, uptime_s, rssi, free_heap}` |

`relay.set` controla cargas físicas y pasa por la política de aprobaciones del
agente igual que cualquier acción local.

## 5. Errores

```json
{"error": {"code": "invalid_token", "message": "Token inválido o revocado"}}
```

| HTTP | `code` |
|---|---|
| 400 | `bad_request` |
| 401 | `invalid_token` |
| 403 | `missing_scope` |
| 404 | `not_found` |
| 409 | `busy` |
| 422 | `validation_error` |
| 429 | `rate_limited` |
| 503 | `provider_unavailable` |

## 6. Seguridad

- El núcleo de escritorio rechaza peticiones cuyo `Host` no sea
  `127.0.0.1:<puerto>` o `localhost:<puerto>` (protección contra DNS rebinding)
  y exige token también a clientes locales.
- Los tokens se guardan hasheados (SHA-256) en el servidor; el token en claro
  solo se muestra una vez.
- Los dispositivos deben guardar el token en almacenamiento seguro
  (NVS cifrado en ESP32, keyring del sistema en PCs).
- Todo contenido que llega desde un nodo se trata como dato no confiable,
  nunca como instrucciones.
