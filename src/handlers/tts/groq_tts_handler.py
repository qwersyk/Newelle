from .custom_openai_tts import CustomOpenAITTSHandler
from ...handlers import ExtraSettings

class GroqTTSHandler(CustomOpenAITTSHandler):

    key = "groq_tts"

    def __init__(self, a, b) -> None:
        super().__init__(a, b)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")
        self.set_setting("instructions", "")
        self.set_setting("response_format", "wav")

    def get_models(self):
        models = ["canopylabs/orpheus-v1-english", "canopylabs/orpheus-arabic-saudi"]
        m = tuple()
        for model in models:
            m += ((model, model),)
        return m

    def get_voices(self):
        if self.get_setting("model", False, "canopylabs/orpheus-v1-english") == "canopylabs/orpheus-v1-english":
            voices = "autumn, diana, hannah, austin, daniel, troy".split(", ")
        else:
            voices = "fahad, sultan, lulwa, noura".split(", ")
        v = tuple()
        for voice in voices:
            v += ((voice.capitalize(), voice),)
        return v
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("api_key", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.ComboSetting("voice", _("Voice"), _("The voice to use"), self.get_voices(), "troy"),
            ExtraSettings.ComboSetting("model", _("Model"), _("The model to use"), self.get_models(), "canopylabs/orpheus-v1-english", update_settings=True),
        ]
