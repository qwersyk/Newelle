from abc import abstractmethod
from gi.repository import Gtk, WebKit, GLib
from .tts import TTSHandler
import os, subprocess, threading, json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from livepng import LivePNG
from pydub import AudioSegment
from time import sleep

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

    def is_installed(self) -> bool:
        return True

    def install(self):
        pass

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

    @abstractmethod
    def speak_with_tts(self, text: str, tts : TTSHandler):
        pass

    def destroy(self):
        pass

class Live2DHandler(AvatarHandler):

    key = "Live2D"

    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
             "key": "fps",
                "title": _("Lipsync Framerate"),
                "description": _("Maximum amount of frames to generate for lipsynv"),
                "type": "range",
                "min": 5,
                "max": 30,
                "default": 10,
                "round-digits": 0
            }
        ]
    def is_installed(self) -> bool:
        return os.path.isdir(self.webview_path)

    def install(self):
        subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/live2d-lipsync-viewer.git", self.webview_path])

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

    def speak_with_tts(self, text: str, tts: TTSHandler):
        frame_rate = self.get_setting("fps")
        filename = tts.get_tempname("wav")
        tts.save_audio(text, filename)

        audio = AudioSegment.from_file(filename)
        # Calculate frames
        sample_rate = audio.frame_rate
        audio_data = audio.get_array_of_samples()
        amplitudes = LivePNG.calculate_amplitudes(sample_rate, audio_data, frame_rate)
        t1 = threading.Thread(target=self._start_animation, args=(amplitudes, frame_rate))
        t2 = threading.Thread(target=tts.playsound, args=(filename, ))
        t2.start()
        t1.start()
        
    def _start_animation(self, amplitudes: list[float], frame_rate=10):
        for amplitude in amplitudes:
            self.set_mouth(amplitude*8.8)
            sleep(1/frame_rate)

    def set_mouth(self, value):
        script = "set_mouth_y({})".format(value)
        self.webview.evaluate_javascript(script, len(script))


