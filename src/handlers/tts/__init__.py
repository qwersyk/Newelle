from .tts import TTSHandler
from .custom_handler import CustomTTSHandler
from .espeak_handler import EspeakHandler
from .gtts_handler import gTTSHandler
from .elevenlabs_handler import ElevenLabs
from .kokoro_handler import KokoroTTSHandler
from .openai_tts_handler import OpenAITTSHandler
from .custom_openai_tts import CustomOpenAITTSHandler
from .groq_tts_handler import GroqTTSHandler
from .edge_handler import EdgeTTSHandler

__all__ = [
    "TTSHandler",
    "CustomTTSHandler",
    "EspeakHandler",
    "gTTSHandler",
    "ElevenLabs",
    "KokoroTTSHandler",
    "OpenAITTSHandler",
    "CustomOpenAITTSHandler",
    "GroqTTSHandler",
    "EdgeTTSHandler"
]

