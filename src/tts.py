from abc import abstractmethod
from gtts import gTTS, lang
import subprocess, threading, time
import os, json, pyaudio
from .extra import can_escape_sandbox
from pydub import AudioSegment

class TTSHandler:
    """Every TTS handler should extend this class."""
    key = ""
    voices : tuple
 
    _playing : bool = False
    _play_lock : threading.Semaphore = threading.Semaphore(1)

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.voices = tuple()
        pass

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

    def playsound(self, path):
        self._play_lock.acquire()
        audio = AudioSegment.from_file(path)
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(format=self.p.get_format_from_width(audio.sample_width),
                        channels=audio.channels,
                        rate=audio.frame_rate,
                        output=True
                    )
        # Play audio
        self._playing = True
        self.stream.write(audio.raw_data)
        self._playing = False
        self._play_lock.release()

    def is_installed(self) -> bool:
        """If all the requirements are installed"""
        return True

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

    def set_setting(self, setting, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("tts-voice", json.dumps(j))

    def get_setting(self, name):
        """Get setting from key"""
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        """Get the default setting from a key"""
        for x in self.get_extra_settings():
            if x["key"] == name:
                return x["default"]
        return None

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
        if not self.is_installed() or not can_escape_sandbox():
            return self.voices
        output = subprocess.check_output(["flatpak-spawn", "--host", "espeak", "--voices"]).decode("utf-8")
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
        subprocess.check_output(["flatpak-spawn", "--host", "espeak", "-v" + str(self.get_current_voice()), message])
        self._play_lock.release()

    def save_audio(self, message, file):
        r = subprocess.check_output(["flatpak-spawn", "--host", "espeak", "-f", "-v" + str(self.get_current_voice()), message, "--stdout"])
        f = open(file, "wb")
        f.write(r)

    def is_installed(self):
        if not can_escape_sandbox():
            return False
        output = subprocess.check_output(["flatpak-spawn", "--host", "whereis", "espeak"]).decode("utf-8")
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

    @staticmethod
    def get_extra_settings() -> list:
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
            subprocess.check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", message)])
            self._play_lock.release()


    
