from abc import abstractmethod
from typing import Callable

from gtts import gTTS, lang
from subprocess import check_output
import threading 
import time
import os
from ...utility.system import can_escape_sandbox, get_spawn_command 
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
from pygame import mixer
from ..handler import Handler

class TTSHandler(Handler):
    """Every TTS handler should extend this class."""
    key = ""
    schema_key = "tts-voice"
    voices : tuple
    _play_lock : threading.Semaphore = threading.Semaphore(1)
    def __init__(self, settings, path):
        mixer.init()
        self.settings = settings
        self.path = path
        self.voices = tuple()
        self.on_start = lambda : None
        self.on_stop  = lambda : None
        pass

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
        os.remove(path)

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
        mixer.music.load(path)
        mixer.music.play()
        while mixer.music.get_busy():
            time.sleep(0.1)
        self.on_stop()
        self._play_lock.release()

    def stop(self):
        if mixer.music.get_busy():
            mixer.music.stop()

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


class gTTSHandler(TTSHandler):
    key = "gtts"
   
    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        x = lang.tts_langs()
        res = tuple()
        for l in x:
            t = (x[l], l)
            res += (t,)
        self.voices = res
        return res

    def save_audio(self, message, file):
        voice = self.get_current_voice()
        if not self.voice_available(voice):
            voice = self.get_voices()[0][1]
        tts = gTTS(message, lang=voice)
        tts.save(file)


class EspeakHandler(TTSHandler):
    
    key = "espeak"

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        if not self.is_installed():
            return self.voices
        output = check_output(get_spawn_command() + ["espeak", "--voices"]).decode("utf-8")
        # Extract the voice names from the output
        lines = output.strip().split("\n")[1:]
        voices = tuple()
        for line in lines:
            spl = line.split()
            voices += ((spl[3], spl[4]),)
        self.voices = voices
        return voices

    def play_audio(self, message):
        self._play_lock.acquire()
        check_output(get_spawn_command() + ["espeak", "-v" + str(self.get_current_voice()), message])
        self._play_lock.release()

    def save_audio(self, message, file):
        r = check_output(get_spawn_command() + ["espeak", "-f", "-v" + str(self.get_current_voice()), message, "--stdout"])
        f = open(file, "wb")
        f.write(r)

    def is_installed(self):
        if not can_escape_sandbox():
            return False
        output = check_output(get_spawn_command() + ["whereis", "espeak"]).decode("utf-8")
        paths = []
        if ":" in output:
            paths = output.split(":")[1].split()
        if len(paths) > 0:
            return True
        return False

class CustomTTSHandler(TTSHandler):
    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.key = "custom_command"
        self.voices = tuple()

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self) -> list:
        return [{
            "key": "command",
            "title": _("Command to execute"),
            "description": _("{0} will be replaced with the model fullpath"),
            "type": "entry",
            "default": ""
        }]


    def is_installed(self):
        return True

    def play_audio(self, message):
        command = self.get_setting("command")
        if command is not None:
            self._play_lock.acquire()
            check_output(get_spawn_command() + ["bash", "-c", command.replace("{0}", message)])
            self._play_lock.release()

class ElevenLabs(TTSHandler):
    key = "elevenlabs"
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for ElevenLabs"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "voice",
                "title": _("Voice"),
                "description": _("Voice ID to use"),
                "type": "entry",
                "default": "21m00Tcm4TlvDq8ikWAM"
            },
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the model to use"),
                "type": "combo",
                "values": (("eleven_turbo_v2_5", "eleven_turbo_v2_5"), ("eleven_multilingual_v2", "eleven_multilingual_v2")),
                "default": "eleven_turbo_v2_5"
            },
            {
                "key": "stability",
                "title": _("Stability"),
                "description": _("stability of the voice"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0.50
            },
            {
                "key": "similarity",
                "title": _("Similarity boost"),
                "description": _("Boosts overall voice clarity and speaker similarity"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0.75
            },
            {
                "key": "style_exaggeration",
                "title": _("Style exaggeration"),
                "description": _("High values are reccomended if the style of the speech must be exaggerated"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0
            },

        ]

    @staticmethod
    def get_extra_requirements() -> list[str]:
        return ["elevenlabs"]

    def save_audio(self, message, file):
        from elevenlabs.client import ElevenLabs 
        from elevenlabs import save
        from elevenlabs.types import VoiceSettings
        client = ElevenLabs(api_key=self.get_setting("api"))
        audio = client.generate(text=message, voice=self.get_setting("voice"), model=self.get_setting("model"),
                                voice_settings=VoiceSettings(stability=self.get_setting("stability"), similarity_boost=self.get_setting("similarity"), style=self.get_setting("style_exaggeration")))
        save(audio, file) 
        return  
