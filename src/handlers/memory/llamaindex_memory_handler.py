from typing import Any, List, Optional
import threading
import os
import shutil
from time import time
import numpy as np

from .memory_handler import MemoryHandler
from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers.llm import LLMHandler
from ...handlers.rag.rag_handler import RAGHandler
from ...handlers import ExtraSettings
from ...utility.pip import find_module, install_module
from ...utility.strings import remove_thinking_blocks

class LlamaIndexMemoryHandler(MemoryHandler):
    key = "llamaindex"
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.data_path = os.path.join(self.path, "llamaindex_memory")
        self.index = None
        self.loading_thread = None
        self.loaded = False
        self.embedding = None
        self.llm = None
        self.rag = None

    def set_handlers(self, llm: LLMHandler, embedding: EmbeddingHandler, rag: Optional[RAGHandler] = None):
        self.llm = llm
        self.embedding = embedding
        self.rag = rag
        self.load_index()

    def is_installed(self) -> bool:
        return find_module("llama_index") is not None and find_module("tiktoken") is not None and find_module("faiss") is not None and find_module("llama_index.vector_stores") is not None

    def install(self):
       if not self.is_installed(): 
           dependencies = "tiktoken faiss-cpu llama-index-core llama-index-readers-file llama-index-vector-stores-faiss"
           install_module(dependencies, self.pip_path)

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ButtonSetting("reset_memory", "Reset Memory", "Reset the memory", lambda x: self.reset_memory(), "Reset Memory"),
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity Threshold", "Minimum similarity for retrieved memories", 0.7, 0.0, 1.0, 2),
            ExtraSettings.ScaleSetting("max_memory_count", "Max Memories", "Maximum number of memories to retrieve", 5, 1, 20, 0),
        ]

    def reset_memory(self):
        if os.path.exists(self.data_path):
            shutil.rmtree(self.data_path)
        self.index = None
        self.loaded = False
        self.load_index() 

    def load_index(self):
        if self.loading_thread and self.loading_thread.is_alive():
            return
        
        if not self.is_installed():
            return

        def _load():
            try:
                from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
                from llama_index.core.settings import Settings
                from llama_index.vector_stores.faiss import FaissVectorStore
                import faiss

                if not os.path.exists(self.data_path):
                    os.makedirs(self.data_path)
                
                # Adapters need to be ready
                while self.embedding is None or self.llm is None:
                    # This might happen if set_handlers is called later? 
                    # Actually set_handlers calls load_index.
                    pass 

                Settings.embed_model = self.get_embedding_adapter(self.embedding)
                Settings.llm = self.get_llm_adapter()

                # Check if index exists
                if os.path.exists(os.path.join(self.data_path, "docstore.json")):
                    vector_store = FaissVectorStore.from_persist_dir(self.data_path)
                    storage_context = StorageContext.from_defaults(persist_dir=self.data_path, vector_store=vector_store)
                    self.index = load_index_from_storage(storage_context)
                else:
                    # Create new index
                    faiss_index = faiss.IndexFlatL2(self.embedding.get_embedding_size())
                    vector_store = FaissVectorStore(faiss_index=faiss_index)
                    storage_context = StorageContext.from_defaults(vector_store=vector_store)
                    self.index = VectorStoreIndex([], storage_context=storage_context)
                    self.index.storage_context.persist(self.data_path)
                
                self.loaded = True
            except Exception as e:
                print(f"Error loading LlamaIndex memory: {e}")
                self.loaded = False

        self.loading_thread = threading.Thread(target=_load)
        self.loading_thread.start()

    def wait_for_loading(self):
        if self.loading_thread:
            self.loading_thread.join()

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        if not self.is_installed():
            return []
            
        if not self.loaded:
             if not self.loading_thread:
                 self.load_index()
             self.wait_for_loading()
        
        if not self.index:
            return []

        from llama_index.core.retrievers import VectorIndexRetriever
        
        max_memories = int(self.get_setting("max_memory_count", 5))
        similarity_threshold = float(self.get_setting("similarity_threshold", 0.7))

        retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=max_memories,
        )
        
        nodes = retriever.retrieve(prompt)
        results = []
        
        for node in nodes:
            if node.score >= similarity_threshold:
                 results.append(node.node.get_content())

        if results:
             return ["--- Memory Context ---"] + results
        return []

    def register_response(self, bot_response: str, history: list[dict[str, str]]):
        if not self.is_installed() or not self.loaded or not self.index:
            return

        from llama_index.core import Document

        if not history:
            return

        last_user_msg = history[-1].get("Message", "")

        # Remove thinking blocks from bot response before storing
        bot_response = remove_thinking_blocks(bot_response)

        interaction = f"User: {last_user_msg}\nAssistant: {bot_response}"

        doc = Document(text=interaction)
        self.index.insert(doc)
        self.index.storage_context.persist(self.data_path)

    def get_embedding_adapter(self, embedding: EmbeddingHandler):
        from llama_index.core.embeddings import BaseEmbedding
        class CustomEmbedding(BaseEmbedding):
            def __init__(self, embedding_model: EmbeddingHandler, **kwargs: Any):
                super().__init__(**kwargs)
                self._embedding_model = embedding_model
                self._embedding_size = embedding_model.get_embedding_size()
                 
            async def _aget_query_embedding(self, query: str) -> List[float]:
                return self._get_query_embedding(query)

            async def _aget_text_embedding(self, text: str) -> List[float]:
                return self._get_text_embedding(text)

            def _get_query_embedding(self, query: str) -> List[float]:
                embeddings = self._embedding_model.get_embedding([query])
                return embeddings[0]

            def _get_text_embedding(self, text: str) -> List[float]:
                embeddings = self._embedding_model.get_embedding([text])
                return embeddings[0]

            def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
                embeddings = self._embedding_model.get_embedding(texts)
                return embeddings
        return CustomEmbedding(embedding)

    def get_llm_adapter(self):
        from llama_index.core.llms import (
            CustomLLM,
            CompletionResponse,
            CompletionResponseGen,
            LLMMetadata,
        )
        from typing import Any
        from llama_index.core.llms.callbacks import llm_completion_callback
        class LLMAdapter(CustomLLM):
            context_window: int = 4000
            num_output: int = 2000
            dummy_response: str = "My response"
            def set_llm(self, llm: LLMHandler):
                self._llm = llm
            @property
            def metadata(self) -> LLMMetadata:
                """Get LLM metadata."""
                return LLMMetadata(
                    context_window=self.context_window,
                    num_output=self.num_output,
                )

            @llm_completion_callback()
            def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
                return CompletionResponse(text=self._llm.generate_text(prompt, [], []))

            @llm_completion_callback()
            def stream_complete(
                self, prompt: str, **kwargs: Any
            ) -> CompletionResponseGen:
                response = self._llm.generate_text(prompt, [], [])
                yield CompletionResponse(text=response, delta=response)

        adapter = LLMAdapter()
        adapter.set_llm(self.llm) 
        return adapter
