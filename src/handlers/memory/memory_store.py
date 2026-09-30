"""Persistent storage, decay model and per-scope RAG indexes for long term memory."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid

from ..rag.rag_handler import RAGRecord

KIND_FACT = "fact"
KIND_CONVERSATION = "conversation"
KINDS = (KIND_FACT, KIND_CONVERSATION)
SHARED_SCOPE = "shared"
DAY = 86400.0


def profile_scope(profile: str) -> str:
    return "profile:" + profile


class MemoryRecord:
    __slots__ = ("id", "kind", "scope", "text", "created_at", "updated_at", "last_reinforced",
                 "access_count", "importance", "pinned", "archived", "source", "revision")

    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.kind = row["kind"]
        self.scope = row["scope"]
        self.text = row["text"]
        self.created_at = row["created_at"]
        self.updated_at = row["updated_at"]
        self.last_reinforced = row["last_reinforced"]
        self.access_count = row["access_count"]
        self.importance = row["importance"]
        self.pinned = bool(row["pinned"])
        self.archived = bool(row["archived"])
        self.source = row["source"]
        self.revision = row["revision"]

    @property
    def doc_id(self) -> str:
        return f"{self.id}:{self.revision}"


def effective_half_life(base_days: float, importance: float, access_count: int) -> float:
    """Half-life grows with importance and with every recall (spaced repetition)."""
    importance = max(0.0, min(1.0, float(importance)))
    return max(0.1, float(base_days)) * (0.5 + importance) * (1.3 ** min(int(access_count), 6))


def memory_strength(record: MemoryRecord, base_days: float, now: float | None = None) -> float:
    """Exponential decay since the memory was last created or recalled (1 = fresh)."""
    if record.pinned:
        return 1.0
    now = time.time() if now is None else now
    days = max(0.0, (now - record.last_reinforced) / DAY)
    return 0.5 ** (days / effective_half_life(base_days, record.importance, record.access_count))


class MemoryStore:
    """Thread-safe SQLite store of memories and per-scope summaries"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(os.path.join(root, "memory.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("""CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                scope TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_reinforced REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                importance REAL NOT NULL DEFAULT 0.5,
                pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'auto',
                revision INTEGER NOT NULL DEFAULT 0)""")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope_kind ON memories(scope, kind)")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS summaries (
                scope TEXT PRIMARY KEY,
                text TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0,
                msgs_since_extract INTEGER NOT NULL DEFAULT 0)""")

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()):
        with self._lock, self._conn:
            self._conn.execute(sql, params)

    # Memories
    def add(self, kind: str, scope: str, text: str, importance: float = 0.5,
            pinned: bool = False, source: str = "auto") -> MemoryRecord:
        now = time.time()
        memory_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO memories (id, kind, scope, text, created_at, updated_at, last_reinforced, "
            "importance, pinned, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (memory_id, kind, scope, text.strip(), now, now, now,
             max(0.0, min(1.0, float(importance))), int(bool(pinned)), source))
        return self.get(memory_id)

    def get(self, memory_id: str) -> MemoryRecord | None:
        rows = self._query("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return MemoryRecord(rows[0]) if rows else None

    def get_many(self, memory_ids: list[str]) -> dict[str, MemoryRecord]:
        if not memory_ids:
            return {}
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self._query(f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(memory_ids))
        return {row["id"]: MemoryRecord(row) for row in rows}

    def list_records(self, scope: str, kind: str | None = None, archived: bool | None = False,
             limit: int | None = None) -> list[MemoryRecord]:
        sql = "SELECT * FROM memories WHERE scope = ?"
        params: list = [scope]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if archived is not None:
            sql += " AND archived = ?"
            params.append(int(archived))
        sql += " ORDER BY pinned DESC, updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [MemoryRecord(row) for row in self._query(sql, tuple(params))]

    def list_all_active(self) -> list[MemoryRecord]:
        return [MemoryRecord(row) for row in self._query("SELECT * FROM memories WHERE archived = 0 AND pinned = 0")]

    def update_text(self, memory_id: str, text: str) -> MemoryRecord | None:
        now = time.time()
        self._execute("UPDATE memories SET text = ?, updated_at = ?, last_reinforced = ?, revision = revision + 1 WHERE id = ?",
                      (text.strip(), now, now, memory_id))
        return self.get(memory_id)

    def update_importance(self, memory_id: str, importance: float):
        self._execute("UPDATE memories SET importance = ? WHERE id = ?",
                      (max(0.0, min(1.0, float(importance))), memory_id))

    def set_pinned(self, memory_id: str, pinned: bool):
        self._execute("UPDATE memories SET pinned = ? WHERE id = ?", (int(bool(pinned)), memory_id))

    def set_archived(self, memory_id: str, archived: bool):
        if archived:
            self._execute("UPDATE memories SET archived = 1 WHERE id = ?", (memory_id,))
        else:
            # A restored memory starts fresh, otherwise it would be archived again right away
            self._execute("UPDATE memories SET archived = 0, last_reinforced = ? WHERE id = ?", (time.time(), memory_id))

    def archive_many(self, memory_ids: list[str]):
        with self._lock, self._conn:
            self._conn.executemany("UPDATE memories SET archived = 1 WHERE id = ?", [(i,) for i in memory_ids])

    def delete(self, memory_id: str):
        self._execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def reinforce(self, memory_ids: list[str]):
        if not memory_ids:
            return
        now = time.time()
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE memories SET last_reinforced = ?, access_count = access_count + 1 WHERE id = ?",
                [(now, i) for i in memory_ids])

    def counts(self, scope: str) -> dict[str, dict[str, int]]:
        result = {kind: {"active": 0, "archived": 0} for kind in KINDS}
        rows = self._query("SELECT kind, archived, COUNT(*) AS n FROM memories WHERE scope = ? GROUP BY kind, archived", (scope,))
        for row in rows:
            if row["kind"] in result:
                result[row["kind"]]["archived" if row["archived"] else "active"] = row["n"]
        return result

    # Summaries
    def get_summary(self, scope: str) -> tuple[str, float]:
        rows = self._query("SELECT text, updated_at FROM summaries WHERE scope = ?", (scope,))
        return (rows[0]["text"], rows[0]["updated_at"]) if rows else ("", 0.0)

    def set_summary(self, scope: str, text: str):
        self._execute(
            "INSERT INTO summaries (scope, text, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (scope, text.strip(), time.time()))

    def increment_messages(self, scope: str) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO summaries (scope, msgs_since_extract) VALUES (?, 1) "
                "ON CONFLICT(scope) DO UPDATE SET msgs_since_extract = msgs_since_extract + 1", (scope,))
            row = self._conn.execute("SELECT msgs_since_extract FROM summaries WHERE scope = ?", (scope,)).fetchone()
        return row["msgs_since_extract"] if row else 0

    def reset_messages(self, scope: str):
        self._execute("UPDATE summaries SET msgs_since_extract = 0 WHERE scope = ?", (scope,))

    def delete_scope(self, scope: str):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM memories WHERE scope = ?", (scope,))
            self._conn.execute("DELETE FROM summaries WHERE scope = ?", (scope,))


class MemoryIndexManager:
    """Keeps one RAG record index per (scope, kind), persisted on disk.

    Index document ids are "<memory id>:<revision>". Deleted or edited revisions
    stay in the index (FAISS cannot delete) and are filtered out at query time;
    the index is rebuilt once too many of them accumulate.
    """
    STALE_RATIO = 0.2
    STALE_MIN = 10
    PERSIST_DELAY = 3.0

    def __init__(self, store: MemoryStore, root: str):
        self.store = store
        self.root = root
        self.rag = None
        self.embedding = None
        self.signature = None
        self._indexes = {}
        self._loading: dict[tuple, threading.Thread] = {}
        self._stale: dict[tuple, int] = {}
        self._timers: dict[tuple, threading.Timer] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _compute_signature(rag, embedding) -> str | None:
        if rag is None or embedding is None:
            return None
        try:
            embedding_settings = json.dumps(embedding.get_all_settings(), sort_keys=True, default=str)
        except Exception:
            embedding_settings = ""
        raw = f"{type(rag).__name__}:{rag.key}|{type(embedding).__name__}:{embedding.key}|{embedding_settings}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def configure(self, rag, embedding) -> bool:
        """Set the RAG and embedding handlers. Returns True if loaded indexes were dropped."""
        signature = self._compute_signature(rag, embedding)
        with self._lock:
            changed = signature != self.signature or rag is not self.rag or embedding is not self.embedding
            self.rag, self.embedding, self.signature = rag, embedding, signature
            if changed:
                self._indexes.clear()
                self._stale.clear()
        return changed

    def _dir(self, key: tuple) -> str:
        scope, kind = key
        readable = re.sub(r"[^A-Za-z0-9_-]", "_", scope)[:40]
        digest = hashlib.sha1(scope.encode()).hexdigest()[:8]
        return os.path.join(self.root, f"{readable}-{digest}", kind)

    def is_loaded(self, scope: str, kind: str) -> bool:
        with self._lock:
            return (scope, kind) in self._indexes

    def get_index(self, scope: str, kind: str, wait: bool):
        key = (scope, kind)
        with self._lock:
            index = self._indexes.get(key)
            if index is not None:
                return index
            if self.rag is None or self.embedding is None:
                return None
            thread = self._loading.get(key)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=self._load, args=(key,), daemon=True)
                self._loading[key] = thread
                thread.start()
        if not wait:
            return None
        thread.join()
        with self._lock:
            return self._indexes.get(key)

    def _live_records(self, key: tuple) -> dict[str, MemoryRecord]:
        scope, kind = key
        return {record.doc_id: record for record in self.store.list_records(scope, kind, archived=None)}

    def _load(self, key: tuple):
        with self._lock:
            rag, embedding, signature = self.rag, self.embedding, self.signature
        if rag is None or embedding is None:
            return
        path = self._dir(key)
        try:
            index = None
            if self._read_signature(path) == signature:
                try:
                    index = rag.load_record_index(path, embedding)
                except Exception as e:
                    print(f"Could not load memory index {key}, rebuilding: {e}")
            live = self._live_records(key)
            if index is not None:
                indexed = set(index.get_record_ids())
                stale = len(indexed - live.keys())
                if stale >= self.STALE_MIN and stale > self.STALE_RATIO * max(len(indexed), 1):
                    index = None
                else:
                    missing = [RAGRecord(doc_id, record.text) for doc_id, record in live.items() if doc_id not in indexed]
                    if missing:
                        index.insert_records(missing)
                        self._persist(index, path, signature)
                    with self._lock:
                        self._stale[key] = stale
            if index is None:
                index = rag.build_record_index([RAGRecord(doc_id, record.text) for doc_id, record in live.items()], embedding)
                self._persist(index, path, signature)
                with self._lock:
                    self._stale[key] = 0
            with self._lock:
                if self.signature == signature:
                    self._indexes[key] = index
        except Exception as e:
            print(f"Error loading memory index {key}: {e}")

    @staticmethod
    def _read_signature(path: str) -> str | None:
        try:
            with open(os.path.join(path, "signature.json")) as f:
                return json.load(f).get("signature")
        except Exception:
            return None

    @staticmethod
    def _persist(index, path: str, signature: str):
        try:
            os.makedirs(path, exist_ok=True)
            index.persist(path)
            with open(os.path.join(path, "signature.json"), "w") as f:
                json.dump({"signature": signature}, f)
        except Exception as e:
            print(f"Error persisting memory index: {e}")

    def _schedule_persist(self, key: tuple):
        def persist():
            with self._lock:
                index, signature = self._indexes.get(key), self.signature
                self._timers.pop(key, None)
            if index is not None:
                self._persist(index, self._dir(key), signature)
        with self._lock:
            timer = self._timers.pop(key, None)
            if timer is not None:
                timer.cancel()
            timer = threading.Timer(self.PERSIST_DELAY, persist)
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def add_record(self, record: MemoryRecord):
        """Index a new record or a new revision. Unloaded indexes catch up when loaded."""
        key = (record.scope, record.kind)
        with self._lock:
            index = self._indexes.get(key)
        if index is None:
            return
        try:
            index.insert_records([RAGRecord(record.doc_id, record.text)])
            self._schedule_persist(key)
        except Exception as e:
            print(f"Error indexing memory: {e}")

    def mark_stale(self, scope: str, kind: str):
        key = (scope, kind)
        with self._lock:
            index = self._indexes.get(key)
            if index is None:
                return
            self._stale[key] = self._stale.get(key, 0) + 1
            stale = self._stale[key]
            size = index.get_index_size() or 1
        if stale >= self.STALE_MIN and stale > self.STALE_RATIO * size:
            self.rebuild(scope, kind)

    def rebuild(self, scope: str | None = None, kind: str | None = None):
        """Drop the persisted indexes (all, a scope, or one kind of a scope) and reload them in background"""
        with self._lock:
            keys = [(scope, k) for k in (KINDS if kind is None else (kind,))] if scope is not None else list(self._indexes.keys())
            for key in keys:
                self._indexes.pop(key, None)
                self._stale.pop(key, None)
                timer = self._timers.pop(key, None)
                if timer is not None:
                    timer.cancel()
        if scope is None:
            shutil.rmtree(self.root, ignore_errors=True)
        else:
            for key in keys:
                shutil.rmtree(self._dir(key), ignore_errors=True)

        def reload():
            for key in keys:
                with self._lock:
                    thread = self._loading.get(key)
                if thread is not None and thread.is_alive():
                    thread.join()
                self.get_index(key[0], key[1], wait=True)
        threading.Thread(target=reload, daemon=True).start()

    def forget_scope(self, scope: str):
        with self._lock:
            for kind in KINDS:
                self._indexes.pop((scope, kind), None)
                self._stale.pop((scope, kind), None)
        for kind in KINDS:
            shutil.rmtree(self._dir((scope, kind)), ignore_errors=True)

    def query(self, scope: str, kind: str, text: str, top_k: int, wait: bool) -> list[tuple[MemoryRecord, float, float | None]]:
        """Return (record, relevance, cosine similarity) for live records matching the query"""
        index = self.get_index(scope, kind, wait)
        if index is None or not text.strip():
            return []
        results = index.query_scored(text, max(1, top_k))
        parsed = []
        for position, result in enumerate(results):
            memory_id, _sep, revision = result.id.rpartition(":")
            if not memory_id:
                continue
            relevance = result.relevance
            if relevance is None:
                # Rank-only backends: spread relevance from 1 to ~0.5
                relevance = 1.0 - 0.5 * position / max(len(results), 1)
            parsed.append((memory_id, revision, relevance, result.similarity))
        records = self.store.get_many([memory_id for memory_id, *_rest in parsed])
        output = []
        for memory_id, revision, relevance, similarity in parsed:
            record = records.get(memory_id)
            if record is None or str(record.revision) != revision or record.scope != scope:
                continue
            output.append((record, float(relevance), similarity))
        return output
