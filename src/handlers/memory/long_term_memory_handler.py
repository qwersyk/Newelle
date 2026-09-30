import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

from .memory_handler import MemoryHandler
from .memory_store import (
    KIND_CONVERSATION,
    KIND_FACT,
    KINDS,
    SHARED_SCOPE,
    MemoryIndexManager,
    MemoryRecord,
    MemoryStore,
    memory_strength,
    profile_scope,
)
from ...handlers import ExtraSettings
from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm.llm import LLMHandler
from ...handlers.rag.rag_handler import RAGHandler
from ...tools import create_io_tool
from ...utility.strings import clean_prompt, remove_thinking_blocks

DEFAULTS = {
    "retrieval_mode": "auto",
    "query_context_messages": 2,
    "include_summary": True,
    "facts_max_count": 5,
    "facts_min_similarity": 0.35,
    "fact_half_life": 120,
    "store_conversations": True,
    "conversations_max_count": 2,
    "conversations_min_similarity": 0.5,
    "conversation_half_life": 30,
    "extract_every": 6,
    "summary_max_words": 200,
    "decay_influence": 0.6,
    "archive_threshold": 0.03,
    "reinforce_on_recall": True,
}

DEFAULT_EXTRACTION_PROMPT = """You maintain the long-term memory of an AI assistant about its user.

Current summary of the user:
{summary}

Already known facts that may be related:
{known_facts}

Recent conversation:
{conversation}

Tasks:
1. Rewrite the summary of the user so it stays short (at most {max_words} words). Keep stable, useful information: identity, preferences, goals, ongoing projects, communication style. Drop outdated or trivial details. If there is nothing to change, return the current summary.
2. Extract new durable facts about the user or their work from the recent conversation that will be useful in future conversations. Each fact must be one self-contained sentence and must not repeat a known fact. Rate importance from 0 (trivial) to 1 (essential). Ignore small talk, temporary requests and facts about the assistant.

Answer ONLY with JSON in this format:
{"summary": "...", "facts": [{"text": "...", "importance": 0.5}]}"""

REGENERATE_SUMMARY_PROMPT = """Write a short summary (at most {max_words} words) of what an AI assistant knows about its user, using the current summary and the saved facts below. Keep stable, useful information: identity, preferences, goals, ongoing projects, communication style. Output only the summary.

Current summary:
{summary}

Saved facts:
{facts}"""

DUPLICATE_SIMILARITY = 0.92
MAX_CONVERSATION_CHARS = 2000
MAX_EXTRACTION_MESSAGE_CHARS = 1500
MAX_EXTRACTION_CHARS = 12000


class LongTermMemoryHandler(MemoryHandler):
    key = "long_term_memory"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.root = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "long_term_memory")
        self.store = MemoryStore(self.root)
        self.indexes = MemoryIndexManager(self.store, os.path.join(self.root, "index"))
        self.llm: Optional[LLMHandler] = None
        self.embedding: Optional[EmbeddingHandler] = None
        self.rag: Optional[RAGHandler] = None
        self._extracting: set[str] = set()
        self._extract_lock = threading.Lock()
        self._listeners = []

    # Setup
    def set_handlers(self, llm: LLMHandler, embedding: EmbeddingHandler, rag: Optional[RAGHandler] = None):
        self.llm = llm
        self.embedding = embedding
        self.rag = rag
        self.indexes.configure(rag, embedding)

        def warm_up():
            self.run_maintenance()
            if self.settings.get_boolean("memory-on"):
                scope = self.get_scope()
                for kind in KINDS:
                    self.indexes.get_index(scope, kind, wait=False)
        threading.Thread(target=warm_up, daemon=True).start()

    def is_installed(self) -> bool:
        # The RAG handler installs its own dependencies
        return True

    def install(self):
        self._is_installed_cache = None

    def _get(self, key: str):
        return self.get_setting(key, False, DEFAULTS.get(key))

    def _get_int(self, key: str) -> int:
        try:
            return int(float(self._get(key)))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def _get_float(self, key: str) -> float:
        try:
            return float(self._get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    # Scope
    def get_current_profile(self) -> str:
        try:
            return self.settings.get_string("current-profile") or "Assistant"
        except Exception:
            return "Assistant"

    @staticmethod
    def _private_key(profile: str) -> str:
        return "private_profile::" + profile

    def is_private(self, profile: str | None = None) -> bool:
        profile = profile or self.get_current_profile()
        return bool(self.get_setting(self._private_key(profile), False, False))

    def get_scope(self) -> str:
        profile = self.get_current_profile()
        return profile_scope(profile) if self.is_private(profile) else SHARED_SCOPE

    def get_scope_label(self, scope: str | None = None) -> str:
        scope = scope or self.get_scope()
        if scope == SHARED_SCOPE:
            return _("Shared memory")
        return _("Private memory of {profile}").format(profile=scope.removeprefix("profile:"))

    # Settings
    def get_extra_settings(self) -> list:
        profile = self.get_current_profile()
        return [
            ExtraSettings.ToggleSetting(
                self._private_key(profile), _("Keep this memory private for this profile"),
                _("The profile \"{profile}\" uses its own memories and summary, isolated from the shared memory used by other profiles").format(profile=profile),
                False, update_settings=True),
            ExtraSettings.ComboSetting(
                "retrieval_mode", _("Automatic Retrieval"),
                _("When to search old memories and add them to the prompt. The user summary is not affected"),
                {_("On every message"): "auto", _("Only when the assistant uses the recall tool"): "manual"},
                DEFAULTS["retrieval_mode"]),
            ExtraSettings.SpinSetting(
                "query_context_messages", _("Context Messages"),
                _("Number of previous messages also used to search memories"),
                DEFAULTS["query_context_messages"], 0, 10),
            ExtraSettings.ToggleSetting(
                "include_summary", _("Include User Summary"),
                _("Add the short summary of the user to every prompt"), DEFAULTS["include_summary"]),
            ExtraSettings.NestedSetting("facts", _("Facts"), _("Durable facts about the user, extracted automatically or saved by the assistant"), [
                ExtraSettings.SpinSetting("facts_max_count", _("Facts to Recall"),
                    _("Maximum number of facts added to the prompt (0 = never add facts automatically)"),
                    DEFAULTS["facts_max_count"], 0, 20),
                ExtraSettings.ScaleSetting("facts_min_similarity", _("Facts Minimum Relevance"),
                    _("Minimum relevance, after decay, for a fact to be recalled (0 = no threshold)"),
                    DEFAULTS["facts_min_similarity"], 0.0, 1.0, 2),
                ExtraSettings.SpinSetting("fact_half_life", _("Facts Half-life (days)"),
                    _("Days after which an unused fact of average importance loses half of its strength"),
                    DEFAULTS["fact_half_life"], 1, 3650),
            ]),
            ExtraSettings.NestedSetting("conversations", _("Conversations"), _("Past user/assistant exchanges"), [
                ExtraSettings.ToggleSetting("store_conversations", _("Store Conversations"),
                    _("Save every exchange so it can be recalled later"), DEFAULTS["store_conversations"]),
                ExtraSettings.SpinSetting("conversations_max_count", _("Conversations to Recall"),
                    _("Maximum number of past exchanges added to the prompt (0 = never add them automatically)"),
                    DEFAULTS["conversations_max_count"], 0, 10),
                ExtraSettings.ScaleSetting("conversations_min_similarity", _("Conversations Minimum Relevance"),
                    _("Minimum relevance, after decay, for an exchange to be recalled (0 = no threshold)"),
                    DEFAULTS["conversations_min_similarity"], 0.0, 1.0, 2),
                ExtraSettings.SpinSetting("conversation_half_life", _("Conversations Half-life (days)"),
                    _("Days after which an unused exchange loses half of its strength"),
                    DEFAULTS["conversation_half_life"], 1, 3650),
            ]),
            ExtraSettings.NestedSetting("extraction", _("Summary and Extraction"), _("Periodic update of the user summary and of the facts"), [
                ExtraSettings.SpinSetting("extract_every", _("Update Every"),
                    _("Number of messages between summary updates and fact extractions (0 = disabled)"),
                    DEFAULTS["extract_every"], 0, 100),
                ExtraSettings.SpinSetting("summary_max_words", _("Summary Length"),
                    _("Maximum number of words of the user summary"), DEFAULTS["summary_max_words"], 50, 1000, 10),
                ExtraSettings.MultilineEntrySetting("extraction_prompt", _("Extraction Prompt"),
                    _("Prompt used to update the summary and extract facts. {summary}, {known_facts}, {conversation} and {max_words} are replaced"),
                    DEFAULT_EXTRACTION_PROMPT, refresh=self._restore_extraction_prompt, refresh_icon="star-filled-rounded-symbolic"),
            ]),
            ExtraSettings.NestedSetting("decay", _("Decay"), _("How old memories fade over time"), [
                ExtraSettings.ScaleSetting("decay_influence", _("Decay Influence"),
                    _("How much the strength of a memory affects its relevance (0 = decay is ignored)"),
                    DEFAULTS["decay_influence"], 0.0, 1.0, 2),
                ExtraSettings.ScaleSetting("archive_threshold", _("Archive Threshold"),
                    _("Memories weaker than this are archived: they are no longer recalled automatically, but can still be found and restored"),
                    DEFAULTS["archive_threshold"], 0.0, 0.5, 2),
                ExtraSettings.ToggleSetting("reinforce_on_recall", _("Reinforce on Recall"),
                    _("Recalled memories become stronger and decay more slowly"), DEFAULTS["reinforce_on_recall"]),
            ]),
            ExtraSettings.ButtonSetting("rebuild_index", _("Rebuild Index"),
                _("Re-embed all memories. Needed only if search results look wrong"),
                lambda _x: self.rebuild_index(), label=_("Rebuild")),
            ExtraSettings.ButtonSetting("open_folder", _("Open Memory Folder"),
                _("Open the folder where memories are stored"), lambda _x: self._open_folder(), label=_("Open Folder")),
            ExtraSettings.ButtonSetting("reset_memory", _("Reset Memory"),
                _("Delete all memories and the summary of the current scope ({scope})").format(scope=self.get_scope_label()),
                lambda _x: self.reset_memory(), label=_("Reset")),
        ]

    def _restore_extraction_prompt(self, button=None):
        self.set_setting("extraction_prompt", DEFAULT_EXTRACTION_PROMPT)
        self.settings_update()

    def _open_folder(self):
        try:
            subprocess.Popen(["xdg-open", self.root])
        except Exception as e:
            print(f"Could not open folder: {e}")

    # Change notifications for the mini app
    def add_change_listener(self, callback):
        self._listeners.append(callback)

    def remove_change_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self):
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as e:
                print(f"Memory listener error: {e}")

    # Decay
    def half_life(self, kind: str) -> float:
        return self._get_float("fact_half_life" if kind == KIND_FACT else "conversation_half_life")

    def get_strength(self, record: MemoryRecord) -> float:
        return memory_strength(record, self.half_life(record.kind))

    def run_maintenance(self):
        """Archive memories whose strength fell below the archive threshold"""
        threshold = self._get_float("archive_threshold")
        if threshold <= 0:
            return
        now = time.time()
        weak = [record.id for record in self.store.list_all_active()
                if memory_strength(record, self.half_life(record.kind), now) < threshold]
        if weak:
            self.store.archive_many(weak)
            self._notify()

    # Search
    def search(self, scope: str, kind: str, query: str, limit: int, min_relevance: float = 0.0,
               include_archived: bool = False, wait: bool = True, queries_weights: list | None = None) -> list[tuple[MemoryRecord, float]]:
        """Hybrid search ranked by relevance weighted by memory strength"""
        if limit <= 0:
            return []
        queries = queries_weights or [(query, 1.0)]
        influence = max(0.0, min(1.0, self._get_float("decay_influence")))
        best: dict[str, tuple[MemoryRecord, float]] = {}
        for text, weight in queries:
            try:
                results = self.indexes.query(scope, kind, text[:2000], limit * 3, wait)
            except Exception as e:
                print(f"Memory search error: {e}")
                continue
            for record, relevance, _similarity in results:
                if record.archived and not include_archived:
                    continue
                strength = self.get_strength(record)
                score = relevance * weight * (1.0 - influence + influence * strength)
                if record.id not in best or best[record.id][1] < score:
                    best[record.id] = (record, score)
        ranked = sorted(best.values(), key=lambda item: item[1], reverse=True)
        return [item for item in ranked if item[1] > 0 and item[1] >= min_relevance][:limit]

    def _build_queries(self, prompt: str, history: list[dict[str, str]]) -> list[tuple[str, float]]:
        queries = [(clean_prompt(prompt), 1.0)]
        count = self._get_int("query_context_messages")
        if count > 0 and history:
            for i, message in enumerate(reversed(history[-count:])):
                text = clean_prompt(message.get("Message", ""))
                if text.strip() and message.get("User") in ("User", "Assistant"):
                    queries.append((text, 0.8 ** (i + 1)))
        return [(text, weight) for text, weight in queries if text.strip()]

    @staticmethod
    def _format_date(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")

    def _format_memory(self, record: MemoryRecord) -> str:
        date = self._format_date(record.created_at)
        if record.kind == KIND_FACT:
            return f"[Remembered fact, saved {date}] {record.text}"
        return f"[Past conversation from {date}]\n{record.text}"

    # MemoryHandler API
    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        scope = self.get_scope()
        result = []
        if self._get("include_summary"):
            summary, _updated = self.store.get_summary(scope)
            if summary.strip():
                result.append("Long-term summary of what you know about the user. Use it to personalize answers, do not mention it unless relevant:\n" + summary)
        if self._get("retrieval_mode") != "auto":
            return result
        queries = self._build_queries(prompt, history)
        if not queries:
            return result
        recalled = []
        for kind, prefix in ((KIND_FACT, "facts"), (KIND_CONVERSATION, "conversations")):
            limit = self._get_int(f"{prefix}_max_count")
            if limit <= 0:
                continue
            # Never block the message: a missing index is loaded in background for next time
            if not self.indexes.is_loaded(scope, kind):
                self.indexes.get_index(scope, kind, wait=False)
                continue
            recalled += self.search(scope, kind, "", limit, self._get_float(f"{prefix}_min_similarity"),
                                    wait=False, queries_weights=queries)
        if recalled:
            if self._get("reinforce_on_recall"):
                self.store.reinforce([record.id for record, _score in recalled])
            result += [self._format_memory(record) for record, _score in recalled]
        return result

    def register_response(self, bot_response: str, history: list[dict[str, str]]):
        if not history:
            return
        scope = self.get_scope()
        user_message = next((clean_prompt(entry.get("Message", "")) for entry in reversed(history)
                             if entry.get("User") == "User"), "")
        bot_response = remove_thinking_blocks(bot_response or "").strip()
        if self._get("store_conversations") and user_message.strip() and bot_response:
            text = f"User: {user_message.strip()}\nAssistant: {bot_response}"
            if len(text) > MAX_CONVERSATION_CHARS:
                text = text[:MAX_CONVERSATION_CHARS] + "…"
            record = self.store.add(KIND_CONVERSATION, scope, text, source="auto")
            self.indexes.add_record(record)
            self._notify()

        extract_every = self._get_int("extract_every")
        count = self.store.increment_messages(scope)
        if extract_every > 0 and count >= extract_every and self.llm is not None:
            conversation = list(history[-(extract_every * 2):])
            if not conversation or conversation[-1].get("User") != "Assistant":
                conversation.append({"User": "Assistant", "Message": bot_response})
            self.start_extraction(scope, conversation)

    # Extraction
    def start_extraction(self, scope: str, conversation: list[dict[str, str]]) -> bool:
        with self._extract_lock:
            if scope in self._extracting:
                return False
            self._extracting.add(scope)
        self.store.reset_messages(scope)

        def run():
            try:
                self._extract(scope, conversation)
            except Exception as e:
                print(f"Memory extraction error: {e}")
            finally:
                with self._extract_lock:
                    self._extracting.discard(scope)
                self.run_maintenance()
                self._notify()
        threading.Thread(target=run, daemon=True).start()
        return True

    @staticmethod
    def _format_conversation(conversation: list[dict[str, str]]) -> str:
        lines = []
        for entry in conversation:
            role = entry.get("User")
            if role not in ("User", "Assistant"):
                continue
            text = remove_thinking_blocks(clean_prompt(entry.get("Message", ""))).strip()
            if not text:
                continue
            if len(text) > MAX_EXTRACTION_MESSAGE_CHARS:
                text = text[:MAX_EXTRACTION_MESSAGE_CHARS] + "…"
            lines.append(f"{role}: {text}")
        return "\n\n".join(lines)[-MAX_EXTRACTION_CHARS:]

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        text = remove_thinking_blocks(text or "")
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _extract(self, scope: str, conversation: list[dict[str, str]]):
        conversation_text = self._format_conversation(conversation)
        if not conversation_text or self.llm is None:
            return
        summary, _updated = self.store.get_summary(scope)
        user_text = " ".join(entry.get("Message", "") for entry in conversation if entry.get("User") == "User")
        known = [record for record, _score in self.search(scope, KIND_FACT, user_text[:2000], 15)]
        if not known:
            known = self.store.list_records(scope, KIND_FACT, limit=15)
        prompt = (self._get("extraction_prompt") or DEFAULT_EXTRACTION_PROMPT)
        prompt = (prompt.replace("{summary}", summary or "(empty)")
                  .replace("{known_facts}", "\n".join("- " + record.text for record in known) or "(none)")
                  .replace("{conversation}", conversation_text)
                  .replace("{max_words}", str(self._get_int("summary_max_words"))))
        data = self._parse_json(self.llm.generate_text(prompt, [], []))
        if data is None:
            print("Memory extraction: could not parse the LLM answer")
            return
        new_summary = data.get("summary")
        if isinstance(new_summary, str) and new_summary.strip():
            self.store.set_summary(scope, new_summary)
        facts = data.get("facts") or []
        if not isinstance(facts, list):
            return
        for fact in facts:
            if isinstance(fact, str):
                fact = {"text": fact}
            if not isinstance(fact, dict) or not str(fact.get("text", "")).strip():
                continue
            try:
                importance = float(fact.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            self.save_fact(scope, str(fact["text"]), importance, source="auto")

    def save_fact(self, scope: str, text: str, importance: float = 0.5, pinned: bool = False,
                  source: str = "tool") -> tuple[MemoryRecord, bool]:
        """Save a fact, reinforcing a near-identical existing one instead of duplicating it.

        Returns the record and whether it was newly created.
        """
        text = text.strip()
        importance = max(0.0, min(1.0, importance))
        try:
            matches = self.indexes.query(scope, KIND_FACT, text, 3, wait=True)
        except Exception as e:
            print(f"Memory duplicate check failed: {e}")
            matches = []
        for record, _relevance, similarity in matches:
            if similarity is not None and similarity >= DUPLICATE_SIMILARITY:
                self.store.reinforce([record.id])
                if importance > record.importance:
                    self.store.update_importance(record.id, importance)
                if pinned and not record.pinned:
                    self.store.set_pinned(record.id, True)
                if record.archived:
                    self.store.set_archived(record.id, False)
                return self.store.get(record.id), False
        record = self.store.add(KIND_FACT, scope, text, importance, pinned, source)
        self.indexes.add_record(record)
        return record, True

    def regenerate_summary(self, scope: str | None = None) -> str:
        """Rewrite the summary from the strongest saved facts (blocking)"""
        scope = scope or self.get_scope()
        if self.llm is None:
            raise RuntimeError(_("No language model available"))
        facts = self.store.list_records(scope, KIND_FACT)
        facts.sort(key=self.get_strength, reverse=True)
        summary, _updated = self.store.get_summary(scope)
        prompt = (REGENERATE_SUMMARY_PROMPT
                  .replace("{max_words}", str(self._get_int("summary_max_words")))
                  .replace("{summary}", summary or "(empty)")
                  .replace("{facts}", "\n".join("- " + fact.text for fact in facts[:60]) or "(none)"))
        new_summary = remove_thinking_blocks(self.llm.generate_text(prompt, [], [])).strip()
        if new_summary:
            self.store.set_summary(scope, new_summary)
            self._notify()
        return new_summary

    # Management API (tools and mini app)
    def add_memory(self, text: str, kind: str = KIND_FACT, scope: str | None = None, pinned: bool = False) -> MemoryRecord:
        scope = scope or self.get_scope()
        if kind == KIND_FACT:
            record, _created = self.save_fact(scope, text, 0.5, pinned, source="user")
        else:
            record = self.store.add(kind, scope, text, pinned=pinned, source="user")
            self.indexes.add_record(record)
        self._notify()
        return record

    def edit_memory(self, memory_id: str, text: str) -> MemoryRecord | None:
        record = self.store.update_text(memory_id, text)
        if record is not None:
            self.indexes.add_record(record)
            self.indexes.mark_stale(record.scope, record.kind)
            self._notify()
        return record

    def delete_memory(self, memory_id: str):
        record = self.store.get(memory_id)
        if record is None:
            return
        self.store.delete(memory_id)
        self.indexes.mark_stale(record.scope, record.kind)
        self._notify()

    def set_pinned(self, memory_id: str, pinned: bool):
        self.store.set_pinned(memory_id, pinned)
        self._notify()

    def set_archived(self, memory_id: str, archived: bool):
        self.store.set_archived(memory_id, archived)
        self._notify()

    def set_summary(self, text: str, scope: str | None = None):
        self.store.set_summary(scope or self.get_scope(), text)
        self._notify()

    def list_memories(self, scope: str, kind: str | None, archived: bool, query: str = "") -> list[MemoryRecord]:
        if query.strip():
            kinds = KINDS if kind is None else (kind,)
            results = []
            for k in kinds:
                results += self.search(scope, k, query, 50, include_archived=True)
            results.sort(key=lambda item: item[1], reverse=True)
            return [record for record, _score in results if record.archived == archived]
        return self.store.list_records(scope, kind, archived=archived)

    def rebuild_index(self):
        self.indexes.rebuild()

    def reset_memory(self):
        scope = self.get_scope()
        self.store.delete_scope(scope)
        self.indexes.forget_scope(scope)
        self._notify()

    # Tools
    def memory_recall(self, query: str, kind: str = "all", limit: int = 5, include_archived: bool = False) -> str:
        scope = self.get_scope()
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = 5
        if isinstance(include_archived, str):
            include_archived = include_archived.lower() == "true"
        kind = str(kind or "all").lower()
        kinds = {"fact": (KIND_FACT,), "facts": (KIND_FACT,), "conversation": (KIND_CONVERSATION,),
                 "conversations": (KIND_CONVERSATION,)}.get(kind, KINDS)
        results = []
        for k in kinds:
            results += self.search(scope, k, query, limit, include_archived=include_archived)
        results.sort(key=lambda item: item[1], reverse=True)
        results = results[:limit]
        if not results:
            return "No relevant memories found."
        if self._get("reinforce_on_recall"):
            self.store.reinforce([record.id for record, _score in results])
            self._notify()
        lines = []
        for record, score in results:
            flags = [record.kind, f"saved {self._format_date(record.created_at)}",
                     f"strength {self.get_strength(record):.2f}", f"relevance {score:.2f}"]
            if record.pinned:
                flags.append("pinned")
            if record.archived:
                flags.append("archived")
            lines.append(f"[{record.id}] ({', '.join(flags)})\n{record.text}")
        return "\n\n---\n\n".join(lines)

    def memory_save(self, content: str, importance: float = 0.5, pinned: bool = False) -> str:
        if not str(content or "").strip():
            return "Error: empty memory"
        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = 0.5
        if isinstance(pinned, str):
            pinned = pinned.lower() == "true"
        record, created = self.save_fact(self.get_scope(), str(content), importance, bool(pinned), source="tool")
        self._notify()
        if created:
            return f"Memory saved with id {record.id}."
        return f"A very similar memory already exists (id {record.id}): it has been reinforced instead.\nExisting memory: {record.text}"

    def get_tools(self) -> list:
        return [
            create_io_tool(
                "memory_recall",
                "Search the long-term memory about the user with hybrid semantic and keyword search. Use it when past information could help: user preferences, facts, previous projects or earlier conversations that are not in the current chat. kind can be 'fact', 'conversation' or 'all'. Set include_archived to true to also search old, faded memories. Returns memories with id, date, strength and relevance.",
                self.memory_recall,
                title="Memory Recall",
                tools_group="Memory",
                icon_name="system-search-symbolic",
            ),
            create_io_tool(
                "memory_save",
                "Save a durable fact about the user in long-term memory, for example a preference, a personal detail, a goal or a decision. Write one self-contained sentence. importance goes from 0 (trivial) to 1 (essential); important memories fade more slowly. Use pinned=true only when the user explicitly asks to always remember something. Near-duplicate memories are merged automatically.",
                self.memory_save,
                title="Memory Save",
                tools_group="Memory",
                icon_name="document-save-symbolic",
            ),
        ]

    # Mini app
    def get_mini_app(self, **kwargs):
        from ...ui.widgets.memory_mini_app import MemoryMiniApp
        return MemoryMiniApp(self)

    def get_mini_app_title(self) -> str:
        return "Memory"

    def get_mini_app_icon(self) -> str:
        return "user-bookmarks-symbolic"
