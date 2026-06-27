# Diseño: Onboarding + Aprendizaje Continuo y Auto-mejora — G-Mini Agent

**Fecha:** 2026-06-27
**Autor del diseño:** Opus 4.8 (para ejecución por Opus 4.6)
**Estado:** Aprobado para plan de implementación
**Inspiración:** `hermes-agent` (NousResearch) + `openclaw`

---

## 1. Objetivo

Dar a G-Mini Agent dos capacidades que hoy NO tiene y que sí tienen hermes/openclaw:

1. **Onboarding de primera ejecución** — al arrancar por primera vez, guiar al usuario a configurar lo esencial (API keys, modelo, voz, autonomía/permisos, avatar) y, de forma opt-in, conocer al usuario para personalizarse.
2. **Aprendizaje continuo + auto-mejora** — el agente aprende del uso (hechos, preferencias, lecciones), recuerda eso en futuras conversaciones, y mantiene/mejora sus propias habilidades (skills) con el tiempo.

Decisiones de alcance (confirmadas con el usuario):
- Onboarding **híbrido**: wizard visual (config técnica) + flujo conversacional opt-in (conocer al usuario).
- Aprendizaje **híbrido configurable**: reflexión tras turnos sustanciales + barrido por inactividad, con modelo auxiliar barato + digest.
- Embeddings: **proveedor real + fallback hash** (semántica de verdad cuando hay API key).
- **Incluir ahora** auto-autoría + curación de skills propias (estilo curator de hermes).

---

## 2. Estado actual de G-Mini (lo que ya existe y los huecos)

Ya existe (reutilizable):
- `backend/core/memory_ltm.py` — `LongTermMemory` con categorías `FACT/PREFERENCE/TASK/LEARNING`, métodos `store()`, `search()`, `update_importance()`, singleton `get_ltm()`. SQLite `data/memory_ltm.db`.
- `backend/core/rlhf_lite.py` — `RLHFLite.record_signal()` (señales de preferencia).
- `backend/core/knowledge_graph.py`, `session_compressor.py` (comparten `memory_ltm.db`).
- `backend/core/skill_registry.py` — `SkillRegistry` con `list_catalog()`, `install_from_path()`, `install_from_git()`.
- `backend/core/skill_runtime.py` — ejecución aislada de skills (sandbox).
- `backend/config.py` — `get(*keys)`, `set(*keys, value)`, `reload()`, `get_api_key()/set_api_key()/delete_api_key()` (Windows Credential Manager).
- `backend/core/agent.py` — `process_message()` (turno texto, ~L2515), `process_message_live()` (voz, ~L3887), `_apply_system_prompt()` (~L2233, ensambla `build_mode_system_prompt + build_autonomy_context` y llama `self._memory.set_system_prompt()`), `initialize()` (~L2412).
- `backend/core/modes.py` — `build_mode_system_prompt()`, `build_autonomy_context()`.

Huecos (lo que falta):
- **No hay onboarding/first-run/wizard** en absoluto (grep vacío).
- `memory_ltm` solo se invoca desde `routes.py` (REST/UI manual). **El agente nunca aprende solo**: nada escribe `LEARNING/FACT/PREFERENCE` desde las conversaciones.
- **La memoria no se inyecta al prompt**: aunque se guarde, no se recupera al contexto en el siguiente turno.
- **Embeddings son un hash TF-IDF determinista** (`_get_embedding` con fallback hash; sin modelo). Recall semántico pobre.
- `skill_registry` puede instalar skills pero **el agente no crea ni cura sus propias skills** (no hay create/lifecycle/archive/pin/consolidate).

---

## 3. Mapeo de inspiración → G-Mini

| Patrón origen | Repo | Adaptación en G-Mini |
|---|---|---|
| Wizard stepwise dirigido por servidor (start→step→answer→next, status running/done/cancelled/error) | openclaw `gateway-protocol/.../wizard.ts` | `OnboardingService` en backend que emite pasos por Socket.IO; UI Electron renderiza cada paso. |
| Profile-build opt-in, consent-gated, en el primer mensaje; guarda en memoria con `target="user"` | hermes `agent/onboarding.py` | Directiva inyectada al primer mensaje; el agente ofrece "conocerte", guarda en `memory_ltm` (FACT/PREFERENCE). |
| Hints contextuales "una sola vez" por bifurcación de comportamiento, flag `onboarding.seen.<flag>` | hermes `agent/onboarding.py` | Mismos flags persistidos en `config.user.yaml` bajo `onboarding.seen.*`. |
| Background-review tras cada turno: fork con whitelist memoria+skills decide qué guardar | hermes `agent/background_review.py` | `LearningService.reflect_on_turn()` — fork con modelo aux barato + digest, escribe a `memory_ltm`/`rlhf_lite`. |
| Curator inactividad: pin/archive/consolidate de skills creadas por el agente, **nunca borra** | hermes `agent/curator.py` | `SkillCurator` disparado por inactividad vía `scheduler`. |
| Setup flows de modelo/keys/memoria/"soul" (personalidad) | hermes `hermes_cli/*` | Pasos del wizard + `agent.soul` (personalidad) en config. |
| Motor de embeddings real | openclaw `memory-host-sdk` | `EmbeddingProvider` con Gemini/OpenAI + fallback hash. |

---

## 4. Arquitectura — 3 pilares

```
┌──────────────────────── G-Mini Agent ────────────────────────┐
│                                                               │
│  PILAR A — Onboarding (first-run)                             │
│   ┌─────────────────┐   ┌──────────────────────────────┐     │
│   │ OnboardingService│  │ Directiva profile-build       │     │
│   │ (wizard stepwise)│  │ (1er mensaje, opt-in)         │     │
│   └────────┬────────┘   └───────────────┬──────────────┘     │
│            │ Socket.IO steps             │ system note         │
│            ▼                             ▼                     │
│        Electron UI                   agent.process_message     │
│                                                               │
│  PILAR B — Aprendizaje continuo                               │
│   process_message ──(tras turno sustancial)──► LearningService│
│        │                                         │ fork aux    │
│        │ recall                                  ▼             │
│   _apply_system_prompt ◄── build_memory_context ── memory_ltm  │
│   scheduler (idle) ──► LearningService.consolidate()          │
│   EmbeddingProvider (real + hash fallback) ─► memory_ltm      │
│                                                               │
│  PILAR C — Skills propias + Curator                          │
│   agent ──(tool skill_author)──► SkillRegistry.create_skill   │
│   scheduler (idle) ──► SkillCurator (pin/archive/consolidate) │
└───────────────────────────────────────────────────────────────┘
```

### Pilar A — Onboarding de primera ejecución (híbrido)

**A1. Setup Wizard (visual, dirigido por servidor)**
- Nuevo `backend/core/onboarding.py` → `OnboardingService`.
- Modelo stepwise (copiado de openclaw): `start()` devuelve el primer paso; `answer(step_id, value)` valida y avanza; `status` ∈ `running|done|cancelled|error`; `cancel()`.
- Pasos (cada uno con `id`, `type`, `title`, `help`, `options/validation`):
  1. `welcome` — intro + idioma.
  2. `provider_keys` — elegir ≥1 proveedor y pegar API key (se guarda con `config.set_api_key`). Botón "probar conexión".
  3. `default_model` — modelo coordinador por defecto (lista filtrada a proveedores con key) → `config.set("model_router","default_model",...)`.
  4. `autonomy` — `agent.autonomy` (baja/media/alta) + `agent.autonomy_level` (asistido/supervisado/libre).
  5. `voice` — activar voz, motor TTS, idioma STT (opcional).
  6. `avatar` — tipo (3d/2d/none) y skin.
  7. `embeddings` — activar embeddings reales (si hay key) o usar hash.
  8. `profile_optin` — ofrecer el flujo conversacional "conóceme" (sí/después/no).
  9. `done` — marca `onboarding.completed=true`.
- Transporte: eventos Socket.IO `onboarding:step`, `onboarding:answer`, `onboarding:done`; endpoints REST equivalentes para robustez.
- **Detección de primer arranque:** `config.get("onboarding","completed")` ausente/false. `initialize()` marca `needs_onboarding=true` y emite `onboarding:required` al conectar la UI.
- UI Electron: nueva vista `onboarding.html` (o panel modal en `index.html`) que renderiza pasos genéricos a partir del esquema enviado por el backend (sin hardcodear cada paso en el front).

**A2. Profile-build conversacional (opt-in)**
- Copia adaptada de `hermes/onboarding.py::profile_build_directive()`.
- Si el usuario aceptó en `profile_optin` (o `profile_build_mode == "ask"`), al **primer mensaje real** se inyecta una nota de sistema que instruye al agente a: presentarse en una frase, **ofrecer** (no asumir) construir un perfil, pedir solo lo que el usuario quiera compartir, **pedir consentimiento antes de cualquier lookup externo**, y guardar hechos durables vía la herramienta de memoria (FACT/PREFERENCE). Si declina, continúa normal.
- Flags `onboarding.seen.profile_build_offered` para no repetir.

**A3. Hints contextuales (una sola vez)**
- Port directo de los flags `onboarding.seen.<flag>` de hermes para tips de primera vez (p. ej. primera acción de automatización, primer uso de voz). Bajo costo, alto valor de UX.

### Pilar B — Aprendizaje continuo y auto-mejora (híbrido configurable)

**B1. Reflexión tras turno sustancial** (`backend/core/learning.py` → `LearningService`)
- Hook en `process_message` (y `process_message_live`): al cerrar un turno, si fue "sustancial" (heurística: nº de mensajes/acciones/longitud, no saludos), encola `reflect_on_turn(snapshot)`.
- `reflect_on_turn`: corre un **fork** con un cliente auxiliar (provider/modelo de `auxiliary.learning.*`, por defecto un modelo barato; ver §7) y un **digest** compacto del turno. El fork tiene **whitelist de herramientas: solo memoria + rlhf** (todo lo demás denegado). Decide qué `FACT/PREFERENCE/TASK/LEARNING` guardar/actualizar en `memory_ltm` y qué señales en `rlhf_lite`.
- Ejecutado en background (no bloquea la respuesta al usuario; daemon task con refs vivas — mismo patrón ya usado en `scheduler`/`agent` para fire-and-forget).
- Anti-duplicado: antes de `store()`, `search()` por similitud alta y `update_importance()` en vez de duplicar.

**B2. Consolidación por inactividad / fin de sesión**
- Job en `scheduler` (ya existe `scheduler.py` con triggers de intervalo/idle): cuando el agente lleva `min_idle` sin actividad, `LearningService.consolidate()` fusiona memorias redundantes, decae importancia de las viejas, y resume preferencias en `rlhf_lite`.

**B3. Recall: inyección de memoria al prompt**
- Nuevo `build_memory_context(query)` en `modes.py` (o `memory_ltm`): recupera top-k memorias relevantes (por embedding) + perfil de usuario, y devuelve un bloque `[MEMORIA RELEVANTE: ...]`.
- Se añade en `agent._apply_system_prompt()` después de `build_autonomy_context()`. Para recall por-consulta (no solo estático), inyectar las memorias más relevantes al último mensaje del usuario en `process_message` antes de llamar al LLM (presupuesto de tokens acotado y configurable).

**B4. Embeddings reales + fallback**
- Nuevo `backend/core/embeddings.py` → `EmbeddingProvider`:
  - Si hay key del proveedor configurado (`memory.embedding_provider`: `google|openai`), usa el endpoint de embeddings (p. ej. Gemini `gemini-embedding-001` / OpenAI `text-embedding-3-small`).
  - Si no, usa el hash actual (mover la lógica de `memory_ltm._get_embedding` aquí como `HashEmbedder`).
  - Cachea embeddings; normaliza dimensión. `memory_ltm` delega en este proveedor.
- Migración: re-embeddear memorias existentes en background al cambiar de proveedor (opcional, marcado por dimensión).

**B5. Señales RLHF**
- Pulgares/correcciones del usuario y "deshacer" → `rlhf_lite.record_signal()`. Alimenta `consolidate()` y, a futuro, ajuste de comportamiento por modo.

### Pilar C — Skills propias + Curator

**C1. Auto-autoría de skills** (extiende `skill_registry.py`)
- Nuevos métodos: `create_skill(name, manifest, files, origin="agent")`, `update_skill()`, `set_lifecycle(state)`, `archive_skill()`, `pin_skill()`, marca `agent_created=true` y timestamps de actividad.
- Nueva herramienta de agente `skill_author` (disponible para el agente y para el fork de reflexión): permite empaquetar un procedimiento repetido en una skill (manifest + script) que `skill_runtime` puede ejecutar luego. Sandbox y least-privilege ya existen en `skills_security`.
- Invariante: las skills creadas por el agente viven separadas (`data/skills/_agent/…`) y se ejecutan con el modo sandbox por defecto.

**C2. Curator** (`backend/core/skill_curator.py`)
- Port adaptado de `hermes/curator.py`. Disparado por **inactividad** vía `scheduler` (no daemon propio): si idle > `min_idle_hours` y último run > `interval_hours`, corre.
- Acciones sobre **solo skills creadas por el agente**: auto-transición de lifecycle por actividad (active→stale→archive), `consolidate` (fusionar skills solapadas, opt-in/off por defecto), `patch`. **Nunca borra** (solo archiva, recuperable). Las skills `pinned` se saltan toda auto-transición.
- Estado en `data/curator_state.json`; reportes en `data/curator_reports/`.

---

## 5. Almacenamiento

- Reutiliza `data/memory_ltm.db` (memorias) y añade:
  - tabla `user_profile` (perfil estructurado del onboarding/profile-build) o categoría dedicada en memory_ltm.
  - índice de embeddings con columna `embedding_dim` y `embedding_model` para soportar migración.
- `data/scheduler.db` (jobs de consolidación y curator).
- `data/curator_state.json`, `data/curator_reports/`.
- Skills del agente: `data/skills/_agent/`.

---

## 6. Esquema de configuración (config.default.yaml — añadidos)

```yaml
onboarding:
  completed: false              # se pone true al terminar el wizard
  profile_build: "ask"          # ask | off
  seen: {}                      # flags de hints de una sola vez

auxiliary:                      # modelos auxiliares (baratos) para tareas de fondo
  learning:
    provider: ""                # "" = hereda del router; o p.ej. "google"
    model: ""                   # p.ej. "gemini-3.1-flash-lite"
  curator:
    provider: ""
    model: ""

memory:
  recall_enabled: true
  recall_top_k: 6
  recall_max_tokens: 1200
  embedding_provider: "auto"    # auto | google | openai | hash
  embedding_model: ""           # "" = default del provider

learning:
  enabled: true
  reflect_after_turn: true      # B1
  substantial_min_messages: 2   # heurística de "turno sustancial"
  consolidate_on_idle: true     # B2
  min_idle_minutes: 10

curator:
  enabled: true
  interval_hours: 168           # 7 días
  min_idle_hours: 2
  consolidate: false            # fusión LLM OFF por defecto (como hermes)
  stale_after_days: 30
  archive_after_days: 90

agent:
  soul: ""                      # personalidad/“soul” opcional añadida al system prompt
```

---

## 7. Costo, privacidad y seguridad

- **Costo:** la reflexión usa modelo aux barato + digest (no replay completo) salvo que el usuario elija el modelo principal (cache caliente). Reutiliza el `cost_optimizer`/`budget` existentes; la reflexión respeta el presupuesto y se salta si hay presión alta.
- **Privacidad:** profile-build es opt-in y consent-gated; **nunca** lee cuentas conectadas sin pedir permiso en cada paso (copiado textual de la filosofía de hermes). Las API keys siguen en Credential Manager.
- **Seguridad:** el fork de reflexión y la herramienta `skill_author` corren con whitelist de herramientas y sandbox (`skills_security`, `sandbox`, `rbac` ya existen). El curator nunca borra.

---

## 8. Límites de módulo (archivos nuevos vs tocados)

Nuevos:
- `backend/core/onboarding.py` (OnboardingService + pasos)
- `backend/core/learning.py` (LearningService: reflect/consolidate)
- `backend/core/embeddings.py` (EmbeddingProvider + HashEmbedder)
- `backend/core/skill_curator.py` (SkillCurator)
- `electron/src/onboarding.html` + `electron/src/js/onboarding.js` (renderer genérico de pasos)
- tests: `tests/test_onboarding.py`, `tests/test_learning.py`, `tests/test_embeddings.py`, `tests/test_skill_curator.py`

Tocados (cambios acotados):
- `backend/core/agent.py` — hook de reflexión en `process_message`/`process_message_live`; recall en `_apply_system_prompt` + por-consulta; bandera first-run en `initialize`; registrar herramienta `skill_author`.
- `backend/core/modes.py` — `build_memory_context()` + `agent.soul` en el prompt.
- `backend/core/memory_ltm.py` — delegar embeddings a `EmbeddingProvider`; helpers de dedup/decay.
- `backend/core/skill_registry.py` — métodos create/lifecycle/archive/pin + `agent_created`.
- `backend/core/scheduler.py` — registrar jobs `learning_consolidate` y `skill_curator`.
- `backend/api/routes.py` + `backend/api/websocket_handler.py` — endpoints/eventos del wizard.
- `electron/src/js/app.js` + `index.html` — disparar onboarding cuando `onboarding:required`.
- `config.default.yaml` + `config.user.yaml.example` — esquema §6.

---

## 9. Estrategia de pruebas

- `test_onboarding.py` — máquina de pasos: start→answer→done, validación, cancel, idempotencia de `completed`, persistencia de `seen`.
- `test_learning.py` — `reflect_on_turn` produce escrituras correctas con un cliente LLM mockeado; dedup (no duplica memorias casi-iguales); turno trivial NO dispara reflexión.
- `test_embeddings.py` — fallback hash determinista; provider real mockeado; cambio de dimensión marca migración.
- `test_skill_curator.py` — transición active→stale→archive; pinned se salta; nunca borra; solo toca `agent_created`.
- Regla del proyecto: cada lógica no trivial deja un test runnable; CI = `compileall` + guard CORS (los tests de app corren local).

---

## 10. Fases de implementación (orden sugerido)

1. **Fundación de memoria** — `embeddings.py` + integrar en `memory_ltm` + `build_memory_context` recall en prompt. (Valor inmediato: el agente recuerda.)
2. **Aprendizaje** — `learning.py` reflect_on_turn (hook en agent) + consolidate por idle (scheduler).
3. **Onboarding** — `onboarding.py` wizard + eventos + UI Electron + profile-build directive + hints.
4. **Skills propias** — `skill_registry` create/lifecycle + herramienta `skill_author`.
5. **Curator** — `skill_curator.py` + job idle en scheduler.
6. **Config + docs + pruebas** transversales en cada fase.

Cada fase es enviable por separado y deja tests. Las fases 1–2 dan "aprende y recuerda"; 3 da "onboarding"; 4–5 dan "auto-mejora de skills".

---

## 11. Fuera de alcance (por ahora)

- Sincronización de memoria entre dispositivos (ya hay `sync.py`; integrar luego).
- Migración desde `~/.openclaw`/`~/.hermes` (el equivalente de `claw migrate`): no aplica.
- Fine-tuning real de modelos; RLHF aquí es señales ligeras, no entrenamiento.
- App companion móvil para el wizard.
