import speech_recognition as sr
from .stt import STTHandler

class WitAIHandler(STTHandler):
    
    key = "witai"
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("Server Access Token for wit.ai"),
                "type": "entry",
                "default": "",
                "password": True,
            },
        ]
 
    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        key = self.get_setting("api")
        try:
            res = r.recognize_wit(audio, key=key)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

