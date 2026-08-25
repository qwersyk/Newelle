from ..handler import Handler
from ..extra_settings import ExtraSettings
from abc import abstractmethod
from gettext import gettext as _
from numpy import ndarray
from typing import Literal


EmbeddingPurpose = Literal["query", "document", None]

class EmbeddingHandler(Handler):
    key = ""
    schema_key = "embedding-settings"
    default_query_prefix = ""
    default_document_prefix = ""


    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.dim = None 

    def load_model(self):
        """Load embedding model, called at every settings reload"""
        pass 

    @abstractmethod 
    def get_embedding(self, text: list[str], purpose: EmbeddingPurpose = None) -> ndarray:
        """
        Get the embedding for the given text

        Args:
            text: text to embed
            purpose: whether the text is a search query, a document, or neither.
                Query and document prefixes are only applied when a purpose is set.

        Returns:
            ndarray: embedding 
        """
        pass

    def get_prefix_settings(self) -> list:
        """Return editable prefix settings for handlers that support arbitrary models."""
        return [
            ExtraSettings.EntrySetting(
                "query_prefix",
                _("Query Prefix"),
                _("Text added before search queries."),
                self.default_query_prefix,
            ),
            ExtraSettings.EntrySetting(
                "document_prefix",
                _("Document Prefix"),
                _("Text added before documents when they are indexed. Leave empty if the model does not require one."),
                self.default_document_prefix,
            ),
        ]

    def _prepare_embedding_texts(
        self,
        text: list[str] | str,
        purpose: EmbeddingPurpose = None,
    ) -> list[str] | str:
        """Apply the configured prefix for an internal embedding operation."""
        if purpose is None:
            return text
        if purpose not in ("query", "document"):
            raise ValueError("Embedding purpose must be 'query', 'document', or None")

        default = getattr(self, f"default_{purpose}_prefix")
        prefix = self.get_setting(
            f"{purpose}_prefix",
            search_default=False,
            return_value=default,
        )
        if prefix is None:
            prefix = default

        if isinstance(text, str):
            return f"{prefix}{text}"
        return [f"{prefix}{item}" for item in text]

    def get_embedding_size(self) -> int:
        if self.dim is None:
            self.dim = self.get_embedding(["test"]).shape[1]
        return self.dim
