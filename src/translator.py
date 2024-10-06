from abc import abstractmethod
from urllib.parse import urlencode
import urllib
import json, os, requests
from subprocess import check_output
from typing import Any
from abc import abstractmethod
from .extra import find_module, install_module
import threading
from .handler import Handler

class TranslatorHandler(Handler):
    key = ""
    schema_key = "translator-settings"    
    
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

    def translate(self, text: str) -> str:
        command = self.get_setting("command")
        if command is not None:
            value = check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", text)])
            return value.decode("utf-8")
        return text

class LibreTranslateHandler(TranslatorHandler):
    key = "LibreTranslate" 
    
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.languages = tuple()
        languages = self.get_setting("languages")
        if languages is not None and len(languages) > 0:
            self.languages = languages
        else:
            self.languages = tuple()
        if len(self.languages) == 0:
            threading.Thread(target=self.get_languages).start()

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": "API Endpoint",
                "description": "URL of LibreTranslate API endpoint",
                "type": "entry",
                "default": "https://lt.dialectapp.org",
            },
            {
                "key": "api_key",
                "title": "API key",
                "description": "Your API key",
                "type": "entry",
                "default": "",
            },
            {
                "key": "language",
                "title": "Destination language",
                "description": "The language you want to translate to",
                "type": "combo",
                "values": self.languages,
                "default": "ja",
            }
        ]

    def get_languages(self):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        r = requests.get(endpoint + "/languages", timeout=10)
        if r.status_code == 200:
            js = r.json()
            result = tuple()
            for language in js[0]["targets"]:
                result += ((language, language), )
            self.languages = result
            self.set_setting("languages", self.languages)
            return result
        else:
            return tuple()
    

    def translate(self, text: str) -> str:
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        language = self.get_setting("language")
        api = self.get_setting("api_key")
        response = requests.post(
            endpoint + "/translate",
            json={
                "q": text,
                "source": "auto",
                "target": language,
                "format": "text",
                "alternatives": 3,
                "api_key": api
            },
            headers={"Content-Type": "application/json"}
        )
        if response.status_code != 200:
            return text
        return response.json()["translatedText"]

    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            threading.Thread(target=self.get_languages).start()

class LigvaTranslateHandler(TranslatorHandler):
    key = "LigvaTranslate" 
    
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.languages = tuple()
        languages = self.get_setting("languages")
        if languages is not None and len(languages) > 0:
            self.languages = languages
        else:
            self.languages = tuple()
        if len(self.languages) == 0:
            threading.Thread(target=self.get_languages).start()

    def get_extra_settings(self) -> list:
        return [
            { "key": "endpoint", "title": "API Endpoint",
                "description": "URL of Ligva API endpoint",
                "type": "entry",
                "default": "https://lingva.dialectapp.org",
            },
            {
                "key": "language",
                "title": "Destination language",
                "description": "The language you want to translate to",
                "type": "combo",
                "values": self.languages,
                "default": "ja",
            }
        ]

    def get_languages(self):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        r = requests.get(endpoint + "/api/v1/languages/", timeout=10)
        if r.status_code == 200:
            js = r.json()
            result = tuple()
            for language in js["languages"]:
                result += ((language["name"], language["code"]), )
            self.languages = result
            self.set_setting("languages", self.languages)
            return result
        else:
            return tuple()
    

    def translate(self, text: str) -> str:
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        language = self.get_setting("language")
        response = requests.get(
            endpoint + "/api/v1/auto/" + urllib.parse.quote(language) + "/" + urllib.parse.quote(text),
        )
        if response.status_code != 200:
            return text
        return response.json()["translation"]

    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            threading.Thread(target=self.get_languages).start()

