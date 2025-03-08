import numpy as np
from .embedding import EmbeddingHandler
from ...handlers import ExtraSettings

class WordLlamaHandler(EmbeddingHandler):
    key = "wordllama"


    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.wl = None

    @staticmethod
    def get_extra_requirements() -> list:
        return ["wordllama"]    
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ComboSetting("model_size", "Model Size", "Size of the embedding", ["128", "256", "512", "1024"],"512"),
        ]

    def load_model(self):
        if not self.is_installed():
            return
        if self.wl is not None:
            return
        from wordllama import WordLlama
        size = self.get_embedding_size()
        if (size >= 256):
            self.wl = WordLlama.load(dim=size)
        else:
            self.wl = WordLlama.load(truncate_dim=size)

    def get_embedding(self, text: list[str]) -> np.ndarray:
        if self.wl is not None:
            return self.wl.embed(text)
        else:
            return np.array([])

    def get_embedding_size(self) -> int:
        return int(self.get_setting("model_size"))
