from abc import abstractmethod
from os.path import abspath, isdir, isfile
from typing import Any
from gi.repository import Gtk, WebKit, GLib, GdkPixbuf
from livepng.model import Semaphore

from .extra import rgb_to_hex
from .tts import TTSHandler
import os, subprocess, threading, json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from livepng import LivePNG
from livepng.validator import ModelValidator
from livepng.constants import FilepathOutput
from pydub import AudioSegment
from time import sleep
from urllib.parse import urlencode, urljoin

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

    def get_extra_settings(self) -> list:
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
        self.settings.set_string("avatars", json.dumps(j))

    def get_setting(self, name, search_default: bool = True) -> Any:
        """Get setting from key"""
        j = json.loads(self.settings.get_string("avatars"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            if search_default:
                return self.get_default_setting(name)
            else:
                return None
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
    def get_expressions(self) -> list[str]:
        """Get the list of possible expressions"""
        pass

    @abstractmethod
    def speak_with_tts(self, text: str, tts : TTSHandler):
        pass

    def destroy(self, add=None):
        pass

class Live2DHandler(AvatarHandler):
    key = "Live2D"
    _wait_js : threading.Event
    _expressions_raw : list[str]
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self._wait_js = threading.Event()
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")
        self.models_dir = os.path.join(self.webview_path, "models")

    def get_available_models(self): 
        file_list = []
        for root, _, files in os.walk(self.models_dir):
            for file in files:
                if file.endswith('.model3.json'):
                    file_name = file.rstrip('.model3.json')
                    relative_path = os.path.relpath(os.path.join(root, file), self.models_dir)
                    file_list.append((file_name, relative_path))
        return file_list

    def get_extra_settings(self) -> list:
        widget = Gtk.Box()
        color = widget.get_style_context().lookup_color('window_bg_color')[1]
        default = rgb_to_hex(color.red, color.green, color.blue)

        return [ 
            {
                "key": "model",
                "title": _("Live2D Model"),
                "description": _("Live2D Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "Arch/arch chan model0.model3.json",
                "folder": os.path.abspath(self.models_dir)
            },
            {
             "key": "fps",
                "title": _("Lipsync Framerate"),
                "description": _("Maximum amount of frames to generate for lipsync"),
                "type": "range",
                "min": 5,
                "max": 30,
                "default": 10,
                "round-digits": 0
            },
            {
                "key": "background-color",
                "title": _("Background Color"),
                "description": _("Background color of the avatar"),
                "type": "entry",
                "default": default,
            }
        ]
    def is_installed(self) -> bool:
        return os.path.isdir(self.webview_path)

    def install(self):
        subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/live2d-lipsync-viewer.git", self.webview_path])
        subprocess.check_output(["wget", "-P", os.path.join(self.models_dir), "http://mirror.nyarchlinux.moe/Arch.tar.xz"])
        subprocess.check_output(["tar", "-Jxf", os.path.join(self.models_dir, "Arch.tar.xz"), "-C", self.models_dir])
        subprocess.Popen(["rm", os.path.join(self.models_dir, "Arch.tar.xz")])
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
        model = self.get_setting("model")
        background_color = self.get_setting("background-color")
        q = urlencode({"model": model, "bg": background_color})
        GLib.idle_add(self.webview.load_uri, urljoin("http://localhost:" + str(httpd.server_address[1]), f"?{q}"))
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

    def destroy(self, add=None):
        self.httpd.shutdown()
        self.webview = None

    def wait_emotions(self, object, result):
        value = self.webview.evaluate_javascript_finish(result)
        self._expressions_raw = json.loads(value.to_string())
        self._wait_js.set()

    def get_expressions(self):
        self._expressions_raw = []
        script = "get_expressions_json()"
        self.webview.evaluate_javascript(script, len(script), callback=self.wait_emotions)
        self._wait_js.wait(3)   
        return self._expressions_raw 

    def speak_with_tts(self, text: str, tts: TTSHandler):
        frame_rate = int(self.get_setting("fps"))
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


class LivePNGHandler(AvatarHandler):
    key = "LivePNG"
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.models_path = os.path.join(path, "avatars", "livepng", "models")
        if not os.path.isdir(self.models_path):
            os.makedirs(self.models_path)
    
    def get_extra_settings(self) -> list:
        styles, default = self.get_styles_list() 
        return [ 
            {
                "key": "model",
                "title": _("LivePNG Model"),
                "description": _("LivePNG Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "arch-chan",
                "folder": os.path.abspath(self.models_path),
                "update_settings": True
            },
            {
             "key": "fps",
                "title": _("Lipsync Framerate"),
                "description": _("Maximum amount of frames to generate for lipsync"),
                "type": "range",
                "min": 5,
                "max": 30,
                "default": 10,
                "round-digits": 0
            },
            {
                "key": "style",
                "title": _("LivePNG model style"),
                "description": _("Choose the style of the model for the specified one"),
                "type": "combo",
                "values": styles,
                "default": default
            }
        ]       
    def get_styles_list(self) -> tuple[list, str]:
        path = self.get_setting("model", False)
        if not type(path) is str:
            return ([], "")
        try:
            self.model = LivePNG(path, output_type=FilepathOutput.LOCAL_PATH)
        except Exception as e:
            return tuple()
        return ([(style, style) for style in self.model.get_styles()], self.model.get_default_style().name)
    
    def get_available_models(self) -> list[tuple[str, str]]:
        dirs = os.listdir(self.models_path)
        result = []
        for dir in dirs:
            if not os.path.isdir(os.path.join(self.models_path, dir)):
                continue
            jsonpath = os.path.join(self.models_path, dir, "model.json")
            if not os.path.isfile(jsonpath):
                continue
            try:
                model = LivePNG(jsonpath)
                result.append((model.get_name(), jsonpath))
            except Exception as e:
                print(e)
        return result

    def create_gtk_widget(self) -> Gtk.Widget:
        self.image = Gtk.Picture()
        self.image.set_vexpand(True)
        self.image.set_hexpand(True)
        self.__load_model()
        return self.image

    def speak_with_tts(self, text: str, tts: TTSHandler):
        frame_rate = int(self.get_setting("fps"))
        filename = tts.get_tempname("wav")
        tts.save_audio(text, filename)

        # Calculate frames
        t1 = threading.Thread(target=self.model.speak, args=(filename, True, False, frame_rate, True, False))
        t2 = threading.Thread(target=tts.playsound, args=(filename, ))
        t2.start()
        t1.start()

    def __load_model(self):
        path = self.get_setting("model")
        if not type(path) is str:
            return
        self.model = LivePNG(path, output_type=FilepathOutput.LOCAL_PATH)
        self.model.set_current_style(self.get_setting("style"))
        t = threading.Thread(target=self.preacache_images)
        t.start()
        self.model.subscribe_callback(self.__on_update)
        self.__on_update(self.model.get_current_image())

    def __on_update(self, frame:str):
        if frame in self.cachedpixbuf:
            GLib.idle_add(self.image.set_pixbuf, self.cachedpixbuf[frame])
        else:
            GLib.idle_add(self.image.set_pixbuf, self.__load_image(frame))

    def preacache_images(self):
        self.cachedpixbuf = {}
        for image in self.model.get_images_list():
            self.cachedpixbuf[image] = self.__load_image(image)

    def get_expressions(self) -> list[str]:
        return [expression for expression in self.model.get_expressions()]
        
    def __load_image(self, image):
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(filename=image, width=2000,height=-1, preserve_aspect_ratio=True )

    def is_installed(self) -> bool:
        return len(self.get_available_models()) > 0

    def set_setting(self, setting, value):
        """Overridden version of set_setting that also updates the default style setting when the model is changed"""
        super().set_setting(setting, value)
        if setting == "model":
            self.set_setting("style", self.get_styles_list()[1])

