from gtts import gTTS, lang
from io import BytesIO
from playsound import playsound
import subprocess
import os, json

class TTSHandler:
    global AVAILABLE_TTS
    def __init__(self, settings, path, tts):
        self.settings = settings
        self.path = path
        self.key = ""
        self.tts = tts
        self.voices = tuple()
        pass

    def get_voices(self):
        return tuple()

    def voice_available(self, voice):
        for l in self.get_voices():
            if l[1] == voice:
                return True
        return False

    def save_audio(self, message, file):
        return False

    def play_audio(self, message):
        path = os.path.join(self.path, "temptts.mp3")
        self.save_audio(message, path)
        playsound(path)
        os.remove(path)

    def is_installed(self):
        return True

    def get_current_voice(self):
        voice = self.get_setting("voice")
        if voice is None:
            return self.voices[0][1]
        else:
            return voice

    def set_voice(self, voice):
        self.set_setting("voice", voice)

    def set_setting(self, setting, value):
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("tts-voice", json.dumps(j))

    def get_setting(self, name):
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        for x in self.tts["extra_settings"]:
            if x["key"] == name:
                return x["default"]
        return None

class gTTSHandler(TTSHandler):
    def __init__(self, settings, path, tts):
        self.settings = settings
        self.path = path
        self.key = "gtts"
        self.voices = tuple()
        self.tts = tts

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
    def __init__(self, settings, path, tts):
        self.settings = settings
        self.path = path
        self.key = "espeak"
        self.voices = tuple()
        self.tts = tts

    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        if not self.is_installed():
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
        subprocess.Popen(["flatpak-spawn", "--host", "espeak", "-v" + self.get_current_voice(), message])

    def save_audio(self, message, path):
        r = subprocess.check_output(["flatpak-spawn", "--host", "espeak", "-f", "-v" + self.get_current_voice(), message, "--stdout"])
        f = open(path, "wb")
        f.write(r)

    def is_installed(self):
        output = subprocess.check_output(["flatpak-spawn", "--host", "whereis", "espeak"]).decode("utf-8")
        paths = []
        if ":" in output:
            paths = output.split(":")[1].split()
        if len(paths) > 0:
            return True
        return False

class CustomTTSHandler(TTSHandler):
    def __init__(self, settings, path, tts):
        self.settings = settings
        self.path = path
        self.key = "custom_command"
        self.voices = tuple()
        self.tts = tts

    def is_installed(self):
        return True

    def play_audio(self, message):
        command = self.get_setting("command")
        subprocess.Popen(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", message)])


    
