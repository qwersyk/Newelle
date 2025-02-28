from .embedding import EmbeddingHandler
from ...handlers import ExtraSettings
import numpy as np 

class OpenAIEmbeddingHandler(EmbeddingHandler):
    key = "openaiembedding"
    models = (("text-embedding-3-small", "text-embedding-3-small"), ("text-embedding-3-large", "text-embedding-3-large"), ("text-embedding-ada-002", "text-embedding-ada-002"))
    def __init__(self, settings, path):
        super().__init__(settings, path)

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    def get_models(self):
        return []

    def build_extra_settings(self, provider_name: str, has_api_key: bool, endpoint_change: bool, supports_automatic_models: bool, model_list_url: str | None, default_automatic_models: bool = False) -> list:
        """Helper to build the list of extra settings for OpenAI Handlers

        Args:
            provider_name: name of the provider, it is stated in model settings 
            has_api_key: if to show the api key setting
            has_stream_settings: if to show the message streaming setting
            endpoint_change: if to allow the endpoint change 
            allow_advanced_params: if to allow advanced parameters like temperature ... 
            supports_automatic_models: if it supports automatic model fetching 
            privacy_notice_url: the url of the privacy policy, None if not stated
            model_list_url: human accessible page that lists the available models

        Returns:
            list containing the extra settings
        """
        api_settings = [ 
            ExtraSettings.EntrySetting("api", _("API Key"), _("API Key for " + provider_name), ""),
        ]
        endpoint_settings = [
            ExtraSettings.EntrySetting("endpoint", _("API Endpoint"), _("API base url, change this to use different APIs"), "https://api.openai.com/v1/"),
        ]
        custom_model = [
            ExtraSettings.ToggleSetting("custom_model", _("Use Custom Model"), _("Use a custom model"), False)
        ]
        models_settings = [ 
            ExtraSettings.EntrySetting("model", _("Model"), _("Name of the Embedding Model to use"), self.models[0][0]),
        ]
        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        automatic_models_settings = [
            ExtraSettings.ComboSetting(
                    "model",
                    _(provider_name + " Model"),
                    _(f"Name of the {provider_name} Model"),
                    self.models,
                    self.models[0][0],
                    refresh=lambda button: self.get_models(),
                )
        ]

        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        
        settings = []
        if has_api_key:
            settings += (api_settings)
        if endpoint_change:
            settings += (endpoint_settings)
        if supports_automatic_models:
            settings += (custom_model)
            custom = self.get_setting("custom_model", False)
            if (custom is None and not default_automatic_models) or custom:
                settings += models_settings
            else:
                settings += automatic_models_settings
        return settings
    def get_extra_settings(self) -> list:
        return self.build_extra_settings("OpenAI", True, True, True, "https://platform.openai.com/docs/guides/embeddings#embedding-models", True)

    def get_embedding(self, text: list[str]) -> np.ndarray:
        from openai import Client
        client = Client(api_key=self.get_setting("api"), base_url=self.get_setting("endpoint"))
        embedding = client.embeddings.create(
            input=text,
            model=self.get_setting("model")
        )
        res = []
        for emb in embedding.data: 
            res.append(emb.embedding)
        return np.array(res)
        
    def get_embedding_size(self) -> int:
        model = self.get_setting("model")
        if model == "text-embedding-3-small":
            return 1536
        elif model == "text-embedding-3-large":
            return 3072
        elif model == "text-embedding-ada-002":
            return 1536
        else:
            return len(self.get_embedding([""]))

