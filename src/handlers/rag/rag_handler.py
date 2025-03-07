from ...handlers import Handler
from ...handlers.embeddings import EmbeddingHandler
from ...handlers.llm import LLMHandler
from ...handlers import ExtraSettings
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
    
    def get_supported_files(self) -> list:
        return []

    @abstractmethod 
    def load(self):
        pass 

    @abstractmethod
    def get_context(self, prompt:str, history: list[dict[str, str]]) -> list[str]:
        return []

    @abstractmethod
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
        pass
    
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


