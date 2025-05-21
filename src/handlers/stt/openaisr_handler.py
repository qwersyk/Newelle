from .stt import STTHandler

class OpenAISRHandler(STTHandler):
    key = "openai_sr"

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": _("API Endpoint"),
                "description": _("Endpoint for OpenAI requests"),
                "type": "entry",
                "default": "https://api.openai.com/v1/"
            },
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpenAI"),
                "type": "entry",
                "default": "",
                "password": True,
            },
            {
                "key": "model",
                "title": _("Whisper Model"),
                "description": _("Name of the OpenAI model"),
                "type": "entry",
                "default": "whisper-1",
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("Optional: Specify the language for transcription. Use ISO 639-1 language codes (e.g. \"en\" for English, \"fr\" for French, etc.). "),
                "type": "entry",
                "default": "",
            }
        ]

    def recognize_file(self, path) -> str | None:
        import openai
        key = self.get_setting("api")
        model = self.get_setting("model")
        language = str(self.get_setting("language"))
        if language == "":
            language = openai.NOT_GIVEN
        client = openai.Client(api_key=key, base_url=self.get_setting("endpoint"))
        with open(path, "rb") as audio_file:
           transcription = client.audio.transcriptions.create(
                file=(path, audio_file.read()),
                model=model,
                language=language
            )
        return transcription.text
