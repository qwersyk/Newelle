from .custom_openai_tts import CustomOpenAITTSHandler
from ...handlers import ExtraSettings

class GroqTTSHandler(CustomOpenAITTSHandler):

    key = "groq_tts"

    def __init__(self, a, b) -> None:
        super().__init__(a, b)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")
        self.set_setting("instructions", "")

    def get_models(self):
        models = ["playai-tts", "playai-tts-arabic"]
        m = tuple()
        for model in models:
            m += ((model, model),)
        return m

    def get_voices(self):
        if self.get_setting("model", False, "playai-tts") == "playai-tts":
            voices = "Arista-PlayAI, Atlas-PlayAI, Basil-PlayAI, Briggs-PlayAI, Calum-PlayAI, Celeste-PlayAI, Cheyenne-PlayAI, Chip-PlayAI, Cillian-PlayAI, Deedee-PlayAI, Fritz-PlayAI, Gail-PlayAI, Indigo-PlayAI, Mamaw-PlayAI, Mason-PlayAI, Mikail-PlayAI, Mitch-PlayAI, Quinn-PlayAI, Thunder-PlayAI".split(", ")
        else:
            voices = "Ahmad-PlayAI, Amira-PlayAI, Khalid-PlayAI, Nasser-PlayAI".split(", ")
        v = tuple()
        for voice in voices:
            v += ((voice.capitalize(), voice),)
        return v
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("api_key", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.ComboSetting("voice", _("Voice"), _("The voice to use"), self.get_voices(), "Arista-PlayAI"),
            ExtraSettings.ComboSetting("model", _("Model"), _("The model to use"), self.get_models(), "playai-tts", update_settings=True),
        ]
