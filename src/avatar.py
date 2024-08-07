from abc import abstractmethod
from gi.repository import Gtk, WebKit, GLib
from .tts import TTSHandler
import os, subprocess, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

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

    def is_installed() -> bool:
        return True

    def install(self) -> bool:
        return True

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

    def destroy(self):
        pass

class Live2DHandler(AvatarHandler):

    key = "Live2D"

    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")

    def is_installed(self):
        return os.path.isdir(self.webview_path)

    def install(self):
        out = subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/live2d-lipsync-viewer.git", self.webview_path])

    def __start_webserver(self):
        folder_path = self.webview_path
        class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                # Get the default translate path
                path = super().translate_path(path)
                # Replace the default directory with the specified folder path
                return os.path.join(folder_path, os.path.relpath(path, os.getcwd()))
        self.httpd = HTTPServer(('localhost', 0), CustomHTTPRequestHandler)
        httpd = self.httpd
        GLib.idle_add(self.webview.load_uri, "http://localhost:" + str(httpd.server_address[1]))
        httpd.serve_forever()

    def create_gtk_widget(self) -> Gtk.Widget:
        self.webview = WebKit.WebView()
        self.webview.connect("destroy", self.destroy)
        threading.Thread(target=self.__start_webserver).start()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_webaudio(True)
        settings.set_media_playback_requires_user_gesture(False)
        self.webview.set_is_muted(False)
        self.webview.set_settings(settings)
        return self.webview

    def destroy(self):
        self.httpd.shutdown()

    def get_emotions(self):
        return []

    def speak(self, text, reproduce_audio):
        return

    def speak_with_tts(self, text, tts):
        return []
