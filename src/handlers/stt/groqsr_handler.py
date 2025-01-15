from .openaisr_handler import OpenAISRHandler

class GroqSRHandler(OpenAISRHandler):
    key = "groq_sr"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for Groq SR, write 'default' to use the default one"),
                "type": "entry",
                "default": "default"
            },
            {
                "key": "model",
                "title": _("Groq Model"),
                "description": _("Name of the Groq Model"),
                "type": "entry",
                "default": "whisper-large-v3-turbo",
                "website": "https://console.groq.com/docs/models",
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("Specify the language for transcription. Use ISO 639-1 language codes (e.g. \"en\" for English, \"fr\" for French, etc.). "),
                "type": "entry",
                "default": "",
            }
        ]
