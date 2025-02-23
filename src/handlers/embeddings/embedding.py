from ..handler import Handler
from abc import abstractmethod
from numpy import ndarray

class EmbeddingHandler(Handler):
    key = ""
    schema_key = "embedding-settings"


    def __init__(self, settings, path):
        super().__init__(settings, path)

    def load_model(self):
        """Load embedding model, called at every settings reload"""
        pass 

    @abstractmethod 
    def get_embedding(self, text: list[str]) -> ndarray:
        """
        Get the embedding for the given text

        Args:
            text: text to embed 

        Returns:
            ndarray: embedding 
        """
        pass
