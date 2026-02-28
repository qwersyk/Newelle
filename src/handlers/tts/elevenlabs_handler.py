from ...utility.pip import find_module, install_module
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
                "default": "",
                "password": True,
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
                "values": (("eleven_turbo_v2_5", "eleven_turbo_v2_5"), ("eleven_multilingual_v2", "eleven_multilingual_v2"), ("eleven_flash_v2_5", "eleven_flash_v2_5"), ("eleven_v3", "eleven_v3"), ("eleven_ttv_v3", "eleven_ttv_v3")),
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

    def install(self):
        install_module("elevenlabs==2.9.1", self.pip_path, True)
        self._is_installed_cache = None
    
    def is_installed(self) -> bool:
        return find_module("elevenlabs") is not None

    def save_audio(self, message, file):
        from elevenlabs.client import ElevenLabs 
        from elevenlabs import save
        from elevenlabs.types import VoiceSettings
        client = ElevenLabs(api_key=self.get_setting("api"))
        sett = VoiceSettings(stability=self.get_setting("stability"), similarity_boost=self.get_setting("similarity"), style=self.get_setting("style_exaggeration"))
        audio = client.text_to_speech.convert(text=message, voice_id=self.get_setting("voice"), model_id=self.get_setting("model"), output_format="mp3_44100_128", voice_settings=sett)
        save(audio, file) 
