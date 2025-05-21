import threading
import base64
from typing import Any, Callable

from .llm import LLMHandler
from ...utility import convert_history_openai, get_streaming_extra_setting
from ...utility.media import extract_image, extract_file, get_image_base64
from ...handlers import ExtraSettings

class ClaudeHandler(LLMHandler):
    key = "claude"
    default_models = (("claude-3-opus-latest", "claude-3-opus-latest"), ("claude-3-5-sonnet-latest", "claude-3-5-sonnet-latest") )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        models = self.get_setting("models", False)
        if models is None or len(models) == 0:
            self.models = self.default_models
            threading.Thread(target=self.get_models, args=()).start()
        else:
            self.models = models

    def get_supported_files(self) -> list[str]:
        return ["*.pdf"]

    def supports_vision(self) -> bool:
        return True 

    def get_models_list(self):
        return self.models

    def convert_history(self, history) -> list:
        base_history = convert_history_openai(history, [], False)
        if self.supports_vision():
            for message in base_history:
                if message["role"] == "user":
                    image, text = extract_image(message["content"])
                    if image is not None:
                        message["content"] = []
                        message["content"].append({
                            "type": "text",
                            "text": text
                        })
                        b64 = get_image_base64(image)
                        format = b64.split(";")[0].split(":")[1]
                        image = b64.split(";")[1].split(",")[1]
                        message["content"].append({
                            "type": "image",
                            "source": {"type": "base64", "media_type" : format, "data": image}
                        })
                    else:
                        file, text = extract_file(message["content"])
                        if file is not None:
                            message["content"] = []
                            message["content"].append({
                                "type": "text",
                                "text": text
                            })
                            with open(file, "rb") as pdf_file:
                                pdf_data = base64.standard_b64encode(pdf_file.read()).decode("utf-8")    
                            message["content"].append({
                                "type": "document",
                                "source": {"type": "base64", "media_type" : "application/pdf", "data": pdf_data}
                            })
        return base_history
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["anthropic"]

    def get_models(self):
        if not self.is_installed() or self.get_setting("api", False) == "" or self.get_setting("endpoint", False) is None:
            return
        import anthropic
        client = anthropic.Client(api_key=self.get_setting("api"))
        result = tuple()
        for model in client.models.list():
            result += ((model.display_name, model.id,), )

        self.models = result
        self.set_setting("models", result)

    def get_extra_settings(self) -> list:
        settings = [
            ExtraSettings.EntrySetting("api", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.ToggleSetting("custom_model", _("Use a custom model"), _("Use a custom model"), False, update_settings=True),
        ]
        if self.get_setting("custom_model", False):
            settings.append(
                ExtraSettings.EntrySetting("model", _("Model"), _("The model to use"),"")
            )
        else:
            settings.append(
                ExtraSettings.ComboSetting("model", _("Model"), _("The model to use"), self.models, self.models[0][1], refresh= lambda x : self.get_models())
            )
        settings.append(
            ExtraSettings.ScaleSetting("max_tokens", _("Max Tokens"), _("The maximum number of tokens to generate"), 1024, 100, 8912, 0)
        )
        settings.append(get_streaming_extra_setting())
        return settings

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        import anthropic
        client = anthropic.Client(api_key=self.get_setting("api"))
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history)
        response = client.messages.create(
            max_tokens=int(self.get_setting("max_tokens")),
            model=self.get_setting("model"),
            messages=messages,
            system="\n".join(system_prompt)
        )

        return response.content[0].text

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        import anthropic
        client = anthropic.Client(api_key=self.get_setting("api"))
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history)
        with client.messages.stream(
            max_tokens=int(self.get_setting("max_tokens")),
            model=self.get_setting("model"),
            messages=messages,
            system="\n".join(system_prompt)
        ) as stream:
            full_message = ""
            prev_message = ""
            for text in stream.text_stream:
                if len(full_message) - len(prev_message) > 1: 
                    args = (full_message.strip(), ) + tuple(extra_args)
                    on_update(*args)
                    prev_message = full_message
                full_message += text
            return full_message.strip()

