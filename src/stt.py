from abc import abstractmethod
from subprocess import check_output
import os, sys, json
import importlib
from typing import Any
import pyaudio
import wave
import struct
import speech_recognition as sr
from .extra import find_module, get_spawn_command, install_module
from .handler import Handler


class AudioRecorder:
    """Record audio with optional auto-stop on silence detection."""
    def __init__(self, auto_stop: bool = False, stop_function : callable = lambda _: (), silence_threshold: float = 0.01, silence_duration: int = 2):
        self.recording = False
        self.frames = []
        self.auto_stop = auto_stop
        self.stop_function = stop_function
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.sample_format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 44100
        self.chunk_size = 1024
        self.silent_chunks = 0

    def start_recording(self, output_file):
        self.recording = True
        self.frames = []
        self.silent_chunks = 0
        p = pyaudio.PyAudio()
        stream = p.open(format=self.sample_format,
                        channels=self.channels,
                        rate=self.sample_rate,
                        frames_per_buffer=self.chunk_size,
                        input=True)
        while self.recording:
            data = stream.read(self.chunk_size)
            self.frames.append(data)
            if self.auto_stop:
                rms = self._calculate_rms(data)
                if rms < self.silence_threshold:
                    self.silent_chunks += 1
                else:
                    self.silent_chunks = 0
                if self.silent_chunks >= self.silence_duration * (self.sample_rate / self.chunk_size):
                    self.recording = False
        stream.stop_stream()
        stream.close()
        p.terminate()
        self.save_recording(output_file)
    def stop_recording(self, output_file):
        self.recording = False

    def save_recording(self, output_file):
        p = pyaudio.PyAudio()
        wf = wave.open(output_file, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(p.get_sample_size(self.sample_format))
        wf.setframerate(self.sample_rate)
        wf.writeframes(b''.join(self.frames))
        wf.close()
        p.terminate()
        self.stop_function()
    
    def _calculate_rms(self, data):
        """Calculate the root mean square of the audio data."""
        count = len(data) // 2  # Each sample is 2 bytes (16-bit)
        format = "%dh" % (count)
        shorts = struct.unpack(format, data)
        sum_squares = sum(sample * sample for sample in shorts)
        rms = (sum_squares / count) ** 0.5
        return rms

class STTHandler(Handler):
    """Every STT Handler should extend this class"""
    key = ""
    schema_key = "stt-settings"
    
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


class OpenAISRHandler(STTHandler):
    key = "openai_sr"

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": _("API Endpoint"),
                "description": _("Endpoint for openai requests"),
                "type": "entry",
                "default": "https://api.openai.com/v1/"
            },
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpanAI"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "model",
                "title": _("Whisper Model"),
                "description": _("Name of the OpenAI model"),
                "type": "entry",
                "default": "whisper-1",
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("Optional: Specify the language for transcription. Use ISO 639-1 language codes (e.g. \"en\" for English, \"fr\" for French, etc.). "),
                "type": "entry",
                "default": "",
            }
        ]

    def recognize_file(self, path) -> str | None:
        import openai
        key = self.get_setting("api")
        model = self.get_setting("model")
        language = str(self.get_setting("language"))
        if language == "":
            language = openai.NOT_GIVEN
        client = openai.Client(api_key=key, base_url=self.get_setting("endpoint"))
        with open(path, "rb") as audio_file:
           transcription = client.audio.transcriptions.create(
                file=(path, audio_file.read()),
                model=model,
                language=language
            )
        return transcription.text

class GroqSRHandler(OpenAISRHandler):
    key = "groq_sr"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for Groq SR, write 'default' to use the default one"),
                "type": "entry",
                "default": "default"
            },
            {
                "key": "model",
                "title": _("Groq Model"),
                "description": _("Name of the Groq Model"),
                "type": "entry",
                "default": "whisper-large-v3-turbo",
                "website": "https://console.groq.com/docs/models",
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("Specify the language for transcription. Use ISO 639-1 language codes (e.g. \"en\" for English, \"fr\" for French, etc.). "),
                "type": "entry",
                "default": "",
            }
        ]

class GoogleSRHandler(STTHandler):
    
    key = "google_sr"

    def get_extra_settings(self) -> list:
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
    
    def get_extra_settings(self) -> list:
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


class WhisperHandler(STTHandler):
    key = "whisper"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the Whisper model"),
                "type": "combo",
                "values": self.get_models(),
                "default": "tiny",
                "website": "https://github.com/openai/whisper/blob/main/model-card.md#model-details",
            },
        ]
   
    def get_models(self):
        if self.is_installed():
            import whisper
            models = whisper._MODELS.keys()
            result = tuple()
            for model in models:
                result = result + ((model, model),)
            return result
        else:
            return (("tiny", "tiny"), )

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai-whisper"]

    def is_installed(self) -> bool:
        return True if find_module("whisper") is not None else False

    def install(self):
        print("Installing whisper...")
        super().install()
        import whisper
        print("Whisper installed, installing tiny model...")
        whisper.load_model("tiny")

    def recognize_file(self, path):
        import whisper
        if self.model is None:
            self.model = whisper.load_model(self.get_setting("model"))
        res = self.model.transcribe(path)
        if res["text"] is None:
            return ""
        return res["text"]
    
class VoskHandler(STTHandler): 
    key = "vosk"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["vosk"]

    def get_extra_settings(self) -> list:
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

class CustomSRHandler(STTHandler):
    
    key = "custom_command"

    def get_extra_settings(self) -> list:
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
            res = check_output(get_spawn_command() + ["bash", "-c", command.replace("{0}", path)]).decode("utf-8")
            return str(res)
        return None



