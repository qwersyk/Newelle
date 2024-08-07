from abc import abstractmethod
from gi.repository import Gtk
from .tts import TTSHandler

class AvatarHandler:

    key : str = ""

    def __init__(self, settings, path: str):
        self.settings = settings
        self.path = path

    @staticmethod
    def support_emotions() -> bool:
        return False

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return False

    @staticmethod
    def get_extra_settings() -> list:
        """Get extra settings for the TTS"""
        return []

    @staticmethod
    def get_extra_requirements() -> list:
        """Get the extra requirements for the tts"""
        return []

    def set_setting(self, setting, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("avatars"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("tts-voice", json.dumps(j))

    def get_setting(self, name):
        """Get setting from key"""
        j = json.loads(self.settings.get_string("avatars"))
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
    def create_gtk_widget(self) -> Gtk.Widget:
        """Create a GTK Widget to display the avatar"""
        pass

    @abstractmethod
    def get_emotions(self) -> list[str]:
        """Get the list of possible emotions"""
        pass

    @staticmethod
    def speak(self, text: str, reproduce_audio: bool = False):
        pass

    @staticmethod
    def speak_with_tts(self, text: str, tts : TTSHandler):
        pass

class Live2DHandler(AvatarHandler):
    def get_emotions(self):
        return []

    def speak(self, text, reproduce_audio):
        return

    def speak_with_tts(self, text, tts):
        return []
