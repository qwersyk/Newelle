from gtts import gTTS, lang
from io import BytesIO
from playsound import playsound
import subprocess
import os, json

class gTTSHandler:

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.key = "gtts"
        self.voices = tuple()
        pass

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

    def voice_available(self, voice):
        for l in self.get_voices():
            if l[1] == voice:
                return True
        return False

    def save_audio(self, message, file):
        voice = self.get_current_voice()
        if not self.voice_available(voice):
            voice = self.get_voices()[0][1]
        tts = gTTS(message, lang=voice)
        tts.save(file)

    def play_audio(self, message):
        path = os.path.join(self.path, "temptts.mp3")
        self.save_audio(message, path)
        playsound(path)
        os.remove(path)

    def is_installed(self):
        return True

    def get_current_voice(self):
        j = json.loads(self.settings.get_string("tts-voice"))
        if not self.key in j or not self.voice_available(j[self.key]):
            return self.voices[0][1]
        else:
            return j[self.key]


class EspeakHandler:
    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.key = "espeak"
        self.voices = tuple()

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

    def voice_available(self, voice):
        for l in self.get_voices():
            if l[1] == voice:
                return True
        return False

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

    def get_current_voice(self):
        j = json.loads(self.settings.get_string("tts-voice"))
        if not self.key in j or not self.voice_available(j[self.key]):
            return self.voices[0][1]
        else:
            return j[self.key]

