import speech_recognition as sr
from .stt import STTHandler

class GoogleSRHandler(STTHandler):
    
    key = "google_sr"

    def get_extra_settings(self) -> list:
        return [

            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for Google SR, write 'default' to use the default one"),
                "type": "entry",
                "default": "default"
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("The language of the text to recgnize in IETF"),
                "type": "entry",
                "default": "en-US",
                "website": "https://stackoverflow.com/questions/14257598/what-are-language-codes-in-chromes-implementation-of-the-html5-speech-recogniti"
            }
        ]

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

