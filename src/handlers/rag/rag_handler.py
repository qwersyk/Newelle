from ...handlers import Handler
from ...handlers.embeddings import EmbeddingHandler
from ...handlers.llm import LLMHandler
from ...handlers import ExtraSettings
from ...tools import create_io_tool
from abc import abstractmethod
import os


class RAGRecord:
    """A standalone text indexed under a stable id (it is never chunked)."""
    __slots__ = ("id", "text")

    def __init__(self, id: str, text: str):
        self.id = id
        self.text = text


class RAGResult:
    """A scored match from a record index.

    similarity: cosine similarity of the vector match (None if unknown)
    bm25: BM25 score normalized to 0-1 over the returned results (None if unknown)
    relevance: combined 0-1 relevance; None when the index can only rank results
    """
    __slots__ = ("id", "text", "similarity", "bm25", "relevance")

    def __init__(self, id: str, text: str, similarity: float | None = None,
                 bm25: float | None = None, relevance: float | None = None):
        self.id = id
        self.text = text
        self.similarity = similarity
        self.bm25 = bm25
        self.relevance = relevance


class RAGIndex:
    def __init__(self):
        self.documents = []

    @abstractmethod
    def get_all_contexts(self) -> list[str]:
        """Return all the contexts in the index

        Returns:
            List of contexts
        """
        pass

    @abstractmethod 
    def query(self, query:str) -> list[str]:
        """Query the index

        Args:
            query: query string 

        Returns:
            List of context 
        """
        pass

    @abstractmethod
    def insert(self, documents: list[str]):
        """Add a document in the index

        Args:
            documents: list of documents to add to the index 
        """
        self.documents += documents

    @abstractmethod
    def remove(self, documents: list[str]):
        """Remove a document from the index

        Args:
            documents: List of documents to remove 
        """
        self.documents = list(set(self.documents) - set(documents))

    def get_documents(self) -> list[str]:
        """Return the documents in the index

        Returns:
            List of documents 
        """
        return self.documents

    @abstractmethod
    def get_index_size(self):
        """Return the size of the index in tokens

        Returns:
            Size of the index 
        """
        return len(self.documents)

    @abstractmethod
    def update_index(self, documents: list[str]):
        """Remove documents not in the list from the index and add new documents

        Args:
            documents: List of documents to add
        """
        for document in self.documents:
            if document not in documents:
                self.remove([document])
        for document in documents:
            if document not in self.documents:
                self.insert([document])

    @abstractmethod
    def persist(self, path: str):
        """Persist the index to disk

        Args:
            path: The directory path where to persist the index
        """
        pass

    @abstractmethod
    def load_from_disk(self, path: str):
        """Load the index from disk

        Args:
            path: The directory path where the index is persisted
        """
        pass

    def insert_records(self, records: list[RAGRecord]):
        """Add id-addressable records to the index"""
        self.insert(["text:" + record.text for record in records])

    def get_record_ids(self) -> list[str]:
        """Return the ids of the records stored in the index"""
        return []

    def query_scored(self, query: str, top_k: int) -> list[RAGResult]:
        """Query the index returning scored records.

        The default implementation only knows the result texts, so ids are empty
        and scores are None.
        """
        return [RAGResult("", text) for text in self.query(query)[:top_k]]


class GenericRecordIndex(RAGIndex):
    """Record index built on top of any text-only RAGIndex.

    Used by RAG handlers that do not implement build_record_index natively. Results
    are mapped back to record ids by text containment, and are only ranked.
    """

    def __init__(self, handler, records: list[RAGRecord]):
        super().__init__()
        self.handler = handler
        self.records: dict[str, str] = {}
        self.inner: RAGIndex | None = None
        self.insert_records(records)

    def insert_records(self, records: list[RAGRecord]):
        if not records:
            return
        for record in records:
            self.records[record.id] = record.text
        documents = ["text:" + record.text for record in records]
        if self.inner is None:
            self.inner = self.handler.build_index(documents)
        else:
            self.inner.insert(documents)

    def get_record_ids(self) -> list[str]:
        return list(self.records.keys())

    def get_index_size(self):
        return len(self.records)

    def get_all_contexts(self) -> list[str]:
        return list(self.records.values())

    def query(self, query: str) -> list[str]:
        return [result.text for result in self.query_scored(query, 5)]

    def query_scored(self, query: str, top_k: int) -> list[RAGResult]:
        if self.inner is None:
            return []
        results = []
        seen = set()
        for context in self.inner.query(query):
            for record_id, text in self.records.items():
                if record_id not in seen and text.strip() and text.strip() in context:
                    seen.add(record_id)
                    results.append(RAGResult(record_id, text))
                    break
            if len(results) >= top_k:
                break
        return results


class RAGHandler(Handler):
    key = ""
    schema_key = "rag-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.documents_path = os.path.join(self.path, "documents")
        if not os.path.exists(self.documents_path):
            os.mkdir(self.documents_path)

    def get_custom_folders(self) -> list[str]:
        """Return the list of user-defined custom document folders.

        The folders are read from the GSettings key ``custom-document-folders``
        and filtered to include only existing directories.
        """
        folders: list[str] = []
        folders = self.settings.get_strv("custom-document-folders")

        # Expand ~ and filter out non-existing paths
        valid_folders: list[str] = []
        for folder in folders:
            if not isinstance(folder, str):
                continue
            expanded = os.path.expanduser(folder)
            if os.path.isdir(expanded):
                valid_folders.append(expanded)
        return valid_folders

    def set_handlers(self, llm: LLMHandler, embeddings: EmbeddingHandler):
        self.llm = llm
        self.embedding = embeddings

    def get_index_row(self):
        """Get the exta settings corresponding to the index row to be get in Settings

        Returns:
            ExtraSettings.DownloadSetting: The extra settings for the index row 
        """
        return ExtraSettings.DownloadSetting("index", 
                                             _("Index your documents"), 
                                             _("Index all the documents in your document folder. You have to run this operation every time you edit/create a document, change document analyzer or change embedding model"), 
                                             self.index_exists(), 
                                             self.index_button_pressed, lambda _: self.indexing_status, download_icon="text-x-generic")
    @abstractmethod
    def get_supported_files(self) -> list:
        """Get the list of supported files to run RAG on

        Returns: 
            List of supported files
            
        """
        return []
    
    @abstractmethod
    def get_supported_files_reading(self) -> list:
        """Get the list of supported files that can be read for context extraction"""
        return self.get_supported_files()

    @abstractmethod 
    def load(self):
        pass 

    @abstractmethod
    def get_context(self, prompt:str, history: list[dict[str, str]]) -> list[str]:
        return []

    def query_document(self, prompt: str, documents: list[str], chunk_size: int|None = None) -> list[str]:
        """
        Query the document

        Args:
            prompt: Prompt for the query 
            documents: List of documents to query, can be in this format:
                file:path/to/file
                text:text of the content to index
                url:https://url
            chunk_size: Chunk size for the query 

        Returns:
            The query result 
        """
        index = self.build_index(documents, chunk_size)
        return index.query(prompt)

    @abstractmethod
    def build_index(self, documents: list[str], chunk_size: int|None = None) -> RAGIndex:
       pass

    def build_record_index(self, records: list[RAGRecord], embedding: EmbeddingHandler | None = None) -> RAGIndex:
        """Build an index of short, id-addressable records (e.g. memories).

        Records are indexed as single units without chunking. Handlers should
        override this to provide real similarity scores in query_scored.

        Args:
            records: records to index
            embedding: embedding handler to use, defaults to the one of the RAG handler
        """
        return GenericRecordIndex(self, records)

    def load_record_index(self, path: str, embedding: EmbeddingHandler | None = None) -> RAGIndex | None:
        """Load a record index persisted with RAGIndex.persist.

        Returns:
            The loaded index, or None if persistence is not supported or nothing is saved
        """
        return None

    @abstractmethod 
    def index_exists(self) -> bool:
        """
        Check if the index of user's documents exists

        Returns:
            True if the index exists 
        """
        return False

    @abstractmethod 
    def delete_index(self):
        """
        Delete the index of user's documents"""
        pass 

    @abstractmethod 
    def indexing_status(self) -> float:
        """Get the percentage of the indexing of the user's documents

        Returns:
            The percentage of the indexing 
        """
        return 0

    @abstractmethod 
    def create_index(self, button=None):
        """
        Create the index of user's documents

        Args:
            button (): 
        """
        pass 

    def index_button_pressed(self, button=None):
        """Triggered when the index or delete button is pressed

        Args:
            button (): 
        """
        if self.index_exists():
            self.delete_index() 
            self.settings_update()
        else:
            self.indexing = True
            self.create_index()

    def get_tools(self) -> list:
        return []
