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
        self.indexing_status = 0
        self.indexing = False
        self.loading_thread = None
    
    def get_extra_settings(self) -> list:
        return [
            #ExtraSettings.DownloadSetting("index", "Index Files", "Put the files you want to query in the specified folder. Every time you add or remove a file, or change the embedding model, you should reindex it.", self.index_exists(), self.index_button_pressed, lambda _: self.indexing_status, download_icon="text-x-generic", folder=self.documents_path),
            ExtraSettings.ScaleSetting("chunk_size", "Chunk Size", "Split text in chunks of the given size (in tokens). Requires a reindex", 512, 64, 2048, 1), 
            ExtraSettings.ScaleSetting("return_documents", "Documents to return", "Maximum number of documents to return", 3,1,5, 1), 
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity of the document to be returned", "Set the percentage similarity of a document to get returned", 0.65,0,1, 2), 
            ExtraSettings.ToggleSetting("use_llm", "Secondary LLM", "Use the secondary LLM to improve retrivial", False),
            ExtraSettings.NestedSetting("documents", "Document extensions", "List of document extensions to index", 
                [
                    ExtraSettings.ToggleSetting("md", "Markdown", ".md files", True),
                    ExtraSettings.ToggleSetting("pdf", "PDF", ".pdf files", True),
                    ExtraSettings.ToggleSetting("docx", "Docx", ".docx files", True),
                    ExtraSettings.ToggleSetting("epub", "Epub", ".epub files", True),
                ]
            )
        ]

    def wait_for_loading(self):
        if self.loading_thread is not None:
            self.loading_thread.join()

    def get_supported_files(self) -> list:
        return ["*" + x for x in self.get_supported_formats()]

    def get_supported_formats(self) -> list[str]:
        r = []
        if self.get_setting("md"):
            r.append(".md")
        if self.get_setting("pdf"):
            r.append(".pdf")
        if self.get_setting("docx"):
            r.append(".docx")
        if self.get_setting("epub"):
            r.append(".epub")
        return r

    def load(self):
        if self.index_exists():
            self.loading_thread = threading.Thread(target=self.load_index)
            self.loading_thread.start()

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
        Settings.llm = self.get_llm_adapter()
        storage_context = StorageContext.from_defaults(persist_dir=self.data_path)
        index = load_index_from_storage(storage_context) 
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=int(self.get_setting("return_documents")),
        )
        self.index = index
        self.retriever = retriever
        retriever.retrieve("test")
        self.loading_thread = None

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        self.wait_for_loading()
        r = []
        if self.get_setting("use_llm"):
            query_engine = self.index.as_query_engine()
            response = query_engine.query(prompt)
            r.append(str(response))
        else:
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

    def index_exists(self):
        return os.path.exists(os.path.join(self.data_path, "docstore.json")) and (not self.indexing) 
    
    def index_button_pressed(self, button=None):
        if self.index_exists():
            os.remove(os.path.join(self.data_path, "docstore.json"))
            self.settings_update()
        else:
            self.indexing = True
            self.create_index()

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
        documents = SimpleDirectoryReader(self.documents_path + "/", recursive=True, required_exts=self.get_supported_formats(), exclude_hidden=False).load_data() 
        self.indexing_status = 0
        index = VectorStoreIndex.from_documents(documents[:1])
        i = 1
        for document in documents[1:]:
            index.insert(document)
            i += 1
            self.indexing_status = (i / len(documents))

        index.storage_context.persist(self.data_path)
        self.indexing = False
   
    def query_document(self, prompt: str, documents: list[str], chunk_size: int|None = None) -> list[str]: 
        from llama_index.core.settings import Settings
        from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Document
        from llama_index.core.retrievers import VectorIndexRetriever
        import requests
        self.llm.load_model(None)
        self.embedding.load_model()
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        chunk_size = int(self.get_setting("chunk_size")) if chunk_size is None else chunk_size
        document_list = []
        urls = []
        for document in documents:
            if document.startswith("file:"):
                path = document.lstrip("file:")
                document_list.extend(SimpleDirectoryReader(input_files=[path]).load_data())
            elif document.startswith("text:"):
                text = document.lstrip("text:")
                document_list.append(Document(text=text))
            elif document.startswith("url:"):
                url = document.lstrip("url:")
                urls.append(url)
        index = VectorStoreIndex.from_documents(document_list)
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=int(self.get_setting("return_documents")),
        )
        r = []
        nodes = retriever.retrieve(prompt)
        for node in nodes:
            if node.score < float(self.get_setting("similarity_threshold")):
                continue
            r.append("--")
            r.append("- Source: " + node.metadata.get("file_name"))
            r.append(node.node.get_content())
        return r

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
