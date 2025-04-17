from .openai_handler import OpenAIHandler


class GroqHandler(OpenAIHandler):
    key = "groq"
    default_models = (("llama-3.3-70B-versatile", "llama-3.3-70B-versatile" ), ) 
    
    def supports_vision(self) -> bool:
        return any(x in self.get_setting("model") for x in ["llama-4", "vision"])

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Groq", True, True, False, False, True, "https://groq.com/privacy-policy/", "https://console.groq.com/docs/models", False, True)

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        # Remove system prompt if history contains image prompt
        # since it is not supported by groq
        h = super().convert_history(history, prompts)
        contains_image = False
        for message in h:
            if type(message["content"]) is list:
                if any(content["type"] == "image_url" for content in message["content"]):
                    contains_image = True
                    break
        if contains_image and (prompts is None or len(prompts) > 0):
            h.pop(0)
        return h

