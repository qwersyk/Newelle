from typing import Any, List
import threading
from time import time

from ...handlers.llm import LLMHandler 
from ...handlers.embeddings.embedding import EmbeddingHandler 
from ...handlers import ExtraSettings 
from .rag_handler import RAGHandler, RAGIndex 
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
        self.index = None
        self.loaded_index = ""
   
    def get_subdirectories(self):
        r = []
        for dir in os.scandir(self.documents_path):
            if os.path.isdir(dir.path):
                r.append([dir.name, dir.path])
        return r
    
    def get_extra_settings(self) -> list:
        r = [
            ExtraSettings.ScaleSetting("chunk_size", "Chunk Size", "Split text in chunks of the given size (in tokens). Requires a reindex", 512, 64, 2048, 0), 
            ExtraSettings.ScaleSetting("return_documents", "Documents to return", "Maximum number of documents to return", 3,1,5, 0), 
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity of the document to be returned", "Set the percentage similarity of a document to get returned", 0.1,0,1, 2), 
            ExtraSettings.ToggleSetting("use_llm", "Secondary LLM", "Use the secondary LLM to improve retrivial", False),
            ExtraSettings.ToggleSetting("subdirectory_on", "Index Only a subdirectory", "Choose only a subdirectory to index. If you already have indexed it, you don't need to re-index", False, update_settings=True), 
        ]
        if self.get_setting("subdirectory_on", False, False):
            r += [
                ExtraSettings.ComboSetting("subdirectory", "Subdirectory", "Subdirectory to index", self.get_subdirectories(), self.get_subdirectories()[0][1] if len(self.get_subdirectories()) > 0 else ".", update_settings=True)
            ]

        r += [ 
            ExtraSettings.NestedSetting("documents", "Document extensions", "List of document extensions to index", 
                [
                    ExtraSettings.ToggleSetting("md", "Markdown", ".md files", True),
                    ExtraSettings.ToggleSetting("txt", "TXT", ".txt files", True),
                    ExtraSettings.ToggleSetting("pdf", "PDF", ".pdf files", True),
                    ExtraSettings.ToggleSetting("docx", "Docx", ".docx files", True),
                    ExtraSettings.ToggleSetting("epub", "Epub", ".epub files", True),
                    ExtraSettings.ToggleSetting("csv", "CSV", ".csv files", True),
                ]
            )
        ]
        return r

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
        if self.get_setting("csv"):
            r.append(".csv")
        return r
    def get_supported_files_reading(self) -> list:
        return self.get_supported_files() + ["plaintext"]
    
    def load(self):
        if self.index_exists() and ((self.index is None and self.loading_thread is None) or self.loaded_index != self.get_paths()[1]):
            self.loading_thread = threading.Thread(target=self.load_index)
            self.loading_thread.start()

    def install(self):
       dependencies = "tiktoken faiss-cpu llama-index-core llama-index-readers-file llama-index-vector-stores-faiss"
       install_module(dependencies, self.pip_path)

    def is_installed(self) -> bool:
        return find_module("llama_index") is not None and find_module("tiktoken") is not None and find_module("faiss") is not None and find_module("llama_index.vector_stores") is not None

    def load_index(self):
        from llama_index.core import StorageContext, load_index_from_storage
        from llama_index.core.settings import Settings
        from llama_index.core.indices.vector_store import VectorIndexRetriever
        from llama_index.vector_stores.faiss import FaissVectorStore
        documents_path, data_path = self.get_paths()
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        Settings.llm = self.get_llm_adapter()
        vector_store = FaissVectorStore.from_persist_dir(data_path)
        storage_context = StorageContext.from_defaults(persist_dir=data_path, vector_store=vector_store)
        index = load_index_from_storage(storage_context) 
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=int(self.get_setting("return_documents")),
        )
        self.index = index
        self.retriever = retriever
        self.embedding.load_model()
        retriever.retrieve("test")
        self.loading_thread = None
        self.loaded_index = data_path

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        self.wait_for_loading()
        if self.index is None:
            return []
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
                    r.append("---")
                    r.append("- Source: " + node.metadata.get("file_name"))
                    r.append(node.node.get_content())
        return r

    def index_exists(self):
        documents_path, data_path = self.get_paths()
        return os.path.exists(os.path.join(data_path, "docstore.json")) and (not self.indexing) 
    
    def delete_index(self):
        documents_path, data_path = self.get_paths()
        os.remove(os.path.join(data_path, "docstore.json"))

    def get_paths(self):
        if self.get_setting("subdirectory_on"):
            documents_path = self.get_setting("subdirectory")
            name = documents_path.split("/")[-1]
            data_path = os.path.join(self.data_path, name)
            if not os.path.exists(data_path):
                os.makedirs(data_path)
        else:
            documents_path = self.documents_path
            data_path = self.data_path
        return documents_path, data_path
    
    def create_index(self, button=None):  
        if not self.is_installed():
            return
        from llama_index.core.settings import Settings
        from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext
        from llama_index.vector_stores.faiss import FaissVectorStore
        import faiss
        # Ensure llm and embedding load 
        documents_path, data_path = self.get_paths()
        try:
            self.llm.load_model(None)
            self.embedding.load_model()
            print("Creating index")
            Settings.embed_model = self.get_embedding_adapter(self.embedding)
            chunk_size = int(self.get_setting("chunk_size"))
            Settings.chunk_size = chunk_size 
            documents = SimpleDirectoryReader(documents_path, recursive=True, required_exts=self.get_supported_formats(), exclude_hidden=False).load_data() 
            self.indexing_status = 0
            faiss_index = faiss.IndexFlatL2(self.embedding.get_embedding_size())
            vector_store = FaissVectorStore(faiss_index=faiss_index)
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            index = VectorStoreIndex.from_documents(documents[:1], storage_context=storage_context)
            i = 1
            for document in documents[1:]:
                print("Indexing document " + str(i) + " of " + str(len(documents)) + ": " + document.metadata["file_name"])
                index.insert(document)
                i += 1
                self.indexing_status = (i / len(documents))

            index.storage_context.persist(data_path)
            self.indexing = False
        except Exception as e:
            print(e)
            self.indexing = False
            self.indexing_status = 1
        self.set_setting("last_index_created", time())
  
    @staticmethod 
    def parse_document_list(documents: list[str]):
        from llama_index.core import SimpleDirectoryReader, Document
        import requests
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
        t = []
        for url in urls:
            def request(url):
                r = requests.get(url)
                document_list.append(Document(text=r.text))
            th = threading.Thread(target=request, args=(url, ))
            th.start()
        [t.join() for t in t]
        return document_list
    
    def build_index(self, documents: list[str], chunk_size: int | None = None) -> RAGIndex: 
        from llama_index.core.settings import Settings
        from llama_index.core import VectorStoreIndex, StorageContext 
        from llama_index.core.callbacks import TokenCountingHandler, CallbackManager
        from llama_index.vector_stores.faiss import FaissVectorStore
        import tiktoken 
        import faiss
        counter = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model("gpt-4o").encode
        )
        self.llm.load_model(None)
        self.embedding.load_model()
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        Settings.callback_manager = CallbackManager([counter]) 
        chunk_size = int(self.get_setting("chunk_size")) if chunk_size is None else chunk_size
        document_list = self.parse_document_list(documents)
        faiss_index = faiss.IndexFlatL2(self.embedding.get_embedding_size())
        vector_store = FaissVectorStore(faiss_index=faiss_index)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_documents(document_list, storage_context=storage_context)
        return LlamaIndexIndex(index, int(self.get_setting("return_documents")), float(self.get_setting("similarity_threshold")), counter, document_list) 

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


class LlamaIndexIndex(RAGIndex):
    def __init__(self, index, return_documents, similarity_threshold, counter, docs):
        super().__init__()
        self.index = index
        self.retriever = None
        self.return_documents = return_documents
        self.similarity_threshold = similarity_threshold
        self.counter = counter
        self.docs = docs
       
    def get_index_size(self):
        return self.counter.total_embedding_token_count

    def query(self, query: str) -> list[str]:
        from llama_index.core.retrievers import VectorIndexRetriever
        if self.retriever is None:
            retriever = VectorIndexRetriever(
                index=self.index,
                similarity_top_k=int(self.return_documents))
            self.retriever = retriever 
        r = []
        nodes = self.retriever.retrieve(query)
        for node in nodes:
            if node.score < float(self.similarity_threshold):
                continue
            r.append("---")
            r.append(node.node.get_content())
        return r

    def get_all_contexts(self) -> list[str]:
        r = []
        last_document = ""
        for document in self.docs:
            file = document.metadata.get("file_path", None)
            if file != last_document:
                r.append("-- File name: " + file)
                last_document = file
            r.append(document.text)
        return r

    def insert(self, documents: list[str]):
        self.documents += documents
        documents_list = LlamaIndexHanlder.parse_document_list(documents)
        self.docs += documents_list
        for document in documents_list:
            self.index.insert(document)

    def remove(self, documents: list[str]):
        for document in documents:
            try:
                self.index.delete(document)
            except Exception as e:
                print("Error deleting document:" + str(e))
        return super().remove(documents)

