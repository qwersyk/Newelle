from gtts import gTTS, lang

from .tts import TTSHandler

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

