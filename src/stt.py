import os, sys, subprocess, json
import importlib
import pyaudio
import wave
import speech_recognition as sr

class AudioRecorder:
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
def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    if full_module_name == "git+https://github.com/openai/whisper.git":
        full_module_name = "whisper"
    try:
        return importlib.import_module(full_module_name)
    except ImportError as exc:
        if not (full_module_name + '.').startswith(exc.name + '.'):
            raise


def install_module(module, path):
    r = subprocess.check_output(["pip3", "install", "--target", path, module]).decode("utf-8")
    return r

class STTHandler:
    def __init__(self, settings, pip_path, stt):
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt
        self.key = ""

    def install(self):
        for module in self.stt["extra_requirements"]:
            install_module(module, self.pip_path)

    def is_installed(self):
        for module in self.stt["extra_requirements"]:
            if find_module(module) is None:
                return False
        return True

    def recognize_file(self, path):
        return None

    def set_setting(self, name, value):
        j = json.loads(self.settings.get_string("stt-settings"))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][name] = value
        self.settings.set_string("stt-settings", json.dumps(j))

    def get_setting(self, name):
        j = json.loads(self.settings.get_string("stt-settings"))
        if self.key not in j or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        for x in self.stt["extra_settings"]:
            if x["key"] == name:
                return x["default"]
        return None

class SphinxHandler(STTHandler):
    def __init__(self, settings, pip_path, stt):
        self.key = "Sphinx"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

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
    def __init__(self, settings, pip_path, stt):
        self.key = "google_sr"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

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
    def __init__(self, settings, pip_path, stt):
        self.key = "witai"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

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
    def __init__(self, settings, pip_path, stt):
        self.key = "vosk"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

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
    def __init__(self, settings, pip_path, stt):
        self.key = "whisperapi"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

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
    def __init__(self, settings, pip_path, stt):
        self.key = "custom_command"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

    def recognize_file(self, path):
        command = self.get_setting("command")
        res = subprocess.check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", path)]).decode("utf-8")
        return res
