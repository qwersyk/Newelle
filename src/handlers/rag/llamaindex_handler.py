from typing import Any, List
import threading
from ...handlers.llm import LLMHandler
from ...handlers.embeddings.embedding import EmbeddingHandler
from ...handlers import ExtraSettings
from .rag_handler import RAGHandler
from ...utility.pip import find_module, install_module
import os

class LlamaIndexHanlder(RAGHandler):
    key = "llamaindex"
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.data_path = os.path.join(os.path.dirname(self.path), "rag_cache")
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ButtonSetting("index", "Index Files", "Put the files you want to query in the specified folder. Every time you add or remove a file, or change the embedding model, you should reindex it.", self.create_index, "Re-index", None, self.documents_path),
            ExtraSettings.ScaleSetting("chunk_size", "Chunk Size", "Split text in chunks of the given size", 512, 64, 2048, 1), 
            ExtraSettings.ScaleSetting("return_documents", "Documents to return", "Maximum number of documents to return", 3,1,5, 1), 
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity of the document to be returned", "Set the percentage similarity of a document to get returned", 0.65,0,1, 2) 
        ]

    def load(self):
        if not os.path.exists(os.path.join(self.data_path, "docstore.json")):
            self.create_index()
        threading.Thread(target=self.load_index).start()

    def install(self):
       install_module("llama-index-core", os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip"))
       install_module("llama-index-readers-file", os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip"))

    def is_installed(self) -> bool:
        return find_module("llama_index") is not None

    def load_index(self):
        from llama_index.core import StorageContext, load_index_from_storage
        from llama_index.core.settings import Settings
        from llama_index.core.indices.vector_store import VectorIndexRetriever
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        storage_context = StorageContext.from_defaults(persist_dir=self.data_path)
        index = load_index_from_storage(storage_context) 
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=int(self.get_setting("return_documents")),
        )
        self.retriever = retriever
        retriever.retrieve("test")
        print("Index loaded")
   
    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        r = []
        nodes = self.retriever.retrieve(prompt)
        if len(nodes) > 0:
            r.append("--- Context from Files ---")
            for node in nodes:
                if node.score < float(self.get_setting("similarity_threshold")):
                    continue
                r.append("--")
                r.append("- Source: " + node.metadata.get("file_name"))
                r.append(node.node.get_content())
        return r

    def create_index(self, button=None):  
        from llama_index.core.settings import Settings
        from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
        # Ensure llm and embedding load 
        self.llm.load_model(None)
        self.embedding.load_model()
        print("Creating index")
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        chunk_size = int(self.get_setting("chunk_size"))
        Settings.chunk_size = chunk_size 
        documents = SimpleDirectoryReader(self.documents_path + "/", recursive=True, required_exts=[".md", ".pdf"], exclude_hidden=False).load_data()
        index = VectorStoreIndex.from_documents(documents,)
        index.storage_context.persist(self.data_path)
        print("Index created")
    
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
