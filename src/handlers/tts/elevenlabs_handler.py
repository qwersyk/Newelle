from .tts import TTSHandler

class ElevenLabs(TTSHandler):
    key = "elevenlabs"
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for ElevenLabs"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "voice",
                "title": _("Voice"),
                "description": _("Voice ID to use"),
                "type": "entry",
                "default": "21m00Tcm4TlvDq8ikWAM"
            },
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the model to use"),
                "type": "combo",
                "values": (("eleven_turbo_v2_5", "eleven_turbo_v2_5"), ("eleven_multilingual_v2", "eleven_multilingual_v2")),
                "default": "eleven_turbo_v2_5"
            },
            {
                "key": "stability",
                "title": _("Stability"),
                "description": _("stability of the voice"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0.50
            },
            {
                "key": "similarity",
                "title": _("Similarity boost"),
                "description": _("Boosts overall voice clarity and speaker similarity"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0.75
            },
            {
                "key": "style_exaggeration",
                "title": _("Style exaggeration"),
                "description": _("High values are reccomended if the style of the speech must be exaggerated"),
                "type": "range",
                "min": 0,
                "max": 1,
                "round-digits": 2,
                "default": 0
            },

        ]

    @staticmethod
    def get_extra_requirements() -> list[str]:
        return ["elevenlabs"]

    def save_audio(self, message, file):
        from elevenlabs.client import ElevenLabs 
        from elevenlabs import save
        from elevenlabs.types import VoiceSettings
        client = ElevenLabs(api_key=self.get_setting("api"))
        audio = client.generate(text=message, voice=self.get_setting("voice"), model=self.get_setting("model"),
                                voice_settings=VoiceSettings(stability=self.get_setting("stability"), similarity_boost=self.get_setting("similarity"), style=self.get_setting("style_exaggeration")))
        save(audio, file) 
        return  
