import gettext
import json
import threading

import requests

from .ollama_handler import OllamaHandler
from ...utility import get_streaming_extra_setting
from ...handlers import ExtraSettings
from ..handler import ErrorSeverity

_ = gettext.gettext


class OllamaCloudHandler(OllamaHandler):
    key = "ollama_cloud"
    default_models = (("gpt-oss:120b", "gpt-oss:120b"),)

    def __init__(self, settings, path):
        super().__init__(settings, path)
        if self.get_setting("endpoint", False) is None:
            self.set_setting("endpoint", "https://ollama.com")
        models = self.get_setting("models", False)
        if models is None or len(models) == 0:
            self.models = self.default_models
            threading.Thread(target=self.get_models, args=(False,)).start()
        else:
            self.models = json.loads(models)
    def load_model(self, model):
        return True
    def get_client_headers(self) -> dict[str, str]:
        api_key = self.get_setting("api", False, "")
        if api_key is None or api_key.strip() == "":
            return {}
        return {"Authorization": "Bearer " + api_key.strip()}

    def _get_tags_url(self) -> str:
        endpoint = self.get_setting("endpoint", False, "https://ollama.com")
        return endpoint.rstrip("/") + "/api/tags"

    def get_models(self, manual: bool = False):
        headers = self.get_client_headers()
        try:
            response = requests.get(self._get_tags_url(), headers=headers, timeout=15)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            result = tuple()
            for model in models:
                name = model.get("name")
                if name is not None and len(name) > 0:
                    result += ((name, name),)
            if len(result) == 0:
                result = self.default_models
            self.models = result
            self.set_setting("models", json.dumps(result))
            self.settings_update()
        except Exception as e:
            if manual:
                self.throw("Error getting Ollama Cloud models: " + str(e), ErrorSeverity.WARNING)

    def get_extra_settings(self) -> list:
        default = self.models[0][1] if len(self.models) > 0 else self.default_models[0][0]
        settings = [
            ExtraSettings.EntrySetting("api", _("API Key"), _("Ollama Cloud API key"), "", password=True),
            ExtraSettings.EntrySetting("endpoint", _("API Endpoint"), _("Ollama Cloud endpoint"), "https://ollama.com"),
            ExtraSettings.ToggleSetting("custom_model", _("Custom Model"), _("Use a custom model"), False, update_settings=True),
        ]

        if not self.get_setting("custom_model", False):
            settings.append(
                ExtraSettings.ComboSetting(
                    "model",
                    _("Ollama Cloud Model"),
                    _("Name of the Ollama Cloud model"),
                    self.models,
                    default,
                    refresh=lambda x: self.get_models(manual=True),
                )
            )
        else:
            settings.append(
                ExtraSettings.EntrySetting("model", _("Ollama Cloud Model"), _("Name of the Ollama Cloud model"), default)
            )

        settings += [
            ExtraSettings.ToggleSetting("thinking", _("Enable Thinking"), _("Allow thinking in the model, only some models are supported"), True, website="https://ollama.com/search?c=thinking"),
            ExtraSettings.ToggleSetting("native_tool_calling", _("Native Tool Calling"), _("Enable native tool calling (Will use API's tool calling formatting instead of Newelle's. Disable only if you have issues with tool calling or the model you are using does not support it natively)"), True),
            get_streaming_extra_setting(),
        ]
        return settings
