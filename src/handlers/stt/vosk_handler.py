import speech_recognition as sr
import json
from .stt import STTHandler


class VoskHandler(STTHandler): 
    key = "vosk"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["vosk"]

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "path",
                "title": _("Model Path"),
                "description": _("Absolute path to the VOSK model (unzipped)"),
                "type": "entry",
                "website": "https://alphacephei.com/vosk/models",
                "default": ""
            },
        ]

    def recognize_file(self, path):
        from vosk import Model
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        path = self.get_setting("path")
        r.vosk_model = Model(path)
        try:
            res = json.loads(r.recognize_vosk(audio))["text"]
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(e)
            return None
        return res

