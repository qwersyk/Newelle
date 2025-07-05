from os import walk
import subprocess
from .tts import TTSHandler
from ...utility.pip import install_module, find_module
from ..handler import ErrorSeverity
from ...handlers import ExtraSettings

class KokoroTTSHandler(TTSHandler):
    key = "kokoro"
    def install(self):
        extra_deps = "fugashi jaconv mojimoji mecab-python3 unidic-lite"
        index_url = " --extra-index-url https://download.pytorch.org/whl/cpu --trusted-host download.pytorch.org"
        install_module("kokoro==0.9.4 soundfile espeakng-loader " + extra_deps + index_url, self.pip_path, update=False)
        if not self.is_installed():
            self.throw("Kokoro installation failed", ErrorSeverity.ERROR)

    def is_installed(self) -> bool:
        return find_module("kokoro") is not None and find_module("soundfile") is not None

    def get_voices(self):
        voices = "af_alloy, af_aoede, af_bella, af_heart, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky".split(", ")
        voices += "am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck".split(", ")
        voices += "bf_alice, bf_emma, bf_isabella, bf_lily, bm_daniel, bm_fable, bm_george, bm_lewis".split(", ")
        # Espeak required for non english to work
        voices += ["ef_dora", "em_alex", "em_santa"]
        voices += ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"]
        voices += ["ff_siwis"]
        voices += "if_sara, im_nicola".split(", ")
        voices += ["pf_dora", "pm_alex", "pm_santa"]
        voices += "jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro, jm_kumo".split(", ")
        voices += "zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang".split(", ")
        flags = {"a": "🇺🇸", "b": "🇬🇧","e": "🇪🇸", "f": "🇫🇷", "h": "🇮🇳", "p": "🇧🇷", "i": "🇮🇹", "j": "🇯🇵", "z": "🇨🇳"}
        genders = {"m": "🚹", "f": "🚺"}
        v = tuple()
        for voice in voices:
            nationality = voice[0]
            gender = voice[1]
            name = voice[3:]
            v += ((flags[nationality] + genders[gender] + " " + name.capitalize(), voice),)
        return v

    def save_audio(self, message, file):
        from kokoro import KPipeline
        import soundfile as sf
        voice = self.get_current_voice()
        pipeline = KPipeline(lang_code=self.get_current_voice()[0]) # <= make sure lang_code matches voice
        text = message

        generator = pipeline(
            text, voice=self.get_current_voice(), # <= change voice here
            speed=1, split_pattern=r'\n+'
        )
        for i, (gs, ps, audio) in enumerate(generator):
            sf.write(file, audio, 24000) 
