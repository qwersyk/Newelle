from typing import Any, List
import threading
from time import time
import numpy as np

from ...handlers.llm import LLMHandler 
from ...handlers.embeddings.embedding import EmbeddingHandler 
from ...handlers import ExtraSettings 
from .rag_handler import RAGHandler, RAGIndex 
from ...utility.pip import find_module, install_module
from ...tools import Tool, create_io_tool
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
            ExtraSettings.ButtonSetting("update_index", "Update Index", "Update the index with new/modified files instead of reindexing everything", self.update_index_button_pressed, label="Update"),
            ExtraSettings.ScaleSetting("return_documents", "Documents to return", "Maximum number of documents to return", 3,1,20, 0), 
            ExtraSettings.ScaleSetting("similarity_threshold", "Similarity of the document to be returned", "Set the percentage similarity of a document to get returned", 0.1,0,1, 2), 
            ExtraSettings.ScaleSetting("oversample_factor", "Oversample Factor", "Factor to multiply the documents to return before filtering with Otsu's thresholding", 2.0, 1.0, 10.0, 1, update_settings=True),
            ExtraSettings.ScaleSetting("message_context", "Context Messages", "Number of previous messages to consider for retrieval", 5, 0, 20, 0),
            ExtraSettings.ToggleSetting("use_llm", "Secondary LLM", "Use the secondary LLM to improve retrivial", False),
            ExtraSettings.ToggleSetting("subdirectory_on", "Index Only a subdirectory", "Choose only a subdirectory to index. If you already have indexed it, you don't need to re-index", False, update_settings=True),
            ExtraSettings.ToggleSetting("use_bm25", "Use BM25", "Enable hybrid search with BM25", True),
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

    def get_index_row(self):
        """Get the exta settings corresponding to the index row to be get in Settings

        Returns:
            ExtraSettings.DownloadSetting: The extra settings for the index row 
        """
        return ExtraSettings.DownloadSetting("index", 
                                             _("Index your documents"), 
                                             _("Index all the documents in your document folder. You have to run this operation every time you change document analyzer or change embedding model. If you add/delete/edit documents, run the refresh."), 
                                             self.index_exists(), 
                                             self.index_button_pressed, lambda _: self.indexing_status, download_icon="text-x-generic",
                                             refresh=self.update_index_button_pressed)
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
       if not find_module("llama_index") and not find_module("faiss"): 
           dependencies = "tiktoken faiss-cpu llama-index-core llama-index-readers-file llama-index-vector-stores-faiss llama-index-retrievers-bm25"
           install_module(dependencies, self.pip_path)

    def is_installed(self) -> bool:
        return find_module("llama_index") is not None and find_module("tiktoken") is not None and find_module("faiss") is not None and find_module("llama_index.vector_stores") is not None and find_module("llama_index.retrievers.bm25") is not None

    def index_exists(self) -> bool:
        _, data_path = self.get_paths()
        return os.path.exists(os.path.join(data_path, "docstore.json"))

    def delete_index(self):
        import shutil
        _, data_path = self.get_paths()
        if os.path.exists(data_path):
            shutil.rmtree(data_path)
        self.index = None
        self.retriever = None
        self.loaded_index = ""


    def load_index(self):
        from llama_index.core import StorageContext, load_index_from_storage
        from llama_index.core.settings import Settings
        from llama_index.core.indices.vector_store import VectorIndexRetriever
        from llama_index.vector_stores.faiss import FaissVectorStore
        from llama_index.core.retrievers import BaseRetriever

        documents_path, data_path = self.get_paths()
        Settings.embed_model = self.get_embedding_adapter(self.embedding)
        Settings.llm = self.get_llm_adapter()
        vector_store = FaissVectorStore.from_persist_dir(data_path)
        storage_context = StorageContext.from_defaults(persist_dir=data_path, vector_store=vector_store)
        index = load_index_from_storage(storage_context) 
        oversample = float(self.get_setting("oversample_factor", 2.0))
        similarity_top_k = int(int(self.get_setting("return_documents")) * oversample)
        
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=similarity_top_k,
        )
        
        if self.get_setting("use_bm25"):
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                bm25_path = os.path.join(data_path, "bm25_retriever")
                bm25_retriever = None
                if os.path.exists(bm25_path):
                     bm25_retriever = BM25Retriever.from_persist_dir(bm25_path)
                else:
                     bm25_retriever = BM25Retriever.from_defaults(docstore=index.docstore, similarity_top_k=similarity_top_k)
                     
                if bm25_retriever:
                    class HybridRetriever(BaseRetriever):
                        def __init__(self, vector_retriever, bm25_retriever, rrf_k):
                            super().__init__()
                            self.vector_retriever = vector_retriever
                            self.bm25_retriever = bm25_retriever
                            self.rrf_k = rrf_k
                            
                        def _retrieve(self, query_bundle):
                            query = query_bundle.query_str
                            vec_nodes = self.vector_retriever.retrieve(query_bundle)
                            bm25_nodes = self.bm25_retriever.retrieve(query_bundle)
                            return LlamaIndexHanlder.reciprocal_rank_fusion(query, vec_nodes, bm25_nodes, self.rrf_k)
                            
                    retriever = HybridRetriever(retriever, bm25_retriever, similarity_top_k)
            except Exception as e:
                print(f"Failed to load BM25 retriever: {e}")

        self.index = index
        self.retriever = retriever
        self.embedding.load_model()
        retriever.retrieve("test")
        self.loading_thread = None
        self.loaded_index = data_path

    @staticmethod
    def compute_bm25_weight(query: str, is_exact: bool = False) -> float:
        import math
        if is_exact:
            return 0.9
        
        # Basic stop words list (can be expanded)
        stop_words = {"a", "an", "the", "in", "on", "at", "for", "to", "of", "and", "or", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "but", "if", "so", "as", "not", "no", "from", "by", "with", "about", "into", "through", "during", "before", "after", "above", "below", "up", "down", "out", "off", "over", "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "nor", "only", "own", "same", "than", "too", "very", "can", "will", "just", "should", "now"}
        
        query_words = query.lower().split()
        distinct_words = set(query_words)
        ilength = len(distinct_words)
        
        # rlength is the number of stop words removed from the natural language query
        rlength = sum(1 for w in distinct_words if w in stop_words)
        
        denom = ilength - rlength
        if denom < 1:
            denom = 1
            
        query_length_weight = 1.0 / math.pow(float(denom), 2)
        query_length_weight = max(0.0, min(query_length_weight, 1.0))
        
        bm_weight = 0.25 + query_length_weight * 0.50
        return bm_weight

    @staticmethod
    def reciprocal_rank_fusion(query: str, embedding_results: list, bm25_results: list, k: int) -> list:
        # Map node ids to their ranks
        bm25_ranks = {node.node.node_id: i + 1 for i, node in enumerate(bm25_results)}
        embedding_ranks = {node.node.node_id: i + 1 for i, node in enumerate(embedding_results)}
        
        # Combined results map: node_id -> NodeWithScore
        # We start with embedding results and augment with BM25
        results_map = {node.node.node_id: node for node in embedding_results}
        
        # Add BM25 results if missing
        missing_bm25 = []
        for node in bm25_results:
            if node.node.node_id not in results_map:
                missing_bm25.append(node)
                results_map[node.node.node_id] = node # Add to map for lookup, score will be overwritten
        
        all_nodes = list(results_map.values())
        
        bm_weight = 0 if not bm25_results else LlamaIndexHanlder.compute_bm25_weight(query)
        fusion_k = 60
        
        len_bm25 = len(bm25_results)
        len_embedding = len(embedding_results)
        
        for node in all_nodes:
            nid = node.node.node_id
            
            a_bm25_rank = bm25_ranks.get(nid, len_bm25 + 1)
            a_embedding_rank = embedding_ranks.get(nid, len_embedding + 1)
            
            a_bm25_score = 1.0 / (fusion_k + a_bm25_rank)
            a_embedding_score = 1.0 / (fusion_k + a_embedding_rank)
            
            # Weighted RRF
            weighted_score = bm_weight * a_bm25_score + (1.0 - bm_weight) * a_embedding_score
            node.score = weighted_score
            
        # Sort by new score
        all_nodes.sort(key=lambda x: x.score, reverse=True)
        return all_nodes[:k]



    @staticmethod
    def apply_otsu(nodes, return_documents):
        if not nodes:
            return []
        
        scores = [node.score for node in nodes]
        if len(scores) < 2:
             return nodes

        # Otsu's thresholding
        scores = np.array(scores)
        sorted_indices = np.argsort(scores)
        sorted_scores = scores[sorted_indices]
        
        n = len(scores)
        best_var = -1
        best_thresh = sorted_scores[0]
        
        # Iterate splits
        for i in range(1, n):
            c0 = sorted_scores[:i]
            c1 = sorted_scores[i:]
            w0 = i / n
            w1 = (n - i) / n
            mu0 = np.mean(c0)
            mu1 = np.mean(c1)
            var_b = w0 * w1 * (mu0 - mu1)**2
            if var_b > best_var:
                best_var = var_b
                best_thresh = sorted_scores[i]
        
        # Filter
        filtered_nodes = [node for node in nodes if node.score >= best_thresh]
        filtered_nodes.sort(key=lambda x: x.score, reverse=True)
        return filtered_nodes[:return_documents]

    def retrieve_with_history(self, prompt: str, history: list[dict[str, str]]) -> list:
        from llama_index.core.schema import NodeWithScore
        import copy
        
        message_context = int(self.get_setting("message_context", 5))
        queries = [prompt]
        
        # Get previous messages in reverse order (newest first)
        if message_context > 1 and history:
            prev_messages = history[-(message_context-1):]
            queries.extend([msg.get("content", "") for msg in reversed(prev_messages)])
            
        all_nodes = {} # node_id -> NodeWithScore
        
        # Decay factor for weights
        decay = 0.8
        
        for i, query in enumerate(queries):
            if not query.strip():
                continue
                
            weight = decay ** i
            nodes = self.retriever.retrieve(query)
            
            for node in nodes:
                # We need to clone the node because we are modifying the score
                # and the same node instance might be returned by the retriever (cached)
                # actually retriever returns new NodeWithScore objects but they point to same TextNode
                
                # If we already found this node, add to score
                if node.node.node_id in all_nodes:
                    all_nodes[node.node.node_id].score += node.score * weight
                else:
                    # Create a copy to avoid side effects if we modify it
                    new_node = copy.deepcopy(node)
                    new_node.score = node.score * weight
                    all_nodes[node.node.node_id] = new_node
        
        # Convert back to list
        combined_nodes = list(all_nodes.values())
        return combined_nodes

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
            nodes = self.retrieve_with_history(prompt, history)
            nodes = self.apply_otsu(nodes, int(self.get_setting("return_documents")))
            for node in nodes:
                if not self.get_setting("use_bm25") and node.score < float(self.get_setting("similarity_threshold")):
                     continue
                r.append("---")
                r.append("- Source: " + str(node.metadata.get("file_path", node.metadata.get("file_name", "Unknown"))))
                r.append(node.node.get_content())
        return r

    def get_paths(self):
        if self.get_setting("subdirectory_on"):
            documents_path = self.get_setting("subdirectory")
            try:
                name = documents_path.split("/")[-1]
            except:
                name = "index"
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
        
        documents_path, data_path = self.get_paths()
        try:
            self.llm.load_model(None)
            self.embedding.load_model()
            print("Creating index")
            Settings.embed_model = self.get_embedding_adapter(self.embedding)
            chunk_size = int(self.get_setting("chunk_size"))
            Settings.chunk_size = chunk_size 
            documents = SimpleDirectoryReader(documents_path, recursive=True, required_exts=self.get_supported_formats(), exclude_hidden=False, filename_as_id=True).load_data() 
            custom_folders = self.get_custom_folders()
            for folder in custom_folders:
                documents.extend(SimpleDirectoryReader(folder, recursive=True, required_exts=self.get_supported_formats(), exclude_hidden=True, filename_as_id=True).load_data())
            self.indexing_status = 0
            faiss_index = faiss.IndexFlatL2(self.embedding.get_embedding_size())
            vector_store = FaissVectorStore(faiss_index=faiss_index)
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            index = VectorStoreIndex.from_documents(documents[:1], storage_context=storage_context)
            i = 1
            for document in documents[1:]:
                print("Indexing document " + str(i) + " of " + str(len(documents)) + ": " + document.metadata.get("file_path", document.metadata.get("file_name", "Unknown")))
                index.insert(document)
                i += 1
                self.indexing_status = (i / len(documents))

            index.storage_context.persist(data_path)
            
            # Persist BM25 Index
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                # We need all nodes for BM25. VectorStoreIndex splits documents into nodes.
                # We can access them from docstore
                nodes = list(index.docstore.docs.values())
                print("Creating BM25 Index...")
                # Ensure similarity_top_k doesn't exceed the number of nodes to avoid warnings
                similarity_top_k = min(int(self.get_setting("return_documents")), len(nodes)) if nodes else 1
                bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=similarity_top_k)
                bm25_path = os.path.join(data_path, "bm25_retriever")
                bm25_retriever.persist(bm25_path)
            except Exception as e:
                print(f"Failed to create BM25 index: {e}")

            self.indexing = False
        except Exception as e:
            print(e)
            self.indexing = False
            self.indexing_status = 1
        self.set_setting("last_index_created", time())

    def update_index_button_pressed(self, button=None):
        if self.indexing:
            return
        t = threading.Thread(target=self.update_index, args=(button, ))
        t.start()

    def update_index(self, button=None):
        if not self.is_installed():
            return
        if not self.index_exists():
            self.create_index(button)
            return

        from llama_index.core.settings import Settings
        from llama_index.core import SimpleDirectoryReader
        
        documents_path, data_path = self.get_paths()
        try:
            self.llm.load_model(None)
            self.embedding.load_model()
            print("Updating index")
            
            # Ensure index is loaded
            if self.index is None:
                self.load_index()
                self.wait_for_loading()
            
            if self.index is None:
                print("Failed to load index for update")
                return

            self.indexing = True
            self.indexing_status = 0
            
            Settings.embed_model = self.get_embedding_adapter(self.embedding)
            chunk_size = int(self.get_setting("chunk_size"))
            Settings.chunk_size = chunk_size 
            
            reader = SimpleDirectoryReader(
                documents_path, 
                recursive=True, 
                required_exts=self.get_supported_formats(), 
                exclude_hidden=False,
                filename_as_id=True
            )
            documents = reader.load_data()
            
            custom_folders = self.get_custom_folders()
            for folder in custom_folders:
                documents.extend(SimpleDirectoryReader(
                    folder, 
                    recursive=True, 
                    required_exts=self.get_supported_formats(), 
                    exclude_hidden=True,
                    filename_as_id=True
                ).load_data())
            
            print(f"Refreshing {len(documents)} documents...")
            self.index.refresh_ref_docs(documents)
            
            self.index.storage_context.persist(data_path)
            
            # Update BM25 Index if needed
            if self.get_setting("use_bm25"):
                try:
                    from llama_index.retrievers.bm25 import BM25Retriever
                    nodes = list(self.index.docstore.docs.values())
                    print("Updating BM25 Index...")
                    # Ensure similarity_top_k doesn't exceed the number of nodes to avoid warnings
                    similarity_top_k = min(int(self.get_setting("return_documents")), len(nodes)) if nodes else 1
                    bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=similarity_top_k)
                    bm25_path = os.path.join(data_path, "bm25_retriever")
                    bm25_retriever.persist(bm25_path)
                    
                    # Refresh the current retriever
                    self.load_index()
                    self.wait_for_loading()
                except Exception as e:
                    print(f"Failed to update BM25 index: {e}")

            self.indexing = False
            self.indexing_status = 1
        except Exception as e:
            print(f"Error updating index: {e}")
            self.indexing = False
            self.indexing_status = 1
        self.set_setting("last_index_created", time())
        self.settings_update()

    @staticmethod 
    def parse_document_list(documents: list[str]):
        from llama_index.core import SimpleDirectoryReader, Document
        import requests
        document_list = []
        urls = []
        for document in documents:
            if document.startswith("file:") or os.path.exists(document):
                path = document.lstrip("file:")
                document_list.extend(SimpleDirectoryReader(input_files=[path]).load_data())
            elif document.startswith("text:"):
                text = document.lstrip("text:")
                document_list.append(Document(text=text))
            elif document.startswith("url:") or document.startswith("http://") or document.startswith("https://"):
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
        
        bm25_retriever = None
        use_bm25 = self.get_setting("use_bm25")
        if use_bm25 and document_list:
             try:
                 from llama_index.retrievers.bm25 import BM25Retriever
                 # Ensure similarity_top_k doesn't exceed the number of nodes to avoid warnings
                 similarity_top_k = min(int(self.get_setting("return_documents")), len(document_list))
                 bm25_retriever = BM25Retriever.from_defaults(nodes=document_list, similarity_top_k=similarity_top_k)
             except Exception as e:
                 print(f"Failed to create BM25 retriever: {e}")

        return LlamaIndexIndex(index, int(self.get_setting("return_documents")), float(self.get_setting("similarity_threshold")), counter, document_list, float(self.get_setting("oversample_factor", 2.0)), bm25_retriever, use_bm25) 

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
    
    def get_tools(self) -> list:
        """Get tools provided by the RAG handler

        Returns:
            list: List of tools for semantic search
        """
        r= [
            create_io_tool(
                name="rag_search_files",
                description="Perform semantic search over specified files or documents. Use this tool when you need to search through specific documents, files, or URLs for relevant information based on meaning rather than keywords. Supports: file paths (file:path/to/file), direct text content (text:content), and URLs (url:https://example.com). Examples: 'find information about API setup in readme.md', 'search for pricing in documentation.pdf', 'find all mentions of configuration in project files'.",
                func=self._tool_search_files,
                title="RAG Search Files",
                default_on=True,
                tools_group="RAG"
            ),
        ]
        if self.settings.get_boolean("rag-on"):
            r += [
                create_io_tool(
                    name="rag_search_index",
                    description="Perform semantic search over the pre-built document index. Use this tool when you need to find information across the user's indexed documents (documents folder and custom folders) without specifying specific files. Returns relevant passages based on semantic similarity to your query. Examples: 'find documentation about authentication', 'search for code examples', 'find information about database setup'. Only use if an index has been created.",
                    func=self._tool_search_index,
                    title="RAG Search Index",
                    default_on=True,
                    tools_group="RAG"
                ),
            ]
        return r

    def _tool_search_files(self, query: str, documents: list[str]) -> str:
        """Tool function to perform semantic search over arbitrary files

        Args:
            query: The semantic search query
            documents: List of documents to search. Format:
                file:path/to/file - for local files
                text:content - for direct text content
                url:https://url - for URLs

        Returns:
            Relevant passages from the documents
        """
        if type(documents) is not list:
            documents = [documents]
        try:
            results = self.query_document(query, documents)
            return "\n".join(results) if results else "No relevant results found."
        except Exception as e:
            return f"Error performing semantic search: {str(e)}"

    def _tool_search_index(self, query: str) -> str:
        """Tool function to perform semantic search over the existing index

        Args:
            query: The semantic search query

        Returns:
            Relevant passages from the indexed documents
        """
        try:
            if not self.index_exists():
                return "No index exists. Please create an index first."
            results = self.get_context(query, [])
            return "\n".join(results) if results else "No relevant results found in index."
        except Exception as e:
            return f"Error searching index: {str(e)}"

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
    def __init__(self, index, return_documents, similarity_threshold, counter, docs, oversample_factor=2.0, bm25_retriever=None, use_bm25=False):
        super().__init__()
        self.index = index
        self.retriever = None
        self.bm25_retriever = bm25_retriever
        self.use_bm25 = use_bm25
        self.return_documents = return_documents
        self.similarity_threshold = similarity_threshold
        self.counter = counter
        self.docs = docs
        self.oversample_factor = oversample_factor
       
    def get_index_size(self):
        return self.counter.total_embedding_token_count

    def query(self, query: str) -> list[str]:
        from llama_index.core.retrievers import VectorIndexRetriever
        if self.retriever is None:
            similarity_top_k = int(self.return_documents * self.oversample_factor)
            retriever = VectorIndexRetriever(
                index=self.index,
                similarity_top_k=similarity_top_k)
            self.retriever = retriever 
        
        r = []
        vec_nodes = self.retriever.retrieve(query)
        
        nodes = vec_nodes
        if self.use_bm25 and self.bm25_retriever:
            bm25_nodes = self.bm25_retriever.retrieve(query)
            # Use shared static method
            nodes = LlamaIndexHanlder.reciprocal_rank_fusion(query, vec_nodes, bm25_nodes, int(self.return_documents * self.oversample_factor))
            
        nodes = LlamaIndexHanlder.apply_otsu(nodes, self.return_documents)
        for node in nodes:
            if not self.use_bm25 and node.score < float(self.similarity_threshold):
                continue
            r.append("---")
            r.append("- Source: " + str(node.metadata.get("file_path", node.metadata.get("file_name", "Unknown"))))
            r.append(node.node.get_content())
        return r

    def get_all_contexts(self) -> list[str]:
        r = []
        last_document = ""
        for document in self.docs:
            file = document.metadata.get("file_path", document.metadata.get("file_name", "Unknown"))
            if file != last_document:
                r.append("-- Source: " + str(file))
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

    def persist(self, path: str):
        """Persist the index to disk

        Args:
            path: The directory path where to persist the index
        """
        import os
        from llama_index.core import StorageContext

        # Create directory if it doesn't exist
        os.makedirs(path, exist_ok=True)

        # Persist the vector store index
        self.index.storage_context.persist(persist_dir=path)

        # Persist BM25 retriever if used
        if self.use_bm25 and self.bm25_retriever:
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                bm25_path = os.path.join(path, "bm25_retriever")
                self.bm25_retriever.persist(bm25_path)
            except Exception as e:
                print(f"Failed to persist BM25 retriever: {e}")

    def load_from_disk(self, path: str):
        """Load the index from disk

        Args:
            path: The directory path where the index is persisted
        """
        import os
        from llama_index.core import StorageContext, load_index_from_storage
        from llama_index.vector_stores.faiss import FaissVectorStore
        from llama_index.core.retrievers import VectorIndexRetriever

        if not os.path.exists(path):
            raise FileNotFoundError(f"Index path does not exist: {path}")

        # Load the vector store index
        vector_store = FaissVectorStore.from_persist_dir(path)
        storage_context = StorageContext.from_defaults(persist_dir=path, vector_store=vector_store)
        self.index = load_index_from_storage(storage_context)

        # Recreate retriever
        similarity_top_k = int(self.return_documents * self.oversample_factor)
        self.retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=similarity_top_k
        )

        # Load BM25 retriever if it exists
        if self.use_bm25:
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                bm25_path = os.path.join(path, "bm25_retriever")
                if os.path.exists(bm25_path):
                    self.bm25_retriever = BM25Retriever.from_persist_dir(bm25_path)
            except Exception as e:
                print(f"Failed to load BM25 retriever: {e}")
                self.bm25_retriever = None


