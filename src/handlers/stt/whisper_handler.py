from .stt import STTHandler
from ...utility.pip import find_module
from ..handler import ErrorSeverity

class WhisperHandler(STTHandler):
    key = "whisper"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the Whisper model"),
                "type": "combo",
                "values": self.get_models(),
                "default": "tiny",
                "website": "https://github.com/openai/whisper/blob/main/model-card.md#model-details",
            },
        ]
   
    def get_models(self):
        if self.is_installed():
            import whisper
            models = whisper._MODELS.keys()
            result = tuple()
            for model in models:
                result = result + ((model, model),)
            return result
        else:
            return (("tiny", "tiny"), )

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai-whisper"]

    def is_installed(self) -> bool:
        return True if find_module("whisper") is not None else False

    def install(self):
        print("Installing whisper...")
        super().install()
        try:
            import whisper
            print("Whisper installed, installing tiny model...")
            whisper.load_model("tiny")
        except Exception as e:
            return
            self.throw("Error installing Whisper: " + str(e), ErrorSeverity.ERROR)

    def recognize_file(self, path):
        import whisper
        if self.model is None:
            self.model = whisper.load_model(self.get_setting("model"))
        res = self.model.transcribe(path)
        if res["text"] is None:
            return ""
        return res["text"]
