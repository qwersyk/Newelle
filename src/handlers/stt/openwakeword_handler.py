import os
import gettext
_ = gettext.gettext
from .stt import STTHandler
from ...utility.pip import install_module, find_module
from ..handler import ErrorSeverity
from ..extra_settings import ExtraSettings
import wave
import numpy as np


class OpenWakeWordHandler(STTHandler):
    key = "openwakeword"
    schema_key = "openwakeword"
    
    PRETRAINED_MODELS = {
        "hey_jarvis": "hey_jarvis",
        "alexa": "alexa", 
        "hey_mycroft": "hey_mycroft",
        "hey_rhasspy": "hey_rhasspy",
    }
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
        self.wakewords_dir = os.path.join(self.path, "wakewords")
        if not os.path.exists(self.wakewords_dir):
            os.makedirs(self.wakewords_dir)
    
    def get_wakeword_models(self, up=False):
        models = tuple()
        for name, value in self.PRETRAINED_MODELS.items():
            models += ((name, value),)
        if os.path.exists(self.wakewords_dir):
            for f in os.listdir(self.wakewords_dir):
                if f.endswith('.onnx'):
                    name = os.path.splitext(f)[0]
                    models += ((f"{name}", os.path.join(self.wakewords_dir, f)),)
        if up:
            self.settings_update()
        return models
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ComboSetting(
                "wakeword_model",
                _("Wakeword Model"),
                _("Select a pre-trained or custom wakeword model. Wakewords must be in onnx format."),
                self.get_wakeword_models(),
                "hey_jarvis",
                folder=self.wakewords_dir,
                refresh=lambda x: self.get_wakeword_models(True), update_settings=True
            ),
            ExtraSettings.EntrySetting("wakeword", "Wakeword", "Wakewords to remove from the transcription, comma separated", "hey jarvis, jarvis"),
            ExtraSettings.ScaleSetting("sensitivity", _("Sensitivity"), _("Minimum score for detection. You can see the score given to recorded audio in the console when launching Newelle"), 0.5, 0.0, 1.0, 2),
        ]
     
    def install(self):
        install_module("openwakeword", self.pip_path)
        if not self.is_installed():
            self.throw("OpenWakeWord installation failed", ErrorSeverity.ERROR)
        from openwakeword import utils
        utils.download_models()
        self._is_installed_cache = None

    def is_installed(self) -> bool:
        return find_module("openwakeword") is not None

    def _get_model_path(self, model_name: str) -> str:
        import openwakeword
        if model_name in self.PRETRAINED_MODELS:
            pretrained_paths = openwakeword.get_pretrained_model_paths()
            for path in pretrained_paths:
                if model_name in path:
                    return path
        return model_name

    def _get_model(self):
        if self.model is None:
            from openwakeword.model import Model
            selected_model = self.get_setting("wakeword_model")
            model_path = self._get_model_path(selected_model)
            try:
                self.model = Model(wakeword_models=[model_path])
            except (TypeError, RuntimeError):
                try:
                    self.model = Model(wakeword_model_paths=[model_path])
                except (TypeError, RuntimeError):
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
            
            predictions_list = model.predict_clip(audio_data, padding=1)
            
            max_scores = {}
            for frame_preds in predictions_list:
                for word, score in frame_preds.items():
                    if word not in max_scores or score > max_scores[word]:
                        max_scores[word] = score
            
            detected_words = []
            for word, score in max_scores.items():
                print(word, score)
                if score > self.get_setting("sensitivity"):
                    detected_words.append(word)
 
            if detected_words:
                return ', '.join(detected_words)
            
            return ""
        except Exception as e:
            print(f"OpenWakeWord error: {e}")
            return ""


    def set_setting(self, key: str, value):
        super().set_setting(key, value)
        if key == "wakeword_model":
            self.model = None
            if not value.endswith(".onnx"):
                wakeword_txt = value.split("_")
            else:
                wakeword_txt = value.split(".onnx")[0].split("/")[-1].split("_") 
            self.set_setting("wakeword", ", ".join(wakeword_txt))

    def get_wakewords(self):
        return self.get_setting("wakeword")
