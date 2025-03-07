from .openai_handler import OpenAIEmbeddingHandler
import numpy as np
from ...utility.pip import find_module


class GeminiEmbeddingHanlder(OpenAIEmbeddingHandler):

    key = "geminiembedding"
    models = (("text-embedding-004", "text-embedding-004"), )

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://generativelanguage.googleapis.com/v1beta/openai/")

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Gemini", True, False, True, None, True)

    def get_embedding_size(self) -> int:
        return 768
