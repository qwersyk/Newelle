import numpy as np
import os
from .memory_handler import MemoryHandler
from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm.llm import LLMHandler
from ...handlers import Handler
from ...utility.util import convert_history_newelle
from ...utility.strings import extract_json
from ...utility.pip import find_module, install_module


class MemoripyHandler(MemoryHandler):
    key = "memoripy"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.memory_manager = None

    def is_installed(self) -> bool:
        return find_module("memoripy") is not None

    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        install_module("git+https://github.com/FrancescoCaracciolo/Memoripy.git", pip_path)

    def load(self, embedding, llm):
        from memoripy import JSONStorage
        from memoripy import MemoryManager
        storage = os.path.join(self.path, "memory.json")
        storage_option = JSONStorage(storage)
        self.memory_manager = MemoryManager(self._create_chat_adapter(llm), self._create_embedding_adapter(embedding), storage_option)
    
    def _create_embedding_adapter(self, embedding: EmbeddingHandler):
        from memoripy.model import EmbeddingModel
        
        class EmbeddingAdapter(EmbeddingModel):
            def __init__(self, embedding: EmbeddingHandler):
                self.embedding = embedding

            def get_embedding(self, text: str) -> np.ndarray:
                return self.embedding.get_embedding([text])
       
            def initialize_embedding_dimension(self) -> int:
                return self.embedding.get_embedding(["test"]).shape[1]

        return EmbeddingAdapter(embedding)

    def _create_chat_adapter(self, llm:LLMHandler):
        from memoripy.model import ChatModel
        class ChatModelAdapter(ChatModel):
            def __init__(self, llm: LLMHandler):
                self.llm = llm
                self.prompt = """
                Extract key concepts from the following chat conversation. Focus on highly relevant and specific concepts that capture the essence of the discussion. Your response must be a JSON array where each element is a string representing one key concept. Do not include any additional text, commentary, or formatting; output only the JSON array.
                
                Chat Conversation:                 
                """
            def invoke(self, messages: list) -> str:
                prompts, history = convert_history_newelle(messages[:-1], llm.supports_vision())
                response = self.llm.generate_text(messages[:-1]["content"]["text"],history,prompts)
                return response

            def extract_concepts(self, text: str) -> list[str]:
                response = self.llm.generate_text(text, [],[self.prompt])
                j = extract_json(response)
                if type(j) is not list:
                    return []
                else:
                    return j
        return ChatModelAdapter(llm)

    def get_context(self, prompt, history: list[dict[str, str]], embedding: EmbeddingHandler, llm: LLMHandler) -> list[str]:
        if self.memory_manager is None:
            self.load(embedding, llm)
        if self.memory_manager is not None:
            relevant_interactions = self.memory_manager.retrieve_relevant_interactions(prompt, exclude_last_n=len(history))
        else:
            return []
        return relevant_interactions

    def register_response(self, history, embedding, llm):
        if self.memory_manager is not None:
            combined_text = " ".join([history[i]["Message"] for i in range(2)])
            concepts = self.memory_manager.extract_concepts(combined_text)
            new_embedding = embedding.get_embedding(concepts)
            self.memory_manager.add_interaction(history[-2]["Message"], history[-1]["Message"], new_embedding, concepts)
