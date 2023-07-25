import os, sys, subprocess
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

        try:
            res = r.recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res
                    
