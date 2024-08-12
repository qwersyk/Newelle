from abc import abstractmethod
import json, os
from subprocess import check_output
from typing import Any
from abc import abstractmethod
from .extra import find_module, install_module


class TranslatorHandler():
    key = ""
    
    def __init__(self, settings, path: str):
        self.settings = settings
        self.path = path

    def is_installed(self) -> bool:
        return True

    def install(self):
        pass

    
    @staticmethod
    def requires_sandbox_escape() -> bool:
        return False

    def get_extra_requirements(self) -> list:
        return []

    def get_extra_settings(self) -> list:
        return []

    def set_setting(self, setting, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("translator-settings"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("translator-settings", json.dumps(j))

    def get_setting(self, name) -> Any:
        """Get setting from key"""
        j = json.loads(self.settings.get_string("translator-settings"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        """Get the default setting from a key"""
        for x in self.get_extra_settings():
            if x["key"] == name:
                return x["default"]
        return None

    @abstractmethod
    def translate(self, text: str) -> str:
        return text

class GoogleTranslatorHandler(TranslatorHandler):
    key = "GoogleTranslator"
    
    def is_installed(self) -> bool:
        return find_module("googletranslate") is not None

    def install (self):
        install_module("git+https://github.com/ultrafunkamsterdam/googletranslate", os.path.join(self.path, "pip"))

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "language",
                "title": "Destination language",
                "description": "The language you want to translate to",
                "type": "combo",
                "values": self.get_languages(),
                "default": "ja",
            }
        ]

    def get_languages(self):
        if not self.is_installed():
            return []
        from googletranslate import LANG_CODE_TO_NAME
        result = []
        for lang_code in LANG_CODE_TO_NAME:
            result.append((LANG_CODE_TO_NAME[lang_code], lang_code))
        return result

    def translate(self, text: str) -> str:
        from googletranslate import translate
        dest = self.get_setting("language")
        return translate(text, dest)

class CustomTranslatorHandler(TranslatorHandler):
    key="CustomTranslator"

 
    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self) -> list:
        return [{
            "key": "command",
            "title": _("Command to execute"),
            "description": _("{0} will be replaced with the text to translate"),
            "type": "entry",
            "default": ""
        }]

    def is_installed(self):
        return True

    def play_audio(self, message):
        command = self.get_setting("command")
        if command is not None:
            value = check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", message)])
            return value.decode("utf-8")
