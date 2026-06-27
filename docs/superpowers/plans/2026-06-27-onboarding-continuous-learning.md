# Onboarding + Aprendizaje Continuo y Auto-mejora — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar a G-Mini Agent un onboarding de primera ejecución (wizard visual + perfil conversacional opt-in) y un sistema de aprendizaje continuo que recuerda, reflexiona y mantiene/mejora sus propias skills.

**Architecture:** 5 fases secuenciales e independientemente enviables. (1) Fundación de memoria: embeddings reales + recall al prompt. (2) Aprendizaje: reflexión tras turno + consolidación por inactividad. (3) Onboarding: wizard stepwise dirigido por backend + directiva profile-build. (4) Skills propias: auto-autoría. (5) Curator: mantenimiento por inactividad. Todo reutiliza la infra existente (`config`/keyring, `scheduler`, `memory_ltm`, `skill_registry`, `rlhf_lite`, providers/router).

**Tech Stack:** Python 3.11 (FastAPI + Socket.IO backend), SQLite, numpy, providers existentes (Google/OpenAI vía `backend/providers`), Electron 30 (frontend). Tests: `unittest` (patrón ya usado en `tests/`).

**Spec de referencia:** `docs/superpowers/specs/2026-06-27-onboarding-continuous-learning-design.md`

---

## Convenciones (leer antes de empezar)

- **Ejecutar tests:** desde la raíz del repo, `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_<x> -v`.
- **Compile check:** `venv/Scripts/python.exe -m compileall -q backend`.
- **Config:** leer con `config.get("a","b", default=...)`, escribir con `config.set("a","b", value=...)`. API keys: `config.get_api_key(vault)` / `config.set_api_key(vault, key)`.
- **Singletons:** patrón `get_xxx()` (ver `memory_ltm.get_ltm()`). Replicarlo para los nuevos servicios.
- **Logging:** `from loguru import logger`.
- **Commits:** conventional commits en español/inglés como el historial; **no** añadir crédito de Claude/Co-Authored-By.
- **TDD:** test primero, verlo fallar, implementar mínimo, verlo pasar, commit.
- **Patrón fire-and-forget:** mantener refs vivas de tasks daemon (ver `scheduler._bg_tasks` / `agent._on_bg_task_done`) para que el GC no las recolecte.

---

## File Structure

**Nuevos:**
- `backend/core/embeddings.py` — `EmbeddingProvider` (real + `HashEmbedder` fallback), singleton `get_embedder()`.
- `backend/core/learning.py` — `LearningService` (`reflect_on_turn`, `consolidate`), singleton `get_learning()`.
- `backend/core/onboarding.py` — `OnboardingService` (máquina de pasos), `profile_build_directive()`, hints `seen`.
- `backend/core/skill_curator.py` — `SkillCurator`, singleton `get_curator()`.
- `electron/src/onboarding.html`, `electron/src/js/onboarding.js` — renderer genérico de pasos.
- `tests/test_embeddings.py`, `tests/test_learning.py`, `tests/test_onboarding.py`, `tests/test_skill_curator.py`.

**Modificados:**
- `backend/core/memory_ltm.py` — delega embeddings; helpers dedup/decay; perfil de usuario.
- `backend/core/modes.py` — `build_memory_context()` + `agent.soul`.
- `backend/core/agent.py` — recall en `_apply_system_prompt`; hook de reflexión en `process_message`/`process_message_live`; flag first-run en `initialize`; herramienta `skill_author`.
- `backend/core/skill_registry.py` — `create_skill/update_skill/set_lifecycle/archive_skill/pin_skill` + `agent_created`.
- `backend/core/scheduler.py` — jobs `learning_consolidate` y `skill_curator`.
- `backend/api/routes.py`, `backend/api/websocket_handler.py` — endpoints/eventos del wizard.
- `electron/src/js/app.js`, `electron/src/index.html` — disparo de onboarding.
- `config.default.yaml`, `config.user.yaml.example` — esquema (§6 del spec).

---

# FASE 1 — Fundación de memoria (embeddings reales + recall)

Resultado al terminar: el agente genera embeddings reales cuando hay API key (hash si no), y **recuerda** memorias relevantes inyectándolas al system prompt.

### Task 1.1: EmbeddingProvider con fallback hash

**Files:**
- Create: `backend/core/embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] **Step 1: Test que falla**

```python
# tests/test_embeddings.py
"""Tests del proveedor de embeddings (real con fallback hash)."""
import unittest
from backend.core.embeddings import HashEmbedder, EmbeddingProvider, get_embedder


class HashEmbedderTest(unittest.TestCase):
    def test_deterministic_and_dim(self):
        e = HashEmbedder(dim=384)
        v1 = e.embed("hola mundo")
        v2 = e.embed("hola mundo")
        self.assertEqual(len(v1), 384)
        self.assertEqual(v1, v2)                       # determinista
        self.assertNotEqual(v1, e.embed("otra cosa"))  # distinto texto -> distinto vec

    def test_provider_falls_back_to_hash_without_key(self):
        # Sin provider configurado -> usa hash, dim por defecto
        p = EmbeddingProvider(provider="hash", dim=384)
        v = p.embed("texto")
        self.assertEqual(len(v), 384)
        self.assertEqual(p.model_id, "hash")

    def test_singleton(self):
        self.assertIs(get_embedder(), get_embedder())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Verlo fallar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_embeddings -v`
Expected: `ModuleNotFoundError: backend.core.embeddings`.

- [ ] **Step 3: Implementar `embeddings.py`**

```python
# backend/core/embeddings.py
"""Proveedor de embeddings: real (Google/OpenAI) con fallback hash determinista.

memory_ltm delega aquí. Si hay API key del provider configurado, usa embeddings
reales; si no, cae a HashEmbedder (sin dependencias ML, recall pobre pero funcional).
"""
from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np
from loguru import logger

from backend.config import config


class Embedder(Protocol):
    model_id: str
    dim: int
    def embed(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Embedding determinista basado en hash (sin ML). Igual que el fallback previo."""
    model_id = "hash"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in (text or "").lower().split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            for j in range(4):
                idx = (h >> (j * 8)) % self.dim
                vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()


class _GoogleEmbedder:
    """Embeddings vía Google GenAI (gemini-embedding-001 por defecto)."""
    def __init__(self, model: str, dim: int) -> None:
        from google import genai  # import perezoso
        self.model_id = model
        self.dim = dim
        self._client = genai.Client(api_key=config.get_api_key("google_api"))

    def embed(self, text: str) -> list[float]:
        r = self._client.models.embed_content(model=self.model_id, contents=text or " ")
        return list(r.embeddings[0].values)


class _OpenAIEmbedder:
    """Embeddings vía OpenAI (text-embedding-3-small por defecto)."""
    def __init__(self, model: str, dim: int) -> None:
        from openai import OpenAI
        self.model_id = model
        self.dim = dim
        self._client = OpenAI(api_key=config.get_api_key("openai_api"))

    def embed(self, text: str) -> list[float]:
        r = self._client.embeddings.create(model=self.model_id, input=text or " ")
        return list(r.data[0].embedding)


class EmbeddingProvider:
    """Fachada: elige real o hash según config + disponibilidad de key."""
    def __init__(self, provider: str | None = None, model: str | None = None, dim: int = 384) -> None:
        provider = (provider or config.get("memory", "embedding_provider", default="auto") or "auto").lower()
        model = model or config.get("memory", "embedding_model", default="") or ""
        self.dim = dim
        self._impl: Embedder = self._select(provider, model, dim)
        self.model_id = self._impl.model_id
        logger.info(f"EmbeddingProvider activo: {self.model_id}")

    def _select(self, provider: str, model: str, dim: int) -> Embedder:
        try:
            if provider in ("auto", "google") and config.get_api_key("google_api"):
                return _GoogleEmbedder(model or "gemini-embedding-001", 3072)
            if provider in ("auto", "openai") and config.get_api_key("openai_api"):
                return _OpenAIEmbedder(model or "text-embedding-3-small", 1536)
        except Exception as exc:
            logger.warning(f"Embeddings reales no disponibles ({exc}); usando hash.")
        return HashEmbedder(dim)

    def embed(self, text: str) -> list[float]:
        try:
            return self._impl.embed(text)
        except Exception as exc:
            logger.warning(f"Fallo embed real ({exc}); cae a hash este texto.")
            return HashEmbedder(self.dim).embed(text)


_embedder: EmbeddingProvider | None = None


def get_embedder() -> EmbeddingProvider:
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingProvider()
    return _embedder
```

- [ ] **Step 4: Verlo pasar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_embeddings -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/core/embeddings.py tests/test_embeddings.py
git commit -m "feat(memory): proveedor de embeddings real (Google/OpenAI) con fallback hash"
```

---

### Task 1.2: memory_ltm delega en EmbeddingProvider + guarda model/dim

**Files:**
- Modify: `backend/core/memory_ltm.py` (método `_get_embedding`, esquema de tabla, `store`)
- Test: `tests/test_embeddings.py` (añadir caso de integración)

- [ ] **Step 1: Test que falla** (añadir a `tests/test_embeddings.py`)

```python
class MemoryLTMUsesProviderTest(unittest.TestCase):
    def test_store_and_search_roundtrip(self):
        import tempfile, os
        from backend.core.memory_ltm import LongTermMemory, MemoryCategory
        db = os.path.join(tempfile.mkdtemp(), "ltm.db")
        ltm = LongTermMemory(db_path=db)
        mid = ltm.store("El usuario prefiere respuestas concisas", MemoryCategory.PREFERENCE)
        self.assertTrue(mid)
        hits = ltm.search("¿cómo le gustan las respuestas al usuario?", top_k=3)
        self.assertTrue(any("concisas" in h.text for h in hits))
```

- [ ] **Step 2: Verlo fallar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_embeddings.MemoryLTMUsesProviderTest -v`
Expected: FAIL si `LongTermMemory` no acepta `db_path` o no recupera. (Si ya acepta `db_path`, el test puede pasar con hash — igual continúa para cablear el provider.)

- [ ] **Step 3: Cambiar `_get_embedding` para delegar**

En `backend/core/memory_ltm.py`, reemplazar el cuerpo de `_get_embedding` por:

```python
    def _get_embedding(self, text: str) -> list[float]:
        from backend.core.embeddings import get_embedder
        return get_embedder().embed(text)
```

Asegurar que `__init__` acepta `db_path: str | None = None` (usar `config.get("memory","db_path")` por defecto). Añadir columnas `embedding_model TEXT` y `embedding_dim INTEGER` a la tabla (con `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` defensivo en un `try/except`), y guardarlas en `store()` desde `get_embedder().model_id`/`.dim`. Ajustar `search()` para ignorar (o re-embeddear) entradas cuya `embedding_dim` no coincida con la actual.

- [ ] **Step 4: Verlo pasar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_embeddings -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/core/memory_ltm.py tests/test_embeddings.py
git commit -m "feat(memory): memory_ltm usa EmbeddingProvider y versiona model/dim"
```

---

### Task 1.3: `build_memory_context()` (recall) en modes.py

**Files:**
- Modify: `backend/core/modes.py` (nueva función)
- Test: `tests/test_learning.py` (crear archivo con este primer test)

- [ ] **Step 1: Test que falla**

```python
# tests/test_learning.py
"""Tests de recall e inyección de memoria + reflexión."""
import os, tempfile, unittest
from backend.core.memory_ltm import LongTermMemory, MemoryCategory


class MemoryContextTest(unittest.TestCase):
    def test_build_memory_context_returns_relevant_block(self):
        from backend.core.modes import build_memory_context
        db = os.path.join(tempfile.mkdtemp(), "ltm.db")
        ltm = LongTermMemory(db_path=db)
        ltm.store("El usuario se llama Gabriel", MemoryCategory.FACT)
        block = build_memory_context("¿cómo me llamo?", ltm=ltm, top_k=3)
        self.assertIn("Gabriel", block)
        self.assertIn("MEMORIA", block.upper())

    def test_empty_when_no_memories(self):
        from backend.core.modes import build_memory_context
        db = os.path.join(tempfile.mkdtemp(), "ltm.db")
        ltm = LongTermMemory(db_path=db)
        self.assertEqual(build_memory_context("hola", ltm=ltm), "")
```

- [ ] **Step 2: Verlo fallar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_learning.MemoryContextTest -v`
Expected: FAIL (`build_memory_context` no existe).

- [ ] **Step 3: Implementar en `modes.py`**

```python
def build_memory_context(query: str, ltm=None, top_k: int | None = None, max_tokens: int | None = None) -> str:
    """Bloque de system prompt con memorias relevantes a `query`. Vacío si no hay."""
    if not config.get("memory", "recall_enabled", default=True):
        return ""
    if ltm is None:
        from backend.core.memory_ltm import get_ltm
        ltm = get_ltm()
    top_k = top_k or int(config.get("memory", "recall_top_k", default=6))
    budget = max_tokens or int(config.get("memory", "recall_max_tokens", default=1200))
    try:
        hits = ltm.search(query, top_k=top_k)
    except Exception:
        return ""
    if not hits:
        return ""
    lines, used = [], 0
    for h in hits:
        line = f"- ({h.category}) {h.text}"
        used += len(line) // 4  # ~4 chars/token
        if used > budget:
            break
        lines.append(line)
    if not lines:
        return ""
    return "[MEMORIA RELEVANTE DEL USUARIO:\n" + "\n".join(lines) + "\n]"
```

(Asegurar `from backend.config import config` ya está importado en `modes.py`.)

- [ ] **Step 4: Verlo pasar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_learning.MemoryContextTest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/core/modes.py tests/test_learning.py
git commit -m "feat(memory): build_memory_context para recall en el prompt"
```

---

### Task 1.4: Inyectar recall + `agent.soul` en `_apply_system_prompt`

**Files:**
- Modify: `backend/core/agent.py` (`_apply_system_prompt` ~L2233; `process_message` para recall por-consulta)

- [ ] **Step 1: Recall estático en `_apply_system_prompt`**

En `agent._apply_system_prompt`, tras la línea `prompt = prompt + "\n\n" + build_autonomy_context()`, añadir:

```python
        soul = str(config.get("agent", "soul", default="") or "").strip()
        if soul:
            prompt = prompt + "\n\n[PERSONALIDAD: " + soul + "]"
        # Recall estático: perfil de usuario de alta importancia (no por-consulta).
        from backend.core.modes import build_memory_context
        profile_block = build_memory_context("perfil del usuario preferencias nombre")
        if profile_block:
            prompt = prompt + "\n\n" + profile_block
```

- [ ] **Step 2: Recall por-consulta en `process_message`**

En `process_message`, justo antes de construir/enviar el contexto al LLM, recuperar memorias relevantes al mensaje del usuario y adjuntarlas como nota de sistema del turno (no al prompt cacheado). Usar `build_memory_context(text)`; si no vacío, anteponerlo al mensaje o añadirlo como system-note del turno (seguir el patrón con que `agent` ya inserta notas de turno).

- [ ] **Step 3: Compile + smoke**

Run: `venv/Scripts/python.exe -m compileall -q backend/core/agent.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add backend/core/agent.py
git commit -m "feat(memory): inyectar recall de memoria y soul en el system prompt"
```

---

### Task 1.5: Config Fase 1

**Files:**
- Modify: `config.default.yaml`, `config.user.yaml.example`

- [ ] **Step 1:** Añadir bajo la clave `memory:` (ya existe) las claves `recall_enabled: true`, `recall_top_k: 6`, `recall_max_tokens: 1200`, `embedding_provider: "auto"`, `embedding_model: ""`. Añadir `agent.soul: ""`.
- [ ] **Step 2: Commit**

```bash
git add config.default.yaml config.user.yaml.example
git commit -m "chore(config): claves de recall, embeddings y soul"
```

---

# FASE 2 — Aprendizaje continuo

Resultado: tras turnos sustanciales, un fork con modelo aux barato decide qué guardar en `memory_ltm`/`rlhf_lite`; por inactividad se consolida.

### Task 2.1: `LearningService.reflect_on_turn` (fork de reflexión)

**Files:**
- Create: `backend/core/learning.py`
- Test: `tests/test_learning.py` (añadir clase)

- [ ] **Step 1: Test que falla**

```python
class ReflectOnTurnTest(unittest.TestCase):
    def test_trivial_turn_skipped(self):
        from backend.core.learning import LearningService
        svc = LearningService(ltm=_FakeLTM(), llm=_FakeLLM(returns="[]"))
        # turno trivial (saludo) -> no reflexiona
        wrote = svc.reflect_on_turn_sync([{"role": "user", "content": "hola"}])
        self.assertEqual(wrote, 0)

    def test_extracts_and_dedups(self):
        from backend.core.learning import LearningService
        ltm = _FakeLTM()
        llm = _FakeLLM(returns='[{"category":"fact","text":"El usuario usa Windows"}]')
        svc = LearningService(ltm=ltm, llm=llm)
        convo = [
            {"role": "user", "content": "configura mi proyecto Python en Windows con venv"},
            {"role": "assistant", "content": "Listo, creé el venv en Windows."},
        ]
        wrote = svc.reflect_on_turn_sync(convo)
        self.assertEqual(wrote, 1)
        # segunda vez: dedup (no duplica)
        wrote2 = svc.reflect_on_turn_sync(convo)
        self.assertEqual(wrote2, 0)
```

Incluir helpers de test al inicio del archivo:

```python
class _FakeLTM:
    def __init__(self): self.items = []
    def search(self, q, top_k=5):
        class H:  # noqa
            pass
        out = []
        for cat, txt in self.items:
            if any(w in txt.lower() for w in q.lower().split()):
                h = H(); h.text = txt; h.category = cat; h.score = 0.95
                out.append(h)
        return out[:top_k]
    def store(self, text, category, **kw):
        self.items.append((str(category), text)); return f"id{len(self.items)}"

class _FakeLLM:
    def __init__(self, returns="[]"): self._r = returns
    def complete(self, system, messages, max_tokens=512): return self._r
```

- [ ] **Step 2: Verlo fallar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_learning.ReflectOnTurnTest -v`
Expected: FAIL (`backend.core.learning` no existe).

- [ ] **Step 3: Implementar `learning.py`**

```python
# backend/core/learning.py
"""Aprendizaje continuo: reflexión tras turno + consolidación por inactividad.

reflect_on_turn corre en background con un cliente LLM auxiliar (barato) y un
DIGEST del turno; extrae hechos/preferencias/lecciones y los guarda en memory_ltm
con dedup. Nunca bloquea la respuesta al usuario. Whitelist conceptual: solo
escribe memoria/rlhf (no ejecuta herramientas del agente).
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from backend.config import config

_REFLECT_SYSTEM = (
    "Eres el módulo de memoria de un agente. Lee el turno y extrae SOLO hechos "
    "durables y de alto valor sobre el USUARIO o lecciones de trabajo reutilizables. "
    "Devuelve EXCLUSIVAMENTE un JSON array de objetos {\"category\": one of "
    "[fact,preference,task,learning], \"text\": \"...\"}. Si no hay nada que valga "
    "la pena recordar, devuelve []. No incluyas datos sensibles ni secretos."
)


class LearningService:
    def __init__(self, ltm=None, rlhf=None, llm=None) -> None:
        self._ltm = ltm
        self._rlhf = rlhf
        self._llm = llm  # objeto con .complete(system, messages, max_tokens) -> str

    # ── helpers ──
    def _get_ltm(self):
        if self._ltm is None:
            from backend.core.memory_ltm import get_ltm
            self._ltm = get_ltm()
        return self._ltm

    def _is_substantial(self, convo: list[dict[str, Any]]) -> bool:
        min_msgs = int(config.get("learning", "substantial_min_messages", default=2))
        if len(convo) < min_msgs:
            return False
        user_text = " ".join(m.get("content", "") for m in convo if m.get("role") == "user")
        return len(user_text.strip()) >= 12  # filtra "hola", "ok", "gracias"

    def _digest(self, convo: list[dict[str, Any]], tail: int = 12) -> list[dict]:
        return [{"role": m.get("role"), "content": (m.get("content") or "")[:1500]}
                for m in convo[-tail:]]

    def _category(self, raw: str):
        from backend.core.memory_ltm import MemoryCategory
        try:
            return MemoryCategory(raw)
        except Exception:
            return MemoryCategory.LEARNING

    def _already_known(self, text: str) -> bool:
        try:
            hits = self._get_ltm().search(text, top_k=3)
        except Exception:
            return False
        for h in hits:
            if getattr(h, "score", 0) >= 0.92:  # casi idéntico
                return True
        return False

    # ── API ──
    def reflect_on_turn_sync(self, convo: list[dict[str, Any]]) -> int:
        """Versión síncrona (testeable). Devuelve nº de memorias escritas."""
        if not config.get("learning", "enabled", default=True):
            return 0
        if not self._is_substantial(convo):
            return 0
        if self._llm is None:
            from backend.core.learning_llm import get_aux_llm  # ver Task 2.2
            self._llm = get_aux_llm()
        raw = self._llm.complete(_REFLECT_SYSTEM, self._digest(convo), max_tokens=512)
        try:
            items = json.loads(raw)
            assert isinstance(items, list)
        except Exception:
            logger.debug(f"reflect: salida no-JSON, ignorada: {raw!r}")
            return 0
        wrote = 0
        for it in items:
            text = str(it.get("text", "")).strip()
            if not text or self._already_known(text):
                continue
            self._get_ltm().store(text, self._category(str(it.get("category", "learning"))))
            wrote += 1
        if wrote:
            logger.info(f"reflect_on_turn: {wrote} memorias nuevas")
        return wrote

    async def reflect_on_turn(self, convo: list[dict[str, Any]]) -> None:
        """Lanzar en background; no propaga excepciones."""
        import asyncio
        try:
            await asyncio.to_thread(self.reflect_on_turn_sync, convo)
        except Exception as exc:
            logger.warning(f"reflect_on_turn falló (no crítico): {exc}")

    def consolidate(self) -> dict[str, int]:
        """Decae importancia de memorias viejas y fusiona casi-duplicadas. Idempotente."""
        # Implementación mínima: recorrer, bajar importancia con la edad. Fusión opcional.
        return {"decayed": 0, "merged": 0}  # ver Task 2.3 para la lógica real


_learning: LearningService | None = None


def get_learning() -> LearningService:
    global _learning
    if _learning is None:
        _learning = LearningService()
    return _learning
```

- [ ] **Step 4: Verlo pasar**

Run: `PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_learning.ReflectOnTurnTest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/core/learning.py tests/test_learning.py
git commit -m "feat(learning): reflect_on_turn con extracción + dedup de memorias"
```

---

### Task 2.2: Cliente LLM auxiliar (`learning_llm.py`)

**Files:**
- Create: `backend/core/learning_llm.py`
- Test: `tests/test_learning.py` (un test de selección de modelo)

- [ ] **Step 1: Test que falla**

```python
class AuxLLMTest(unittest.TestCase):
    def test_resolves_provider_model(self):
        from backend.core.learning_llm import resolve_aux_binding
        prov, model = resolve_aux_binding(section="learning")
        self.assertTrue(isinstance(prov, str) and isinstance(model, str))
```

- [ ] **Step 2: Verlo fallar** — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar** un wrapper delgado sobre `backend/providers/router.py`:

```python
# backend/core/learning_llm.py
"""Cliente LLM auxiliar (barato) para tareas de fondo (reflexión, curator).

Resuelve provider/model desde auxiliary.<section>.{provider,model}; si está vacío,
hereda el default del router. Expone .complete(system, messages, max_tokens) -> str.
"""
from __future__ import annotations

from backend.config import config


def resolve_aux_binding(section: str) -> tuple[str, str]:
    prov = config.get("auxiliary", section, "provider", default="") or ""
    model = config.get("auxiliary", section, "model", default="") or ""
    if not prov or not model:
        prov = prov or config.get("model_router", "default_provider", default="google")
        model = model or config.get("model_router", "default_model", default="gemini-3.1-flash-lite")
    return prov, model


class AuxLLM:
    def __init__(self, section: str = "learning") -> None:
        self.provider, self.model = resolve_aux_binding(section)

    def complete(self, system: str, messages: list[dict], max_tokens: int = 512) -> str:
        from backend.providers.router import get_router  # adaptar al API real del router
        router = get_router()
        return router.complete_sync(
            provider=self.provider, model=self.model,
            system=system, messages=messages, max_tokens=max_tokens, temperature=0.2,
        )


def get_aux_llm(section: str = "learning") -> AuxLLM:
    return AuxLLM(section)
```

> NOTA para el ejecutor: ajustar `get_router()/complete_sync(...)` al API real de `backend/providers/router.py`. Si el router solo expone async, exponer un wrapper `complete_sync` que use `asyncio.run`/`to_thread`. Mantener la firma `complete(system, messages, max_tokens) -> str`.

- [ ] **Step 4: Verlo pasar** — Run `tests.test_learning.AuxLLMTest -v`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/core/learning_llm.py tests/test_learning.py
git commit -m "feat(learning): cliente LLM auxiliar para tareas de fondo"
```

---

### Task 2.3: `consolidate()` real (decay + merge)

**Files:**
- Modify: `backend/core/learning.py` (`consolidate`), `backend/core/memory_ltm.py` (helper `all_entries`, `delete`/`archive` si hace falta)
- Test: `tests/test_learning.py`

- [ ] **Step 1: Test que falla**

```python
class ConsolidateTest(unittest.TestCase):
    def test_merges_near_duplicates(self):
        import tempfile, os
        from backend.core.memory_ltm import LongTermMemory, MemoryCategory
        from backend.core.learning import LearningService
        db = os.path.join(tempfile.mkdtemp(), "ltm.db")
        ltm = LongTermMemory(db_path=db)
        ltm.store("El usuario prefiere respuestas cortas", MemoryCategory.PREFERENCE)
        ltm.store("El usuario prefiere respuestas concisas y cortas", MemoryCategory.PREFERENCE)
        svc = LearningService(ltm=ltm)
        res = svc.consolidate()
        self.assertGreaterEqual(res["merged"], 1)
```

- [ ] **Step 2: Verlo fallar.**
- [ ] **Step 3:** Implementar `consolidate`: recuperar todas las entradas (`ltm.all_entries()` — añadir si falta), agrupar por similitud (≥0.95) y conservar la más importante (sumar importancia, borrar/archivar la otra); bajar importancia de entradas con `last_accessed` antiguo. Devolver `{"decayed":n,"merged":m}`.
- [ ] **Step 4: Verlo pasar.**
- [ ] **Step 5: Commit** `feat(learning): consolidate con decay e merge de memorias`.

---

### Task 2.4: Hook de reflexión en el agente + job de consolidación

**Files:**
- Modify: `backend/core/agent.py` (`process_message`, `process_message_live`), `backend/core/scheduler.py`

- [ ] **Step 1:** Al final de `process_message` (tras responder al usuario), si `config.get("learning","reflect_after_turn",default=True)`, lanzar en background:

```python
        if config.get("learning", "reflect_after_turn", default=True):
            from backend.core.learning import get_learning
            snapshot = self._memory.get_history_snapshot()  # usar el método real de memory.py
            task = asyncio.create_task(get_learning().reflect_on_turn(snapshot))
            self._register_bg_task(task)  # mantener ref viva (patrón existente)
```

Repetir el hook en `process_message_live`.

- [ ] **Step 2:** En `scheduler`, registrar un job `learning_consolidate` disparado por inactividad (`learning.consolidate_on_idle`, `learning.min_idle_minutes`) que llama `get_learning().consolidate()`. Seguir el patrón de creación de jobs ya usado en `main.py` (`scheduler.create_job(... task_type="learning_consolidate" ...)`) y añadir el handler del task_type en el dispatcher del scheduler.
- [ ] **Step 3:** Compile check (`compileall backend`). Expected exit 0.
- [ ] **Step 4: Commit** `feat(learning): reflexión tras turno + consolidación por inactividad`.

---

### Task 2.5: Config Fase 2

- [ ] Añadir a `config.default.yaml` y `.example` las secciones `auxiliary.learning/curator` y `learning.*` del §6 del spec. Commit `chore(config): aprendizaje + modelos auxiliares`.

---

# FASE 3 — Onboarding de primera ejecución

Resultado: al primer arranque, la UI lanza un wizard de pasos dirigido por backend; opcionalmente, el agente ofrece conocer al usuario al primer mensaje.

### Task 3.1: `OnboardingService` (máquina de pasos) + profile directive

**Files:**
- Create: `backend/core/onboarding.py`
- Test: `tests/test_onboarding.py`

- [ ] **Step 1: Test que falla**

```python
# tests/test_onboarding.py
import unittest
from backend.core.onboarding import OnboardingService


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.svc = OnboardingService(
            getter=lambda *k, default=None: self.saved.get(k, default),
            setter=lambda *k, value=None: self.saved.__setitem__(k[:-0] or k, value),
            key_setter=lambda v, key: self.saved.__setitem__(("key", v), key),
        )

    def test_start_returns_first_step(self):
        step = self.svc.start()
        self.assertEqual(step["id"], "welcome")
        self.assertEqual(step["status"], "running")

    def test_full_flow_to_done(self):
        self.svc.start()
        ids = []
        nxt = self.svc.answer("welcome", {"language": "es"})
        while nxt["status"] == "running":
            ids.append(nxt["id"])
            nxt = self.svc.answer(nxt["id"], self._default_answer(nxt))
        self.assertEqual(nxt["status"], "done")
        self.assertIn("provider_keys", ids + [self.svc.steps[0]["id"]])

    def _default_answer(self, step):
        return {"skip": True}

    def test_cancel(self):
        self.svc.start()
        out = self.svc.cancel()
        self.assertEqual(out["status"], "cancelled")
```

> NOTA: ajustar los mocks `getter/setter/key_setter` a la firma final de `OnboardingService` (inyección de dependencias para testear sin tocar config real). El test define el contrato: `start()`, `answer(step_id, value)`, `cancel()`, atributo `steps`, y dicts con `id`/`status`.

- [ ] **Step 2: Verlo fallar** — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `onboarding.py`** con:
  - Lista declarativa `STEPS` (ids: `welcome, provider_keys, default_model, autonomy, voice, avatar, embeddings, profile_optin, done`), cada uno con `id,type,title,help,options/validation`.
  - `OnboardingService` con DI (`getter`, `setter`, `key_setter` por defecto = `config.get/set/set_api_key`), estado de sesión (`_idx`, `_status`), métodos `start()→step`, `answer(step_id,value)→step` (valida, persiste vía setter/key_setter, avanza), `cancel()→{status:"cancelled"}`. Al llegar a `done`, `setter("onboarding","completed", value=True)`.
  - `is_first_run(getter)→bool` = `not getter("onboarding","completed", default=False)`.
  - `profile_build_mode(getter)→"ask"|"off"` y `profile_build_directive()→str` (port del texto de hermes `agent/onboarding.py`, en español/consent-gated).
  - hints `is_seen(getter, flag)` / `mark_seen(setter, flag)` sobre `onboarding.seen.<flag>`.
  - singleton `get_onboarding()`.

- [ ] **Step 4: Verlo pasar** — `tests.test_onboarding -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(onboarding): máquina de pasos del wizard + directiva profile-build`.

---

### Task 3.2: Endpoints + eventos Socket.IO del wizard

**Files:**
- Modify: `backend/api/routes.py`, `backend/api/websocket_handler.py`

- [ ] **Step 1:** REST: `GET /api/onboarding/state` (→ `is_first_run` + paso actual), `POST /api/onboarding/start`, `POST /api/onboarding/answer` (`{step_id,value}`), `POST /api/onboarding/cancel`. Cada uno delega en `get_onboarding()`.
- [ ] **Step 2:** Socket.IO: al conectar la UI, si `is_first_run`, emitir `onboarding:required`. Handlers `onboarding:start/answer/cancel` que emiten `onboarding:step` y, al final, `onboarding:done`.
- [ ] **Step 3:** Test ligero: `tests/test_onboarding.py` añade un test que importa el router y verifica que las rutas existen (o test del handler con cliente mock). Compile check.
- [ ] **Step 4: Commit** `feat(onboarding): API REST + eventos Socket.IO del wizard`.

---

### Task 3.3: Disparo first-run en `agent.initialize` + inyección de directiva al primer mensaje

**Files:**
- Modify: `backend/core/agent.py`

- [ ] **Step 1:** En `initialize()`, calcular `self._needs_onboarding = is_first_run(config.get)` y exponerlo (para que el ws handler emita `onboarding:required`).
- [ ] **Step 2:** En `process_message`, si es el **primer mensaje real** y `profile_build_mode == "ask"` y no `is_seen(...,"profile_build_offered")`: anteponer `profile_build_directive()` como nota de sistema del turno y `mark_seen(..., "profile_build_offered")`.
- [ ] **Step 3:** Compile check. Commit `feat(onboarding): first-run flag + directiva profile-build en el primer mensaje`.

---

### Task 3.4: UI Electron — renderer genérico de pasos

**Files:**
- Create: `electron/src/onboarding.html`, `electron/src/js/onboarding.js`
- Modify: `electron/src/index.html`, `electron/src/js/app.js`

- [ ] **Step 1:** `onboarding.js` escucha `onboarding:required` → muestra modal; renderiza cada `onboarding:step` genéricamente según `type` (text/select/secret/toggle/info), envía `onboarding:answer`. Al `onboarding:done`, cierra el modal y recarga settings/modelos.
- [ ] **Step 2:** Enganchar en `app.js` (suscripción a los eventos) y añadir el contenedor modal en `index.html`.
- [ ] **Step 3: Verificación manual** (documentar, no test automatizado): `start.bat`, borrar/renombrar `config.user.yaml` para forzar first-run, confirmar que aparece el wizard y que al completar queda `onboarding.completed: true`.
- [ ] **Step 4: Commit** `feat(onboarding): UI Electron del wizard (renderer genérico de pasos)`.

---

# FASE 4 — Skills propias (auto-autoría)

Resultado: el agente puede empaquetar un procedimiento repetido en una skill ejecutable, marcada `agent_created`.

### Task 4.1: Métodos de autoría/lifecycle en `SkillRegistry`

**Files:**
- Modify: `backend/core/skill_registry.py`
- Test: `tests/test_skill_curator.py` (crear archivo; primeros tests del registry)

- [ ] **Step 1: Test que falla**

```python
# tests/test_skill_curator.py
import os, tempfile, unittest
from backend.core.skill_registry import SkillRegistry


class SkillAuthoringTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.reg = SkillRegistry(agent_skills_dir=os.path.join(self.root, "_agent"))

    def test_create_marks_agent_created(self):
        d = self.reg.create_skill("auto-backup", manifest={"description": "hace backup"},
                                  files={"run.py": "print('hi')"}, origin="agent")
        self.assertTrue(d["agent_created"])
        self.assertEqual(d["lifecycle"], "active")

    def test_archive_not_delete(self):
        self.reg.create_skill("tmp", manifest={"description": "x"}, files={"run.py": "x=1"})
        self.reg.archive_skill("tmp")
        self.assertEqual(self.reg.get_descriptor("tmp")["lifecycle"], "archived")
        self.assertTrue(os.path.exists(self.reg.skill_path("tmp")))  # sigue en disco

    def test_pin_blocks_transition(self):
        self.reg.create_skill("keep", manifest={"description": "x"}, files={"run.py": "x=1"})
        self.reg.pin_skill("keep")
        self.assertTrue(self.reg.get_descriptor("keep")["pinned"])
```

- [ ] **Step 2: Verlo fallar.**
- [ ] **Step 3:** Implementar en `SkillRegistry`: `__init__` acepta `agent_skills_dir` (default `data/skills/_agent`); `create_skill(name, manifest, files, origin="agent")` escribe manifest+archivos, registra `{agent_created, lifecycle:"active", pinned:False, created_at, last_used_at}`; `update_skill`, `set_lifecycle(name,state)`, `archive_skill(name)` (mueve a estado `archived`, no borra), `pin_skill(name)`, `get_descriptor(name)`, `skill_path(name)`. Persistir metadatos en `data/skills/_agent/_meta.json` (atomic write).
- [ ] **Step 4: Verlo pasar.** **Step 5: Commit** `feat(skills): autoría y lifecycle de skills creadas por el agente`.

---

### Task 4.2: Herramienta de agente `skill_author`

**Files:**
- Modify: `backend/core/agent.py` (registro de tools), donde se definan las herramientas del agente.

- [ ] **Step 1:** Añadir tool `skill_author(name, description, files)` que llama `SkillRegistry.create_skill(..., origin="agent")`. Disponible también para el fork de reflexión (whitelist memoria+skills).
- [ ] **Step 2:** Documentar en `data/prompts/system_prompt.md` (o el prompt de modo) cuándo crear una skill (procedimiento repetido ≥2 veces, determinista).
- [ ] **Step 3:** Test: extender `tests/test_skill_curator.py` con un test que invoque el handler de la tool con args mock y verifique que se crea la skill. Compile check.
- [ ] **Step 4: Commit** `feat(skills): herramienta skill_author para auto-autoría`.

---

# FASE 5 — Curator (mantenimiento por inactividad)

Resultado: por inactividad, un proceso revisa solo skills `agent_created` y las transiciona (active→stale→archived), respetando `pinned`, **sin borrar**.

### Task 5.1: `SkillCurator.apply_automatic_transitions`

**Files:**
- Create: `backend/core/skill_curator.py`
- Test: `tests/test_skill_curator.py` (añadir clase)

- [ ] **Step 1: Test que falla**

```python
class CuratorTransitionTest(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        from backend.core.skill_registry import SkillRegistry
        self.reg = SkillRegistry(agent_skills_dir=os.path.join(tempfile.mkdtemp(), "_agent"))

    def test_stale_then_archive_and_pinned_skipped(self):
        from backend.core.skill_curator import SkillCurator
        import time
        old = time.time() - 40 * 86400  # 40 días
        self.reg.create_skill("a", manifest={"description": "x"}, files={"run.py": "x=1"})
        self.reg.set_last_used("a", old)
        self.reg.create_skill("keep", manifest={"description": "x"}, files={"run.py": "x=1"})
        self.reg.set_last_used("keep", old)
        self.reg.pin_skill("keep")
        cur = SkillCurator(registry=self.reg)
        res = cur.apply_automatic_transitions()
        self.assertEqual(self.reg.get_descriptor("a")["lifecycle"], "stale")
        self.assertEqual(self.reg.get_descriptor("keep")["lifecycle"], "active")  # pinned se salta
        self.assertGreaterEqual(res["stale"], 1)

    def test_never_deletes(self):
        from backend.core.skill_curator import SkillCurator
        import time, os
        self.reg.create_skill("old", manifest={"description": "x"}, files={"run.py": "x=1"})
        self.reg.set_last_used("old", time.time() - 200 * 86400)
        cur = SkillCurator(registry=self.reg)
        cur.apply_automatic_transitions(); cur.apply_automatic_transitions()
        self.assertTrue(os.path.exists(self.reg.skill_path("old")))  # archivada, no borrada
```

- [ ] **Step 2: Verlo fallar** (requiere `set_last_used` en registry — añadirlo si falta, mínimo).
- [ ] **Step 3: Implementar `skill_curator.py`** (port reducido de hermes/curator.py):
  - `SkillCurator(registry=None)`; `apply_automatic_transitions(now=None)`: para cada skill `agent_created` y no `pinned`, si `last_used` > `stale_after_days` → `stale`; si > `archive_after_days` → `archived`. Devuelve `{"stale":n,"archived":m}`. **Nunca borra.**
  - `should_run_now()` por `interval_hours`+`min_idle_hours` (estado en `data/curator_state.json`).
  - `run()` = `apply_automatic_transitions()` + (opcional, si `curator.consolidate`) fusión LLM con el aux (OFF por defecto).
  - singleton `get_curator()`.
- [ ] **Step 4: Verlo pasar.** **Step 5: Commit** `feat(curator): transiciones de lifecycle de skills (nunca borra, respeta pinned)`.

---

### Task 5.2: Job del curator en `scheduler` + config

**Files:**
- Modify: `backend/core/scheduler.py`, `config.default.yaml`, `config.user.yaml.example`

- [ ] **Step 1:** Registrar job `skill_curator` disparado por inactividad que llama `get_curator().run()` si `should_run_now()`. Añadir handler del task_type en el dispatcher.
- [ ] **Step 2:** Añadir sección `curator.*` del §6 del spec a la config.
- [ ] **Step 3:** Compile check. Commit `feat(curator): job por inactividad + config`.

---

## Verificación final (tras todas las fases)

- [ ] `venv/Scripts/python.exe -m compileall -q backend` → exit 0.
- [ ] `PYTHONPATH=. venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` → OK (incluye los nuevos test_embeddings/test_learning/test_onboarding/test_skill_curator + los existentes).
- [ ] Manual: forzar first-run (renombrar `config.user.yaml`) → aparece wizard → completar → `onboarding.completed: true`.
- [ ] Manual: conversación con datos del usuario → tras el turno, `data/memory_ltm.db` gana una memoria → en un mensaje posterior el agente la recuerda (recall en prompt).
- [ ] Actualizar `README.md` (sección de capacidades) con onboarding + aprendizaje continuo.

---

## Self-Review (hecho por el autor del plan)

- **Cobertura del spec:** Pilar A → Fase 3; Pilar B (B1 reflexión, B2 consolidación, B3 recall, B4 embeddings, B5 rlhf) → Fases 1–2; Pilar C → Fases 4–5. Config §6 cubierta en Tasks 1.5/2.5/3.x/5.2. Almacenamiento §5 cubierto (memory_ltm + _agent + curator_state). ✔
- **rlhf (B5):** el plan crea el cableado de memoria; las señales rlhf se registran vía `rlhf_lite.record_signal()` ya existente desde la UI; integrarlas en `consolidate` (Task 2.3) — anotado. Sin tarea de UI nueva (YAGNI; ya hay endpoints).
- **Placeholders:** los `NOTA para el ejecutor` marcan puntos donde adaptar al API real del router/memory/scheduler (no son TODOs de diseño, sino de integración con código existente que el ejecutor debe leer). Aceptable y explícito.
- **Consistencia de tipos:** `EmbeddingProvider.model_id/dim/embed`, `MemoryCategory`, `LearningService.reflect_on_turn_sync/reflect_on_turn/consolidate`, `OnboardingService.start/answer/cancel/steps`, `SkillRegistry.create_skill/archive_skill/pin_skill/get_descriptor/skill_path/set_last_used`, `SkillCurator.apply_automatic_transitions/run/should_run_now` — usados consistentes entre tareas. ✔
```
