from abc import abstractmethod
from collections.abc import Callable
from ...handlers import Handler


class WebSearchHandler(Handler):
    schema_key = "websearch-settings"
    
    @abstractmethod
    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        """Return the result for a query and the sources

        Args:
            keywords: the query 
            max_results: the max number of results to return

        Returns:
            - str: the text to send to the LLM 
            - list: the list of sources (URL)
        """
        return "", []

    def supports_streaming_query(self) -> bool:
        return False

    @abstractmethod
    def query_streaming(self,keywords: str, add_website: Callable, max_results: int = None) -> tuple[str, list]:
        """Return the result for a query in streaming mode

        Args:
            keywords: the query 
            add_website: the function to add a website, takes (title, link, favicon_path) 
            max_results: the max number of results to return

        Returns:
            - str: the text to send to the LLM
            - list: the list of sources (URL)
        """
        return "", []
