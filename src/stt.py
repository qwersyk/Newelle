from abc import abstractmethod
from subprocess import check_output
import os, sys, json
import importlib
from typing import Any
import pyaudio
import wave
import speech_recognition as sr
from .extra import find_module, install_module

class AudioRecorder:
    """Record audio"""
    def __init__(self):
        self.recording = False
        self.frames = []
        self.sample_format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 44100
        self.chunk_size = 1024

    def start_recording(self):
        self.recording = True
        self.frames = []
        p = pyaudio.PyAudio()
        stream = p.open(format=self.sample_format,
                        channels=self.channels,
                        rate=self.sample_rate,
                        frames_per_buffer=self.chunk_size,
                        input=True)
        while self.recording:
            data = stream.read(self.chunk_size)
            self.frames.append(data)
        stream.stop_stream()
        stream.close()
        p.terminate()

    def stop_recording(self, output_file):
        self.recording = False
        p = pyaudio.PyAudio()
        wf = wave.open(output_file, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(p.get_sample_size(self.sample_format))
        wf.setframerate(self.sample_rate)
        wf.writeframes(b''.join(self.frames))
        wf.close()
        p.terminate()


class STTHandler:
    """Every STT Handler should extend this class"""
    key = ""
    def __init__(self, settings, pip_path):
        self.settings = settings
        self.pip_path = pip_path

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return False

    @staticmethod
    def get_extra_requirements() -> list:
        """Return the list of extra requirements"""
        return []

    @staticmethod
    def get_extra_settings() -> list:
        """Return the list of extra settings"""
        return []

    def install(self):
        """Install the required extra dependencies"""
        for module in self.get_extra_requirements():
            install_module(module, self.pip_path)

    def is_installed(self) -> bool:
        """If the handler is installed"""
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                return False
        return True

    @abstractmethod
    def recognize_file(self, path) -> str | None:
        """Recognize a given audio file"""
        pass

    def set_setting(self, name, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("stt-settings"))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][name] = value
        self.settings.set_string("stt-settings", json.dumps(j))

    def get_setting(self, name) -> Any:
        """Get setting from key""" 
        j = json.loads(self.settings.get_string("stt-settings"))
        if self.key not in j or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        """Get the default setting from a key"""
        for x in self.get_extra_settings():
            if x["key"] == name:
                return x["default"]
        return None

class SphinxHandler(STTHandler):
    key = "Sphinx"
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["pocketsphinx"]

    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)

        try:
            res = r.recognize_sphinx(audio)
        except sr.UnknownValueError:
            res = _("Could not understand the audio")
        except Exception as e:
            print(e)
            return None
        return res


class GoogleSRHandler(STTHandler):
    
    key = "google_sr"

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for Google SR, write 'default' to use the default one"),
                "type": "entry",
                "default": "default"
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("The language of the text to recgnize in IETF"),
                "type": "entry",
                "default": "en-US",
                "website": "https://stackoverflow.com/questions/14257598/what-are-language-codes-in-chromes-implementation-of-the-html5-speech-recogniti"
            }
        ]

    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        key = self.get_setting("api")
        language = self.get_setting("language")
        try:
            if key == "default":
                res = r.recognize_google(audio, language=language)
            else:
                res = r.recognize_google(audio, key=key, language=language)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

class WitAIHandler(STTHandler):
    
    key = "witai"
    
    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("Server Access Token for wit.ai"),
                "type": "entry",
                "default": ""
            },
        ]
 
    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        key = self.get_setting("api")
        try:
            res = r.recognize_wit(audio, key=key)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

class VoskHandler(STTHandler): 
    key = "vosk"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["vosk"]

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "path",
                "title": _("Model Path"),
                "description": _("Absolute path to the VOSK model (unzipped)"),
                "type": "entry",
                "website": "https://alphacephei.com/vosk/models",
                "default": ""
            },
        ]

    def recognize_file(self, path):
        from vosk import Model
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        path = self.get_setting("path")
        r.vosk_model = Model(path)
        try:
            res = json.loads(r.recognize_vosk(audio))["text"]
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

class WhisperAPIHandler(STTHandler):    
    key = "whisperapi"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpenAI"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "model",
                "title": _("Whisper API Model"),
                "description": _("Name of the Whisper API Model"),
                "type": "entry",
                "default": "whisper-1"
            },
        ]

    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        model = self.get_setting("model")
        api = self.get_setting("api")
        try:
            res = r.recognize_whisper_api(audio, model=model, api_key=api)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

class CustomSRHandler(STTHandler):
    
    key = "custom_command"

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "command",
                "title": _("Command to execute"),
                "description": _("{0} will be replaced with the model fullpath"),
                "type": "entry",
                "default": ""
            },
        ]

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def recognize_file(self, path):
        command = self.get_setting("command")
        if command is not None:
            res = check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", path)]).decode("utf-8")
            return str(res)
        return None



