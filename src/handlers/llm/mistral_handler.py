from .openai_handler import OpenAIHandler

class MistralHandler(OpenAIHandler):
    key = "mistral"
    default_models = (("open-mixtral-8x7b", "open-mixtral-8x7b"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.mistral.ai/v1/")
        self.set_setting("advanced_params", False)

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Mistral", True, True, False, True, True, None, "https://docs.mistral.ai/getting-started/models/models_overview/", False, True)
