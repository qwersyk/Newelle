from ...handlers.embeddings import EmbeddingHandler
from ...handlers import ExtraSettings

class Model2VecHandler(EmbeddingHandler):
    key="model2vec"
    @staticmethod
    def get_extra_requirements() -> list:
        return ["model2vec"]

    def get_models(self):
        models = [
            "minishlab/potion-base-32M",
            "minishlab/potion-multilingual-128M",
            "minishlab/potion-retrieval-32M",
            "minishlab/potion-base-8M",
            "minishlab/potion-base-4M",
            "minishlab/potion-base-2M"
        ]
        return models
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ComboSetting("model", "Model", "Model to use", self.get_models(), "minishlab/potion-base-8M")
        ]

    def load_model(self):
        from model2vec import StaticModel
        self.model = StaticModel.from_pretrained(self.get_setting("model"))

    def get_embedding(self, text: list[str]):
        if not hasattr(self, "model"):
            self.load_model()
        embeddings = self.model.encode(text)
        return embeddings
