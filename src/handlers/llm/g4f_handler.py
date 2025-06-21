import os
from subprocess import check_output
from typing import Any, Callable

from .llm import LLMHandler
from ...utility import convert_history_openai 
from ...utility.pip import find_module, install_module
from ...utility.media import extract_image, get_image_path
from ...utility.util import get_streaming_extra_setting
from ...handlers import ErrorSeverity, ExtraSettings

class G4FHandler(LLMHandler):
    """Common methods for g4f models"""
    key = "g4f"
   
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.client = None 

    def get_client(self):
        if self.is_installed():
            from g4f.client import Client
            self.client = Client()

    @staticmethod
    def get_extra_requirements() -> list:
        return ["g4f"]
    
    def is_installed(self) -> bool:
        if find_module("g4f") is not None:
           return True
        return False

    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip") 
        # Remove old versions
        check_output(["bash", "-c", "rm -rf " + os.path.join(pip_path, "*g4f*")])
        install_module("g4f nodriver platformdirs", pip_path)
        if not self.is_installed():
            self.throw("g4f installation failed", ErrorSeverity.ERROR)

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ComboSetting("model", _("Model"), _("Model to use"), self.get_models_list(), "gpt-4o"),
            ExtraSettings.ButtonSetting("update", _("Update G4F"), _("Update G4F"), lambda x: self.install(), "Update G4F"),
            get_streaming_extra_setting()
        ]
    
    def get_models_list(self):
        if not self.is_installed():
            return tuple()
        from g4f.models import ModelRegistry
        models = ModelRegistry.all_models()
        self.models = tuple()
        for key, model in models.items():
            self.models += ((key, model.name), )
        return self.models

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        return convert_history_openai(history, prompts, False)
    
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        self.get_client()
        model = self.get_setting("model")
        img = None
        if self.supports_vision():
            img, message = extract_image(prompt)
        else:
            message = prompt
        if img is not None:
            img = get_image_path(img)
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=history,
                image= open(img, "rb") if img is not None else None
            )
            return response.choices[0].message.content
        except Exception as e:
            raise e
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        self.get_client()
        model = self.get_setting("model")
        img = None
        if self.supports_vision():
            img, message = extract_image(prompt)
        else:
            message = prompt
        if img is not None:
            get_image_path(img)
        model = self.get_setting("model")
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=history,
                stream=True,
                image= open(img, "rb") if img is not None else None
            )
            full_message = ""
            prev_message = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    full_message += chunk.choices[0].delta.content
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
            return full_message.strip()
        except Exception as e:
            raise e

