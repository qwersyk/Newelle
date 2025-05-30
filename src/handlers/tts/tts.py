from abc import abstractmethod
from typing import Callable

from subprocess import check_output
import threading 
import time
import os
from ...utility.system import can_escape_sandbox, get_spawn_command 
from ..handler import Handler
from pydub import AudioSegment
from pydub.playback import play
import multiprocessing

class TTSHandler(Handler):
    """Every TTS handler should extend this class."""
    key = ""
    schema_key = "tts-voice"
    voices : tuple
    _play_lock : threading.Semaphore = threading.Semaphore(1)
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.settings = settings
        self.path = path
        self.voices = tuple()
        self.on_start = lambda : None
        self.on_stop  = lambda : None
        self.play_process = None

    def get_extra_settings(self) -> list:
        """Get extra settings for the TTS"""
        voices = self.get_voices()
        default = "" if len(voices) == 0 else voices[0][1]
        return [
            {
                "key": "voice",
                "type": "combo",
                "title": _("Voice"),
                "description": _("Choose the preferred voice"),
                "default": default,
                "values": voices
            }
        ]

    def get_voices(self):
        """Return a tuple containing the available voices"""
        return tuple()

    def voice_available(self, voice):
        """Check fi a voice is available"""
        for l in self.get_voices():
            if l[1] == voice:
                return True
        return False

    @abstractmethod
    def save_audio(self, message, file):
        """Save an audio in a certain file path"""
        pass

    def play_audio(self, message):
        """Play an audio from the given message"""
        # Generate random name
        timestamp = str(int(time.time()))
        random_part = str(os.urandom(8).hex())
        file_name = f"{timestamp}_{random_part}.mp3"
        path = os.path.join(self.path, file_name)
        self.save_audio(message, path)
        self.playsound(path)
        try:
            os.remove(path)
        except Exception as e:
            print("Could not delete file: " + str(e))

    def connect(self, signal: str, callback: Callable):
        if signal == "start":
            self.on_start = callback
        elif signal == "stop":
            self.on_stop = callback

    def playsound(self, path):
        """Play an audio from the given path"""
        self.stop()
        self._play_lock.acquire()
        self.on_start()
        try:
            if path.endswith("mp3"):
                segment = AudioSegment.from_mp3(path)
            else:
                segment = AudioSegment.from_wav(path)
            p = multiprocessing.Process(target=play, args=(segment,))
            p.start()
            self.play_process = p
            p.join()
            p.terminate()
        except Exception as e:
            print("Error playing the audio: " + str(e))
            pass
        self.on_stop()
        self.play_process = None
        self._play_lock.release()

    def stop(self):
        if self.play_process is not None:
            self.play_process.terminate()

    def get_current_voice(self):
        """Get the current selected voice"""
        voice = self.get_setting("voice")
        if voice is None:
            if self.voices == ():
                return None
            return self.voices[0][1]
        else:
            return voice

    def set_voice(self, voice):
        """Set the given voice"""
        self.set_setting("voice", voice)


