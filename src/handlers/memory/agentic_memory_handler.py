import os
from datetime import datetime
from typing import List, Optional
import json
import threading

from .memory_handler import MemoryHandler
from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm.llm import LLMHandler
from ...handlers.rag.rag_handler import RAGHandler
from ...handlers import ExtraSettings
from ...tools import create_io_tool
from ...utility.pip import find_module, install_module
from ...utility.strings import clean_prompt, remove_thinking_blocks


class MemoryChunk:
    """Represents a chunk of memory with metadata"""
    def __init__(self, content: str, file_path: str, line_start: int, line_end: int, embedding=None):
        self.content = content
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.embedding = embedding


class AgenticMemoryHandler(MemoryHandler):
    key = "agentic_memory_handler"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        # The cache_dir is typically passed from controller
        # For now, we'll construct it relative to the config path
        self.cache_dir = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "cache")
        self.memory_dir = os.path.join(self.cache_dir, "memories")
        self.memory_file = os.path.join(self.memory_dir, "MEMORY.md")
        self.daily_notes_dir = os.path.join(self.memory_dir, "memory")
        self.index_dir = os.path.join(self.memory_dir, "llamaindex_index")

        self.embedding: Optional[EmbeddingHandler] = None
        self.llm: Optional[LLMHandler] = None
        self.rag: Optional[RAGHandler] = None
        self.memory_index = None  # RAGIndex from self.rag.build_index
        self.chunks: List[MemoryChunk] = []
        self._lock = threading.Lock()
        self._index_loaded = False
        self._loading_thread = None
        self.message_count = 0
        self._background_update_thread = None

    def set_handlers(self, llm: LLMHandler, embedding: EmbeddingHandler, rag: Optional[RAGHandler] = None):
        self.llm = llm
        self.embedding = embedding
        self.rag = rag
        self._ensure_directories()
        self._load_index()

    def is_installed(self) -> bool:
        # No special dependencies needed - RAG handler manages its own dependencies
        return True

    def install(self):
        # No special dependencies needed - RAG handler manages its own dependencies
        self._is_installed_cache = None


    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ButtonSetting("reset_memory", "Reset Memory", "Reset all memory files and index", lambda _x: self.reset_memory(), label="Reset Memory"),
            ExtraSettings.ButtonSetting("open_folder", "Open Memory Folder", "Open the folder where memories are stored", lambda _x: self._open_folder(), label="Open Folder"),
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity Threshold", "Minimum similarity for retrieved memories (0-1)", 0.3, 0.0, 1.0, 2),
            ExtraSettings.ScaleSetting("context_threshold", "Context Threshold", "Minimum similarity for auto-adding to context (0-1)", 0.7, 0.0, 1.0, 2),
            ExtraSettings.ScaleSetting("max_results", "Max Results", "Maximum number of results to return from search", 5, 1, 20, 0),
            ExtraSettings.ScaleSetting("chunk_size", "Chunk Size", "Maximum characters per memory chunk", 500, 100, 2000, 0),
            ExtraSettings.ScaleSetting("update_freq", "Update Frequency", "Number of messages between automatic memory consolidation (0=disabled)", 20, 0, 50, 0),
            ExtraSettings.ScaleSetting("extract_freq", "Extract Frequency", "Number of messages between automatic memory extractions (0=disabled, only manual tool usage)", 0, 0, 50, 0),
            ExtraSettings.ToggleSetting("auto_add_context", "Auto-add to Context", "Automatically add relevant memories to the conversation context", True),
            ExtraSettings.ToggleSetting("store_interactions", "Store Interactions", "Store user interactions in separate file for vector search", True),
            ExtraSettings.ToggleSetting("keepalive_memory", "Always add main memory to prompt", "This option will always add consolidated memory to prompt in any request", False),
            ExtraSettings.MultilineEntrySetting("extract_prompt", "Extract Prompt",
                "Prompt for extracting important info from conversations. {user} and {assistant} will be replaced.",
                self._get_default_extract_prompt(), refresh=self._restore_extract_prompt, refresh_icon="star-filled-rounded-symbolic"),
            ExtraSettings.MultilineEntrySetting("consolidate_prompt", "Consolidate Prompt",
                "Prompt for memory consolidation. {conversations} and {existing_memories} will be replaced.",
                self._get_default_consolidate_prompt(), refresh=self._restore_consolidate_prompt, refresh_icon="star-filled-rounded-symbolic"),
        ]

    def _get_default_extract_prompt(self) -> str:
        return """Extract the most important information from this conversation that should be remembered for future interactions.
Focus on:
1. User preferences, interests, or goals mentioned
2. Important facts or personal information shared
3. Decisions made or conclusions reached
4. Action items or tasks to remember

If the conversation contains nothing important worth remembering, return exactly: "NOTHING_IMPORTANT"

Conversation:
User: {user}

Assistant: {assistant}

Extract only the important information in a concise format (bullet points preferred). Do not include filler or casual conversation."""

    def _get_default_consolidate_prompt(self) -> str:
        return """You are tasked with consolidating memories from daily conversation logs into long-term facts.
Review the following information and extract NEW important facts that should be added to long-term memory.

Existing relevant memories:
{existing_memories}

Recent conversation logs are provided as context. Please extract 5-10 key facts that are NOT already in existing memories:
1. User preferences and interests
2. Important personal information shared
3. Recurring topics or themes
4. Goals or projects mentioned

Format each fact as a bullet point. Only output the new facts, no other text.

Recent conversations:
{conversations}"""

    def _restore_extract_prompt(self, button=None):
        self.set_setting("extract_prompt", self._get_default_extract_prompt())
        self.settings_update()

    def _restore_consolidate_prompt(self, button=None):
        self.set_setting("consolidate_prompt", self._get_default_consolidate_prompt())
        self.settings_update()

    def _ensure_directories(self):
        """Create memory directories if they don't exist"""
        os.makedirs(self.memory_dir, exist_ok=True)
        os.makedirs(self.daily_notes_dir, exist_ok=True)

    def _open_folder(self):
        """Open the memory folder in the file manager"""
        import subprocess
        try:
            subprocess.run(["xdg-open", self.memory_dir])
        except Exception as e:
            print(f"Could not open folder: {e}")

    def reset_memory(self):
        """Reset all memory files and index"""
        import shutil
        if os.path.exists(self.memory_dir):
            shutil.rmtree(self.memory_dir)
        self._ensure_directories()
        self.chunks = []
        self.memory_index = None
        self._index_loaded = False

    def _get_daily_note_path(self) -> str:
        """Get the path for today's daily note"""
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.daily_notes_dir, f"{today}.md")

    def _split_markdown_into_chunks(self, content: str, file_path: str, max_chunk_size: int = 500) -> List[MemoryChunk]:
        """Split markdown content into chunks by headers and paragraphs"""
        chunks = []
        lines = content.split('\n')
        current_chunk_lines = []
        current_chunk_start = 0
        current_size = 0

        for i, line in enumerate(lines):
            line_size = len(line) + 1  # +1 for newline
            current_size += line_size
            current_chunk_lines.append(line)

            # Split on headers
            if line.startswith('#'):
                # Save previous chunk if it exists
                if current_chunk_lines:
                    chunk_content = '\n'.join(current_chunk_lines)
                    chunks.append(MemoryChunk(
                        content=chunk_content,
                        file_path=file_path,
                        line_start=current_chunk_start,
                        line_end=i - 1
                    ))
                current_chunk_lines = [line]
                current_chunk_start = i
                current_size = line_size

            # Split on chunk size
            elif current_size >= max_chunk_size:
                chunk_content = '\n'.join(current_chunk_lines)
                chunks.append(MemoryChunk(
                    content=chunk_content,
                    file_path=file_path,
                    line_start=current_chunk_start,
                    line_end=i
                ))
                current_chunk_lines = []
                current_chunk_start = i + 1
                current_size = 0

        # Add remaining content
        if current_chunk_lines:
            chunk_content = '\n'.join(current_chunk_lines)
            chunks.append(MemoryChunk(
                content=chunk_content,
                file_path=file_path,
                line_start=current_chunk_start,
                line_end=len(lines) - 1
            ))

        return chunks

    def _get_memory_documents(self) -> list[str]:
        """Get list of memory files as documents for RAG indexing"""
        documents = []

        # Process MEMORY.md
        if os.path.exists(self.memory_file):
            documents.append(f"file:{self.memory_file}")

        # Process daily notes
        if os.path.exists(self.daily_notes_dir):
            for filename in os.listdir(self.daily_notes_dir):
                if filename.endswith('.md'):
                    file_path = os.path.join(self.daily_notes_dir, filename)
                    documents.append(f"file:{file_path}")

        return documents

    def _load_index(self):
        """Load the memory index using RAG handler"""
        if self._loading_thread and self._loading_thread.is_alive():
            return

        def _load():
            try:
                # Wait for RAG to be ready
                while self.rag is None or not self.embedding:
                    import time
                    time.sleep(0.1)

                # Build index from memory files using RAG handler
                memory_documents = self._get_memory_documents()
                self.memory_index = self.rag.build_index(memory_documents)
                self._index_loaded = True
            except Exception as e:
                print(f"Error loading memory index: {e}")
                # Fallback to simple implementation
                self._load_index_simple()

        self._loading_thread = threading.Thread(target=_load, daemon=True)
        self._loading_thread.start()

    def _load_index_simple(self):
        """Fallback simple implementation without LlamaIndex"""
        with self._lock:
            if self._index_loaded:
                return

            self.chunks = []
            chunk_size = int(self.get_setting("chunk_size", return_value=500))

            # Process MEMORY.md
            if os.path.exists(self.memory_file):
                with open(self.memory_file, 'r') as f:
                    content = f.read()
                if content.strip():
                    self.chunks.extend(self._split_markdown_into_chunks(content, self.memory_file, chunk_size))

            # Process daily notes
            if os.path.exists(self.daily_notes_dir):
                for filename in os.listdir(self.daily_notes_dir):
                    if filename.endswith('.md'):
                        file_path = os.path.join(self.daily_notes_dir, filename)
                        with open(file_path, 'r') as f:
                            content = f.read()
                        if content.strip():
                            self.chunks.extend(self._split_markdown_into_chunks(content, file_path, chunk_size))

            # Generate embeddings if embedding handler is available
            if self.embedding and self.chunks:
                self._generate_embeddings()
                self._save_index_simple()

            self._index_loaded = True

    def _save_index_simple(self):
        """Save the memory index to disk (fallback for simple implementation)"""
        try:
            index_file = os.path.join(self.memory_dir, "index.json")
            index_data = {
                'chunks': [],
                'version': 1
            }

            for chunk in self.chunks:
                index_data['chunks'].append({
                    'content': chunk.content,
                    'file_path': chunk.file_path,
                    'line_start': chunk.line_start,
                    'line_end': chunk.line_end,
                    'embedding': chunk.embedding
                })

            os.makedirs(self.memory_dir, exist_ok=True)
            with open(index_file, 'w') as f:
                json.dump(index_data, f)
        except Exception as e:
            print(f"Error saving simple index: {e}")

    def _generate_embeddings(self):
        """Generate embeddings for all chunks"""
        if not self.embedding:
            return

        texts = [chunk.content for chunk in self.chunks]
        embeddings = self.embedding.get_embedding(texts)

        for i, chunk in enumerate(self.chunks):
            chunk.embedding = embeddings[i].tolist() if hasattr(embeddings[i], 'tolist') else list(embeddings[i])

    def _save_index(self):
        """Save the memory index to disk"""
        # Use RAG handler's persist if available
        if self.memory_index is not None and self.rag:
            try:
                self.memory_index.persist(self.index_dir)
                return
            except Exception as e:
                print(f"Error saving memory index, falling back to simple: {e}")

        # Fallback to simple implementation
        self._save_index_simple()

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        """Get relevant context from memory for the given prompt"""
        # Check if auto-add to context is enabled
        r = []
        if self.get_setting("keepalive_memory"):
            if os.path.exists(self.memory_file):
                content = open(self.memory_file, 'r').read()
                r.append("--- Memory.md Content ---\n" + content)
        prompt = clean_prompt(prompt)
        if not self.get_setting("auto_add_context", return_value=True):
            return r

        if not self.embedding or not self._index_loaded:
            self._load_index()

        if not self.chunks:
            return r

        # Get search results using semantic search with context threshold
        context_threshold = float(self.get_setting("context_threshold", return_value=0.7))
        results = self._semantic_search(prompt, threshold=context_threshold)

        if results:
            return r + ["--- Memory Context ---"] + results
        return r

    def register_response(self, bot_response: str, history: list[dict[str, str]]):
        """Extract and store important information from conversation"""
        if not history:
            return

        last_user_msg = history[-1].get("Message", "")

        # Remove thinking blocks from bot response
        bot_response = remove_thinking_blocks(bot_response)

        # Store full interactions if enabled
        if self.get_setting("store_interactions", return_value=True):
            self._store_interaction(last_user_msg, bot_response)

        # Get extract frequency setting (0 = disabled, only manual tool usage)
        extract_freq = int(self.get_setting("extract_freq", return_value=0))

        # Only extract if enabled and it's time to do so
        extracted_info = None
        if extract_freq > 0 and (self.message_count + 1) % extract_freq == 0:
            # Extract important information using LLM
            extracted_info = self._extract_important_info(last_user_msg, bot_response)

        if not extracted_info:
            # No important info to store in daily notes (or extraction not scheduled)
            # Still check for consolidation
            self.message_count += 1
            update_freq = int(self.get_setting("update_freq", return_value=0))
            if update_freq > 0 and self.message_count % update_freq == 0:
                self._run_memory_consolidation(history)
            return

        # Get today's note path
        daily_note_path = self._get_daily_note_path()

        # Append extracted info to daily note
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## {timestamp}\n\n{extracted_info}\n"

        try:
            with open(daily_note_path, 'a') as f:
                f.write(entry)

            # Update memory index if available
            if self.memory_index is not None and self.rag:
                self._update_llamaindex(daily_note_path)
        except Exception as e:
            print(f"Error writing to daily note: {e}")

        # Increment message count and check if consolidation is needed
        self.message_count += 1
        update_freq = int(self.get_setting("update_freq", return_value=0))
        if update_freq > 0 and self.message_count % update_freq == 0:
            # Run memory consolidation in background
            self._run_memory_consolidation(history)

    def _store_interaction(self, user_msg: str, bot_response: str):
        """Store full user interaction in INTERACTIONS.md for vector search"""
        interactions_file = os.path.join(self.memory_dir, "INTERACTIONS.md")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {timestamp}\n\n**User:** {user_msg}\n\n**Assistant:** {bot_response}\n\n---\n"

        try:
            with open(interactions_file, 'a') as f:
                f.write(entry)

            # Update memory index if available
            if self.memory_index is not None and self.rag:
                self._update_llamaindex(interactions_file)
        except Exception as e:
            print(f"Error writing to interactions file: {e}")

    def _update_llamaindex(self, file_path: str):
        """Update memory index with a new or modified file"""
        try:
            if not os.path.exists(file_path):
                return

            # Rebuild index with updated files
            self._rebuild_index()
        except Exception as e:
            print(f"Error updating memory index: {e}")

    def _rebuild_index(self):
        """Rebuild the memory index from all memory files"""
        try:
            if self.rag:
                memory_documents = self._get_memory_documents()
                self.memory_index = self.rag.build_index(memory_documents)
        except Exception as e:
            print(f"Error rebuilding memory index: {e}")

    def _extract_important_info(self, user_msg: str, bot_response: str) -> str:
        """Extract important information from conversation to store in memory"""
        if not self.llm:
            # Fallback: store everything if LLM is not available
            return f"**User:** {user_msg}\n\n**Assistant:** {bot_response}"

        # Get the customizable prompt from settings
        prompt = self.get_setting("extract_prompt", return_value=self._get_default_extract_prompt())

        try:
            result = self.llm.generate_text(
                prompt.format(user=user_msg, assistant=bot_response),
                [],
                []
            )
            result = remove_thinking_blocks(result).strip()

            if "NOTHING_IMPORTANT" in result.upper() or len(result) < 20:
                return ""

            return result
        except Exception as e:
            print(f"Error extracting important info: {e}")
            # Fallback to storing everything
            return f"**User:** {user_msg}\n\n**Assistant:** {bot_response}"

    def _run_memory_consolidation(self, history: list[dict[str, str]]):
        """Run memory consolidation in a background thread"""
        if self._background_update_thread is not None and self._background_update_thread.is_alive():
            return  # Already running

        def consolidate():
            try:
                # Use RAG handler for advanced processing if available, otherwise use LLM
                if self.rag and self.llm:
                    self._rag_memory_consolidation(history)
                elif self.llm:
                    self._llm_memory_consolidation(history)
            except Exception as e:
                print(f"Error in memory consolidation: {e}")

        self._background_update_thread = threading.Thread(target=consolidate, daemon=True)
        self._background_update_thread.start()

    def _llm_memory_consolidation(self, history: list[dict[str, str]]):
        """Consolidate memories using the LLM to extract and summarize key facts"""
        # Get recent conversations from daily notes
        recent_notes = []
        if os.path.exists(self.daily_notes_dir):
            for filename in sorted(os.listdir(self.daily_notes_dir), reverse=True)[:5]:
                if filename.endswith('.md'):
                    file_path = os.path.join(self.daily_notes_dir, filename)
                    with open(file_path, 'r') as f:
                        recent_notes.append(f.read())

        if not recent_notes:
            return

        # Get the customizable consolidation prompt from settings
        consolidate_prompt_template = self.get_setting("consolidate_prompt", return_value=self._get_default_consolidate_prompt())

        # Format with empty existing_memories for LLM-only consolidation
        conversations_text = "\n\n---\n\n".join(recent_notes[-3:])  # Use last 3 notes
        consolidation_prompt = consolidate_prompt_template.format(
            existing_memories="(No existing memories available)",
            conversations=conversations_text[:4000]  # Limit size
        )

        try:
            consolidated = self.llm.generate_text(consolidation_prompt, [], [])
            consolidated = remove_thinking_blocks(consolidated)

            # Append to MEMORY.md
            timestamp = datetime.now().strftime("%Y-%m-%d")
            entry = f"\n## Consolidated {timestamp}\n\n{consolidated}\n\n"

            with open(self.memory_file, 'a') as f:
                f.write(entry)

            # Rebuild index
            self._rebuild_index()
        except Exception as e:
            print(f"Error in LLM memory consolidation: {e}")

    def _rag_memory_consolidation(self, history: list[dict[str, str]]):
        """Consolidate memories using the RAG handler for advanced processing"""
        if not self.rag or not self.llm:
            return

        # Collect memory files as documents
        memory_documents = [f"file:{self.memory_file}"]
        if os.path.exists(self.daily_notes_dir):
            for filename in sorted(os.listdir(self.daily_notes_dir), reverse=True)[:7]:
                if filename.endswith('.md'):
                    file_path = os.path.join(self.daily_notes_dir, filename)
                    memory_documents.append(f"file:{file_path}")

        try:
            # Use RAG to find relevant existing memories
            query = "important facts user preferences interests goals"
            rag_context = self.rag.get_context(query, [])

            # Get the customizable consolidation prompt from settings
            consolidate_prompt_template = self.get_setting("consolidate_prompt", return_value=self._get_default_consolidate_prompt())

            consolidation_prompt = consolidate_prompt_template.format(
                existing_memories="\n".join(rag_context[:10]),
                conversations=""  # Will be filled below
            )

            # Get recent conversations
            recent_notes = []
            if os.path.exists(self.daily_notes_dir):
                for filename in sorted(os.listdir(self.daily_notes_dir), reverse=True)[:3]:
                    if filename.endswith('.md'):
                        file_path = os.path.join(self.daily_notes_dir, filename)
                        with open(file_path, 'r') as f:
                            recent_notes.append(f.read())

            conversations_text = "\n\n---\n\n".join(recent_notes)
            full_prompt = consolidation_prompt + f"\n\nRecent conversations:\n{conversations_text[:3000]}"

            consolidated = self.llm.generate_text(full_prompt, [], [])
            consolidated = remove_thinking_blocks(consolidated)

            # Append to MEMORY.md
            timestamp = datetime.now().strftime("%Y-%m-%d")
            entry = f"\n## Consolidated {timestamp}\n\n{consolidated}\n\n"

            with open(self.memory_file, 'a') as f:
                f.write(entry)

            # Rebuild index
            self._rebuild_index()
        except Exception as e:
            print(f"Error in RAG memory consolidation: {e}")

    def _semantic_search(self, query: str, threshold: Optional[float] = None) -> List[str]:
        """Search for similar memories using embeddings

        Args:
            query: The search query
            threshold: Optional similarity threshold (0-1). If None, uses similarity_threshold setting
        """
        if not self.embedding:
            return []

        # Use provided threshold or fall back to similarity_threshold setting
        if threshold is None:
            threshold = float(self.get_setting("similarity_threshold", return_value=0.6))

        # Try using RAG index if available
        if self.memory_index is not None and self.rag:
            try:
                results = self.memory_index.query(query)
                # Filter by threshold if RAGIndex doesn't support it natively
                max_results = int(self.get_setting("max_results", return_value=5))
                return results[:max_results]
            except Exception as e:
                print(f"Error in RAG index search: {e}")

        # Fallback to simple search
        return self._semantic_search_simple(query, threshold)

    def _semantic_search_simple(self, query: str, threshold: float) -> List[str]:
        """Simple semantic search using numpy (fallback when RAG is not available)"""
        if not self.chunks:
            return []

        max_results = int(self.get_setting("max_results", return_value=5))

        # Generate query embedding
        query_embedding = self.embedding.get_embedding([query])[0]
        if hasattr(query_embedding, 'tolist'):
            query_embedding = query_embedding.tolist()

        # Calculate cosine similarity
        import numpy as np
        similarities = []

        for chunk in self.chunks:
            if chunk.embedding is None:
                continue
            chunk_emb = np.array(chunk.embedding)
            query_emb = np.array(query_embedding)

            # Cosine similarity
            dot_product = np.dot(chunk_emb, query_emb)
            norm_chunk = np.linalg.norm(chunk_emb)
            norm_query = np.linalg.norm(query_emb)

            if norm_chunk > 0 and norm_query > 0:
                similarity = dot_product / (norm_chunk * norm_query)
                similarities.append((chunk, similarity))

        # Sort by similarity
