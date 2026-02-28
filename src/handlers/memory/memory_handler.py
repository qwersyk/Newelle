from abc import abstractmethod
from typing import Optional, List
from ..handler import Handler
from ..llm.llm import LLMHandler
from ..embeddings.embedding import EmbeddingHandler
from ..rag.rag_handler import RAGHandler


class MemoryHandler(Handler):
    """Base class for memory handlers.

    Memory handlers are responsible for storing and retrieving conversation context
    to provide long-term memory capabilities to the AI assistant.
    """
    schema_key = "memory-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.memory_size = 0

    def set_memory_size(self, size: int):
        """Set the memory size (number of messages to remember)

        Args:
            size: Number of messages to keep in memory
        """
        self.memory_size = size

    def set_handlers(self, llm: LLMHandler, embedding: EmbeddingHandler, rag: Optional[RAGHandler] = None):
        """Set the LLM and embedding handlers for memory operations.

        Args:
            llm: The LLM handler to use for text generation
            embedding: The embedding handler to use for semantic search
            rag: Optional RAG handler for advanced document processing
        """
        self.llm = llm
        self.embedding = embedding
        self.rag = rag

    @abstractmethod
    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        """Get relevant context from memory for the given prompt.

        Args:
            prompt: The current user prompt
            history: Conversation history

        Returns:
            List of context strings to add to the prompt
        """
        pass

    @abstractmethod
    def register_response(self, bot_response: str, history: list[dict[str, str]]):
        """Register a bot response in memory.

        Args:
            bot_response: The bot's response message
            history: Conversation history
        """
        pass

    def get_tools(self) -> list:
        """Get tools provided by this memory handler.

        Returns:
            List of tools (empty by default)
        """
        return []
