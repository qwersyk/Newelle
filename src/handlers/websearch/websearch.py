from abc import abstractmethod
from ...handlers import Handler


class WebSearchHandler(Handler):
    schema_key = "websearch-settings"
    
    @abstractmethod
    def query(self, keywords: str) -> str:
        return ""
