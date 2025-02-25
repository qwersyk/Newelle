from ...handlers import Handler
from ...handlers.embeddings import EmbeddingHandler
from ...handlers.llm import LLMHandler
from abc import abstractmethod
import os

class RAGHandler(Handler):
    key = ""
    schema_key = "rag-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.documents_path = os.path.join(os.path.dirname(self.path), "documents")
        if not os.path.exists(self.documents_path):
            os.mkdir(self.documents_path)

    @abstractmethod
    def get_context(self, prompt:str, history: list[dict[str, str]], embedding: EmbeddingHandler, llm: LLMHandler, documents: list[str]) -> list[str]:
        return []

    @abstractmethod
    def query_document(self, prompt: str, documents: list[str]) -> str:
        pass
