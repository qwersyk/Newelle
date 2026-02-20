import os
import gettext
from .stt import STTHandler
from ...utility.pip import install_module, find_module
from ..handler import ErrorSeverity
import wave
import numpy as np

_ = gettext.gettext

class OpenWakeWordHandler(STTHandler):
    key = "openwakeword"
    schema_key = "openwakeword"
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "wake_words",
                "title": _("Wake Words"),
                "description": _("Comma-separated list of wake words to detect"),
                "type": "entry",
                "default": "hey newelle,newelle",
            },
        ]
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["open-wakeword"]
    
    def install(self):
        install_module("openwakeword", self.pip_path)
        if not self.is_installed():
            self.throw("OpenWakeWord installation failed", ErrorSeverity.ERROR)
        from openwakeword import utils
        utils.download_models()
        self._is_installed_cache = None

    def is_installed(self) -> bool:
        return find_module("openwakeword") is not None

    def _get_model(self):
        if self.model is None:
            from openwakeword.model import Model
            wake_words = self.get_setting("wake_words")
            wake_words_list = [w.strip().lower() for w in wake_words.split(',') if w.strip()]
            self.model = Model()
        return self.model
    
    def _read_audio_file(self, path):
        with wave.open(path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            
            audio_data = wf.readframes(frames)
            
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            if channels == 2:
                audio_array = audio_array.reshape(-1, 2)
                audio_array = audio_array.mean(axis=1).astype(np.int16)
            
            return audio_array, rate
    
    def recognize_file(self, path) -> str | None:
        if not os.path.exists(path):
            return None
        
        try:
            model = self._get_model()
            audio_data, rate = self._read_audio_file(path)
            
            predictions = model.predict(audio_data)
            
            detected_words = []
            for word, score in predictions.items():
                print(word, score)
                if score > 0.5:
                    detected_words.append(word)
 
            if detected_words:
                return ', '.join(detected_words)
            
            return ""
        except Exception as e:
            print(f"OpenWakeWord error: {e}")
            return "" 
