"""
Long-Term Memory with semantic search (RAG).

Stores facts, preferences, task history, and learnings as embeddings.
Uses SQLite + numpy: each embedding model keeps its own in-memory matrix so a
search is a single matrix product, and vectors from different models are
never compared with each other.

Every method here may block (SQLite, HTTP embeddings): call it from a thread.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from loguru import logger

from backend.config import config


class MemoryCategory(str, Enum):
    FACT = "fact"              # User facts: "Mi empresa se llama Acme"
    PREFERENCE = "preference"  # Preferences: "Prefiero respuestas concisas"
    TASK = "task"              # Task history: "Desplegamos v2.3 el martes"
    LEARNING = "learning"      # Agent learnings: "El usuario prefiere Python sobre JS"
    ENTITY = "entity"          # Named entities: persons, companies, projects
    RELATIONSHIP = "relationship"  # Extracted from KG
    SKILL = "skill_memory"    # Skill execution patterns


PROFILE_CATEGORIES = ("fact", "preference", "entity")
STATUS_ACTIVE = "active"
STATUS_MERGED = "merged"


@dataclass
class MemoryEntry:
    memory_id: str
    category: str
    content: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5       # 0.0 - 1.0
    access_count: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    expires_at: float | None = None


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class LongTermMemory:
    """Persistent memory with semantic search capabilities."""

    _NEW_COLUMNS = (
        ("embedding_model", "TEXT"),
        ("embedding_dim", "INTEGER"),
        ("base_importance", "REAL"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("merged_into", "TEXT"),
    )

    def __init__(self, db_path: str | None = None) -> None:
        db_path = db_path or config.get("memory", "db_path") or "data/memory_ltm.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_memories = int(config.get("memory", "max_memories") or 5000)
        self._write_lock = threading.RLock()
        self._index_lock = threading.Lock()
        self._version = 0
        self._index: dict[str, tuple[int, list[str], np.ndarray]] = {}
        self._init_db()

    # ── DB ───────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    memory_id     TEXT PRIMARY KEY,
                    category      TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    embedding     BLOB,
                    metadata      TEXT NOT NULL DEFAULT '{}',
                    importance    REAL NOT NULL DEFAULT 0.5,
                    access_count  INTEGER NOT NULL DEFAULT 0,
                    created_at    REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    expires_at    REAL
                )
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(long_term_memory)")}
            for column, ddl in self._NEW_COLUMNS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE long_term_memory ADD COLUMN {column} {ddl}")
            conn.execute("UPDATE long_term_memory SET base_importance = importance WHERE base_importance IS NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_category ON long_term_memory(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_importance ON long_term_memory(importance DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_model ON long_term_memory(embedding_model, status)")
            conn.commit()

    def _changed(self) -> None:
        with self._index_lock:
            self._version += 1

    # ── Embedding ────────────────────────────────────────────────────

    @staticmethod
    def _embedder():
        from backend.core.embeddings import get_embedder

        return get_embedder()

    def _get_embedding(self, text: str) -> list[float]:
        return self._embedder().embed(text)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        a_arr = np.array(a, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))

    def _matrix_for(self, model: str) -> tuple[list[str], np.ndarray]:
        """Matriz normalizada de las memorias activas de un modelo (cacheada)."""
        with self._index_lock:
            version = self._version
            cached = self._index.get(model)
            if cached and cached[0] == version:
                return cached[1], cached[2]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, embedding FROM long_term_memory "
                "WHERE embedding_model = ? AND status = ? AND embedding IS NOT NULL",
                (model, STATUS_ACTIVE),
            ).fetchall()
        ids: list[str] = []
        vectors: list[np.ndarray] = []
        width = None
        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if width is None:
                width = vec.shape[0]
            if vec.shape[0] != width:
                continue
            ids.append(row["memory_id"])
            vectors.append(vec)
        matrix = _normalize(np.vstack(vectors)) if vectors else np.zeros((0, 0), dtype=np.float32)
        with self._index_lock:
            if self._version == version:
                self._index[model] = (version, ids, matrix)
        return ids, matrix

    # ── CRUD ─────────────────────────────────────────────────────────

    def store(
        self,
        content: str,
        category: str = "fact",
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        expires_at: float | None = None,
        *,
        embedding=None,
    ) -> str:
        """Guarda una memoria con su embedding (y el modelo que lo produjo)."""
        category = category.value if isinstance(category, MemoryCategory) else str(category)
        importance = max(0.0, min(1.0, float(importance)))
        memory_id = hashlib.sha256(f"{content}:{category}:{time.time_ns()}".encode()).hexdigest()[:16]
        emb = embedding or self._embedder().embed_text(content, task="document")
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO long_term_memory
                (memory_id, category, content, embedding, metadata, importance,
                 access_count, created_at, last_accessed, expires_at,
                 embedding_model, embedding_dim, base_importance, status)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id, category, content,
                    np.array(emb.vector, dtype=np.float32).tobytes(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    importance, now, now, expires_at,
                    emb.model, emb.dim, importance, STATUS_ACTIVE,
                ),
            )
            conn.commit()
        self._changed()
        logger.debug(f"LTM stored: [{category}] {content[:80]}")
        self._evict_if_needed()
        return memory_id

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
        min_similarity: float | None = None,
        *,
        touch: bool = True,
    ) -> list[dict]:
        """Búsqueda semántica. `touch=False` para consultas internas (no altera recencia)."""
        if not (query or "").strip():
            return []
        q = self._embedder().embed_text(query, task="query")
        return self.search_vector(q, top_k, category, min_similarity, touch=touch)

    def search_vector(
        self,
        q,
        top_k: int = 5,
        category: str | None = None,
        min_similarity: float | None = None,
        *,
        touch: bool = True,
    ) -> list[dict]:
        """Como `search`, con un embedding ya calculado (solo compara su mismo modelo)."""
        from backend.core.embeddings import recall_floor

        ids, matrix = self._matrix_for(q.model)
        if not ids:
            return []
        qv = np.asarray(q.vector, dtype=np.float32)
        if matrix.shape[1] != qv.shape[0] or not np.any(qv):
            return []
        sims = matrix @ (qv / np.linalg.norm(qv))
        floor = recall_floor(q.model) if min_similarity is None else float(min_similarity)
        candidates = np.where(sims >= floor)[0]
        if candidates.size == 0:
            return []
        ordered = candidates[np.argsort(-sims[candidates])][: max(top_k * 5, 20)]
        sim_by_id = {ids[i]: float(sims[i]) for i in ordered}

        placeholders = ",".join("?" for _ in sim_by_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT memory_id, category, content, importance, access_count, last_accessed, metadata, expires_at "
                f"FROM long_term_memory WHERE memory_id IN ({placeholders})",
                list(sim_by_id),
            ).fetchall()

        now = time.time()
        scored = []
        for row in rows:
            if row["expires_at"] and row["expires_at"] < now:
                continue
            if category and row["category"] != category:
                continue
            sim = sim_by_id[row["memory_id"]]
            recency = max(0.0, 1.0 - (now - row["last_accessed"]) / (86400 * 30))
            scored.append({
                "memory_id": row["memory_id"],
                "category": row["category"],
                "content": row["content"],
                "similarity": round(sim, 4),
                "score": round(sim * 0.7 + row["importance"] * 0.2 + recency * 0.1, 4),
                "importance": row["importance"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "access_count": row["access_count"],
            })
        scored.sort(key=lambda item: item["score"], reverse=True)
        results = scored[:top_k]
        if touch and results:
            self.touch([r["memory_id"] for r in results])
        return results

    def touch(self, memory_ids: Iterable[str]) -> None:
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.executemany(
                "UPDATE long_term_memory SET access_count = access_count + 1, last_accessed = ? WHERE memory_id = ?",
                [(now, mid) for mid in memory_ids],
            )
            conn.commit()

    def top_memories(
        self,
        categories: Iterable[str] = PROFILE_CATEGORIES,
        limit: int = 12,
    ) -> list[dict]:
        """Las memorias más importantes (sin embeddings: no hace llamadas de red)."""
        cats = list(categories)
        placeholders = ",".join("?" for _ in cats)
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT memory_id, category, content, importance FROM long_term_memory "
                f"WHERE status = ? AND category IN ({placeholders}) AND (expires_at IS NULL OR expires_at > ?) "
                f"ORDER BY importance DESC, last_accessed DESC LIMIT ?",
                [STATUS_ACTIVE, *cats, now, int(limit)],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_context_injection(self, query: str, max_tokens: int = 500) -> str:
        """Get relevant memories formatted for system prompt injection."""
        results = self.search(query, top_k=10)
        if not results:
            return ""
        lines = ["[Memoria relevante del usuario]"]
        char_budget = max_tokens * 4
        used = len(lines[0])
        for r in results:
            line = f"- [{r['category']}] {r['content']}"
            if used + len(line) > char_budget:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def list_memories(
        self,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_merged: bool = False,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if not include_merged:
            clauses.append("status = ?")
            params.append(STATUS_ACTIVE)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT memory_id, category, content, importance, access_count, created_at, last_accessed, "
                f"metadata, embedding_model, status, merged_into "
                f"FROM long_term_memory {where} ORDER BY importance DESC, last_accessed DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            return [dict(r) for r in rows]

    def find_same_text(self, text: str) -> dict | None:
        """Memoria activa con las mismas palabras (sin embeddings: sirve aunque cambie el modelo)."""
        from backend.core.embeddings import lexical_tokens

        key = lexical_tokens(text)
        if not key:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, category, content, importance FROM long_term_memory WHERE status = ?",
                (STATUS_ACTIVE,),
            ).fetchall()
        for row in rows:
            if lexical_tokens(row["content"]) == key:
                return dict(row)
        return None

    def get(self, memory_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM long_term_memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def delete(self, memory_id: str) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM long_term_memory WHERE memory_id = ?", (memory_id,))
            conn.commit()
        self._changed()
        return cur.rowcount > 0

    def delete_all(self) -> int:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM long_term_memory")
            conn.commit()
        self._changed()
        return cur.rowcount

    def update_importance(self, memory_id: str, importance: float, *, rebase: bool = True) -> bool:
        """Cambia la importancia. `rebase=True` la fija también como base del decay."""
        value = max(0.0, min(1.0, float(importance)))
        sql = (
            "UPDATE long_term_memory SET importance = ?, base_importance = ? WHERE memory_id = ?"
            if rebase else "UPDATE long_term_memory SET importance = ? WHERE memory_id = ?"
        )
        params = (value, value, memory_id) if rebase else (value, memory_id)
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
        return cur.rowcount > 0

    def mark_merged(self, memory_id: str, into_id: str) -> bool:
        """Oculta una memoria duplicada sin borrarla."""
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE long_term_memory SET status = ?, merged_into = ? WHERE memory_id = ?",
                (STATUS_MERGED, into_id, memory_id),
            )
            conn.commit()
        self._changed()
        return cur.rowcount > 0

    def stale_embeddings(self, model: str, limit: int = 100) -> list[dict]:
        """Memorias activas cuyo vector no es del modelo activo (o no tienen)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, content FROM long_term_memory "
                "WHERE status = ? AND (embedding_model IS NULL OR embedding_model != ?) LIMIT ?",
                (STATUS_ACTIVE, model, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_embedding(self, memory_id: str, embedding) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE long_term_memory SET embedding = ?, embedding_model = ?, embedding_dim = ? WHERE memory_id = ?",
                (np.array(embedding.vector, dtype=np.float32).tobytes(), embedding.model, embedding.dim, memory_id),
            )
            conn.commit()
        self._changed()

    def count(self, *, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM long_term_memory"
        params: tuple = ()
        if active_only:
            sql += " WHERE status = ?"
            params = (STATUS_ACTIVE,)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else 0

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            by_model = conn.execute(
                "SELECT COALESCE(embedding_model, '') AS model, COUNT(*) AS n FROM long_term_memory "
                "WHERE status = ? GROUP BY embedding_model",
                (STATUS_ACTIVE,),
            ).fetchall()
            merged = conn.execute(
                "SELECT COUNT(*) FROM long_term_memory WHERE status = ?", (STATUS_MERGED,)
            ).fetchone()[0]
        return {
            "active": sum(r["n"] for r in by_model),
            "merged": merged,
            "by_model": {r["model"] or "sin modelo": r["n"] for r in by_model},
        }

    def _evict_if_needed(self) -> None:
        """Si se supera el máximo, primero salen las fusionadas y luego las menos importantes."""
        count = self.count()
        if count <= self._max_memories:
            return
        to_remove = count - self._max_memories + 100
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM long_term_memory WHERE memory_id IN "
                "(SELECT memory_id FROM long_term_memory "
                " ORDER BY (status = 'active') ASC, importance ASC, last_accessed ASC LIMIT ?)",
                (to_remove,),
            )
            conn.commit()
        self._changed()
        logger.info(f"LTM evicted {to_remove} low-importance memories")

    def all_entries(self, *, active_only: bool = True) -> list[dict]:
        sql = (
            "SELECT memory_id, category, content, embedding, embedding_model, importance, base_importance, "
            "access_count, created_at, last_accessed, metadata, status FROM long_term_memory"
        )
        params: tuple = ()
        if active_only:
            sql += " WHERE status = ?"
            params = (STATUS_ACTIVE,)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM long_term_memory WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()
        if cur.rowcount:
            self._changed()
        return cur.rowcount


# ── Singleton ────────────────────────────────────────────────────────────

_ltm: LongTermMemory | None = None
_ltm_lock = threading.Lock()


def get_ltm() -> LongTermMemory:
    global _ltm
    with _ltm_lock:
        if _ltm is None:
            _ltm = LongTermMemory()
        return _ltm
