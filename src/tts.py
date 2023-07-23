from gtts import gTTS, lang

class gTTSHandler():

    def __init__(self, settings):
        self.settings = settings
        pass

    def get_languages(self):
        x = lang.tts_langs()
        res = tuple()
        for l in x:
            t = (x[l], l)
            res += (t,)
        return res
