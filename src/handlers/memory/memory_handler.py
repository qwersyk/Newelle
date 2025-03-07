from abc import abstractmethod

from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm.llm import LLMHandler
from ...handlers import Handler

class MemoryHandler(Handler):

    key = ""
    schema_key = "memory-settings"
    memory_size = 0

    def set_memory_size(self, length: int):
        self.memory_size = length

    def set_handlers(self, llm: LLMHandler, embedding: EmbeddingHandler):
        self.llm = llm
        self.embedding = embedding


    @abstractmethod
    def get_context(self, prompt:str, history: list[dict[str, str]]) -> list[str]:
        return []

    @abstractmethod 
    def register_response(self, bot_response:str, history:list[dict[str, str]]):
        pass

    @abstractmethod
    def reset_memory(self):
        pass
