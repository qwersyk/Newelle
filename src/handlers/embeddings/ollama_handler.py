from .embedding import EmbeddingHandler
from ...handlers import ExtraSettings
from ...utility.system import get_spawn_command, can_escape_sandbox  
import threading
import json
import numpy as np
import time 
from subprocess import Popen

class OllamaEmbeddingHandler(EmbeddingHandler):
    key = "ollamaembedding"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        models = self.get_setting("models", False)
        if models is None or len(models) == 0:
            self.models = ()
            threading.Thread(target=self.get_models, args=()).start()
        else:
            self.models = json.loads(models)

    def get_extra_settings(self) -> list:
        default = self.models[0][1] if len(self.models) > 0 else ""
        settings = [
            ExtraSettings.EntrySetting("endpoint", _("API Endpoint"), _("API base url, change this to use interference APIs"), "http://localhost:11434"),
            ExtraSettings.ToggleSetting("serve", _("Automatically Serve"), _("Automatically run ollama serve in background when needed if it's not running. You can kill it with killall ollama"), False),
            ExtraSettings.ToggleSetting("custom_model", _("Custom Model"), _("Use a custom model"), False, update_settings=True),
        ]
        if not self.get_setting("custom_model", False):
            settings.append(
                ExtraSettings.ComboSetting(
                    "model",
                    _("Ollama Model"),
                    _("Name of the Ollama Model"),
                    self.models,
                    default,
                    refresh= lambda x: self.get_models(),
                )
            )
        else:
            settings.append(
                ExtraSettings.EntrySetting("model", _("Ollama Model"), _("Name of the Ollama Model"), default)
            )
        return settings
    
    def get_models(self):
        """Get the list of installed models in ollama"""
        if not self.is_installed():
            return
        from ollama import Client 
        client = Client(
            host=self.get_setting("endpoint")
        )
        self.auto_serve(client)
        try:
            models = client.list()["models"]
        except Exception as e:
            print("Can't get Ollama models: ", e)
            return
        res = tuple()
        for model in models:
            res += ((model.model, model.model), )
        self.models = res
        self.set_setting("models", json.dumps(self.models))
        self.settings_update()

    def get_embedding(self, text: list[str]) -> np.ndarray:
        from ollama import Client 
        client = Client(
            host=self.get_setting("endpoint")
        )
        self.auto_serve(client)
        arr = client.embed(model=self.get_setting("model"), input=text)
        return np.array(arr.embeddings)
    
    def auto_serve(self, client):
        """Automatically runs ollama serve on the user system if it's not running and the setting is toggles

        Args:
            client (): ollama client 
        """
        if self.get_setting("serve") and can_escape_sandbox():
            try:
                client.ps()
            except Exception as e:
                Popen(get_spawn_command() + ["ollama", "serve"])
                time.sleep(1)

