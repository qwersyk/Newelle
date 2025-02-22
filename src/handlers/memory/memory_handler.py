from abc import abstractmethod

from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm.llm import LLMHandler
from ...handlers import Handler

class MemoryHandler(Handler):

    key = ""
    schema_key = "memory-settings"

    @abstractmethod
    def get_context(self, prompt:str, history: list[dict[str, str]], embedding: EmbeddingHandler, llm: LLMHandler) -> list[str]:
        return []

    @abstractmethod 
    def register_response(self, history, embedding, llm):
        pass

    @abstractmethod
    def reset_memory(self):
        pass
