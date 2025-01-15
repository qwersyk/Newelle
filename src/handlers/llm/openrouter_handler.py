from .openai_handler import OpenAIHandler

class OpenRouterHandler(OpenAIHandler):
    key = "openrouter"
    default_models = (("meta-llama/llama-3.1-70b-instruct:free", "meta-llama/llama-3.1-70b-instruct:free"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://openrouter.ai/api/v1/")

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("OpenRouter", True, True, False, False, True, "https://openrouter.ai/privacy", "https://openrouter.ai/docs/models", False, True)

