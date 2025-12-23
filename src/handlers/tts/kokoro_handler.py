import os
import re
import urllib.request

from .tts import TTSHandler
from ...utility.pip import install_module, find_module
from ..handler import ErrorSeverity


class KokoroTTSHandler(TTSHandler):
    key = "kokoro"

    MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

    def install(self):
        cache_dir = os.path.join(self.path, "kokoro_cache")
        os.makedirs(cache_dir, exist_ok=True)
        extra_deps = "fugashi jaconv mojimoji mecab-python3 unidic-lite"
        install_module(
            "kokoro-onnx soundfile espeakng-loader misaki " + extra_deps,
            self.pip_path,
            update=False,
            cache_dir=cache_dir,
        )
        self._ensure_model_files(cache_dir)
        if not self.is_installed():
            self.throw("Kokoro installation failed", ErrorSeverity.ERROR)

    def is_installed(self) -> bool:
        return find_module("kokoro_onnx") is not None and find_module("soundfile") is not None

    def get_voices(self):
        voices = "af_alloy, af_aoede, af_bella, af_heart, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky".split(", ")
        voices += "am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck".split(", ")
        voices += "bf_alice, bf_emma, bf_isabella, bf_lily, bm_daniel, bm_fable, bm_george, bm_lewis".split(", ")
        voices += ["ef_dora", "em_alex", "em_santa"]
        voices += ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"]
        voices += ["ff_siwis"]
        voices += "if_sara, im_nicola".split(", ")
        voices += ["pf_dora", "pm_alex", "pm_santa"]
        voices += "jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro, jm_kumo".split(", ")
        voices += "zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang".split(", ")

        flags = {"a": "🇺🇸", "b": "🇬🇧", "e": "🇪🇸", "f": "🇫🇷", "h": "🇮🇳", "p": "🇧🇷", "i": "🇮🇹", "j": "🇯🇵", "z": "🇨🇳"}
        genders = {"m": "🚹", "f": "🚺"}

        v = tuple()
        for voice in voices:
            nationality = voice[0]
            gender = voice[1]
            name = voice[3:]
            v += ((flags.get(nationality, "") + genders.get(gender, "") + " " + name.capitalize(), voice),)
        return v

    def save_audio(self, message, file):
        import numpy as np
        import soundfile as sf
        from kokoro_onnx import Kokoro

        cache_dir = os.path.join(self.path, "kokoro_cache")
        os.makedirs(cache_dir, exist_ok=True)
        self._ensure_model_files(cache_dir)

        voice_setting = self.get_current_voice()
        if isinstance(voice_setting, (tuple, list)):
            voice_id = voice_setting[1] if len(voice_setting) > 1 else voice_setting[0]
        else:
            voice_id = voice_setting

        if not isinstance(voice_id, str) or not voice_id or "_" not in voice_id:
            voice_id = "af_sarah"

        lang_map = {
            "a": "en-us",
            "b": "en-gb",
            "e": "es",
            "f": "fr-fr",
            "h": "hi",
            "i": "it",
            "p": "pt-br",
            "j": "ja",
            "z": "cmn",
        }
        lang_code = lang_map.get(voice_id[0], "en-us")

        kokoro = Kokoro(self._model_path, self._voices_path)

        parts = [p.strip() for p in re.split(r"\n+", message or "") if p.strip()]
        if not parts:
            self.throw("No text to synthesize", ErrorSeverity.WARNING)
            return

        chunks = []
        sample_rate = None
        for part in parts:
            try:
                samples, sr = kokoro.create(part, voice=voice_id, speed=1.0, lang=lang_code)
            except Exception:
                voice_id = "af_sarah"
                lang_code = "en-us"
                samples, sr = kokoro.create(part, voice=voice_id, speed=1.0, lang=lang_code)
            if sample_rate is None:
                sample_rate = sr
            chunks.append(samples)

        audio = np.concatenate(chunks, axis=0)
        sf.write(file, audio, int(sample_rate or 24000))

    def _ensure_model_files(self, cache_dir: str):
        model_path = os.path.join(cache_dir, "kokoro-v1.0.onnx")
        voices_path = os.path.join(cache_dir, "voices-v1.0.bin")

        if not os.path.exists(model_path):
            self._download(self.MODEL_URL, model_path)
        if not os.path.exists(voices_path):
            self._download(self.VOICES_URL, voices_path)

        self._model_path = model_path
        self._voices_path = voices_path

    def _download(self, url: str, dst: str):
        tmp = dst + ".tmp"
        try:
            with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
                f.write(r.read())
            os.replace(tmp, dst)
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            self.throw(f"Failed to download Kokoro asset: {e}", ErrorSeverity.ERROR)
