from abc import abstractmethod
from os.path import abspath, isdir, isfile
from gi.repository import Gtk, WebKit, GLib, GdkPixbuf
from livepng.model import Semaphore
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
    def get_expressions(self) -> list[str]:
        """Get the list of possible expressions"""
        pass

    @abstractmethod
    def speak_with_tts(self, text: str, tts : TTSHandler):
        pass

    def destroy(self):
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
        return [ 
            {
                "key": "model",
                "title": _("Live2D Model"),
                "description": _("Live2D Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "arch chan model0",
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
        model = self.get_setting("model")
        q = urlencode({"model": model})
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

    def destroy(self):
        self.httpd.shutdown()

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

    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.models_path = os.path.join(path, "avatars", "livepng", "models")
        if not os.path.isdir(self.models_path):
            os.makedirs(self.models_path)
    
    def get_extra_settings(self) -> list:
        return [ 
            {
                "key": "model",
                "title": _("LivePNG Model"),
                "description": _("LivePNG Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "arch-chan",
                "folder": os.path.abspath(self.models_path)
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
        ]       
    
    def get_available_models(self) -> list[tuple[str, str]]:
        dirs = os.listdir(self.models_path)
        result = []
        for dir in dirs:
            if not os.path.isdir(os.path.join(self.models_path, dir)):
                continue
            jsonpath = os.path.join(self.models_path, dir, "model.json")
            print(dir)
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
        print(path)
        if not type(path) is str:
            return
        self.model = LivePNG(path, output_type=FilepathOutput.LOCAL_PATH)
        t = threading.Thread(target=self.preacache_images)
        t.start()
        self.model.subscribe_callback(self.__on_update)
        print(self.model.name, self.model.get_current_image()) 
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
        
    def __load_image(self, image):
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(filename=image, width=2000,height=-1, preserve_aspect_ratio=True )

    def is_installed(self) -> bool:
        return len(self.get_available_models()) > 0
