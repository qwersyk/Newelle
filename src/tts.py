from gtts import gTTS, lang
from io import BytesIO
from playsound import playsound
import os

class gTTSHandler():

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        pass

    def get_languages(self):
        x = lang.tts_langs()
        res = tuple()
        for l in x:
            t = (x[l], l)
            res += (t,)
        return res

    def language_available(self, language):
        for l in self.get_languages():
            if l[1] == language:
                return True
        return False

    def save_audio(self, message, file):
        tts = gTTS(message, lang=self.settings.get_string("tts-voice"))
        tts.save(file)

    def play_audio(self, message):
        path = os.path.join(self.path, "temptts.mp3")
        self.save_audio(message, path)
        playsound(path)
        os.remove(path)
