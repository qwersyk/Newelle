from pathlib import Path
from .tts import TTSHandler
from ...utility.pip import install_module, find_module
from ...handlers import ErrorSeverity, ExtraSettings

class CustomOpenAITTSHandler(TTSHandler):
    key = "custom_openai_tts"

    def install(self):
        # Assuming pip_path is available from the base class or context
        install_module("openai",self.pip_path)
        if not self.is_installed():
            self.throw("OpenAI installation failed", ErrorSeverity.ERROR)

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("endpoint", _("Endpoint"), _("Custom endpoint of the service to use"), "https://api.openai.com/v1/"),
            ExtraSettings.EntrySetting("api_key", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.EntrySetting("voice", _("Voice"), _("The voice to use"), "alloy"),
            ExtraSettings.EntrySetting("model", _("Model"), _("The model to use"), "tts-1"),
            ExtraSettings.EntrySetting("instructions", _("Instructions"), _("Instructions for the voice generation. Leave it blank to avoid this field"), "")
        ]
    def is_installed(self) -> bool:
        return find_module("openai") is not None 

    def save_audio(self, message, file):
        from openai import OpenAI
        from openai import NOT_GIVEN
        speech_file_path = file

        try:
            client = OpenAI(api_key=self.get_setting("api_key"), base_url=self.get_setting("endpoint"))
            response = client.audio.speech.create(
                model=self.get_setting("model"), 
                voice=self.get_setting("voice"),
                input=message,
                response_format="mp3",
                instructions=self.get_setting("instructions") if self.get_setting("instructions") != "" else NOT_GIVEN
            )
            response.write_to_file(speech_file_path)
        except Exception as e:
            self.throw(f"TTS error: {e}", ErrorSeverity.ERROR)
