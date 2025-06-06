from ...utility.strings import quote_string
from ...utility.system import get_spawn_command
from .stt import STTHandler
from ...handlers import ErrorSeverity, ExtraSettings
import os 
import subprocess

WHISPER_MODELS = [
    {"model_name": "tiny", "display_name": "Tiny", "size": "75 MiB", "size_bytes": 78643200},
    {"model_name": "tiny.en", "display_name": "Tiny (English)", "size": "75 MiB", "size_bytes": 78643200},
    {"model_name": "base", "display_name": "Base", "size": "142 MiB", "size_bytes": 148943872},
    {"model_name": "base.en", "display_name": "Base (English)", "size": "142 MiB", "size_bytes": 148943872},
    {"model_name": "small", "display_name": "Small", "size": "466 MiB", "size_bytes": 488622080},
    {"model_name": "small.en", "display_name": "Small (English)", "size": "466 MiB", "size_bytes": 488622080},
    {"model_name": "small.en-tdrz", "display_name": "Small (English TDRZ)", "size": "465 MiB", "size_bytes": 487577600},
    {"model_name": "medium", "display_name": "Medium", "size": "1.5 GiB", "size_bytes": 1610612736},
    {"model_name": "medium.en", "display_name": "Medium (English)", "size": "1.5 GiB", "size_bytes": 1610612736},
    {"model_name": "large-v1", "display_name": "Large v1", "size": "2.9 GiB", "size_bytes": 3114473472},
    {"model_name": "large-v2", "display_name": "Large v2", "size": "2.9 GiB", "size_bytes": 3114473472},
    {"model_name": "large-v2-q5_0", "display_name": "Large v2 Q5_0", "size": "1.1 GiB", "size_bytes": 1181116006},
    {"model_name": "large-v3", "display_name": "Large v3", "size": "2.9 GiB", "size_bytes": 3114473472},
    {"model_name": "large-v3-q5_0", "display_name": "Large v3 Q5_0", "size": "1.1 GiB", "size_bytes": 1181116006},
    {"model_name": "large-v3-turbo", "display_name": "Large v3 Turbo", "size": "1.5 GiB", "size_bytes": 1610612736},
    {"model_name": "large-v3-turbo-q5_0", "display_name": "Large v3 Turbo Q5_0", "size": "547 MiB", "size_bytes": 573513728},
]
class WhisperCPPHandler(STTHandler):
    key = "whispercpp"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
    @staticmethod
    def requires_sandbox_escape() -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the Whisper model"),
                "type": "combo",
                "values": self.get_models(),
                "default": "tiny",
                "website": "https://github.com/openai/whisper/blob/main/model-card.md#model-details",
            },
            ExtraSettings.EntrySetting("language", _("Language"), _("Language of the recognition."), "auto"),
            ExtraSettings.NestedSetting("model_library", _("Model Library"), _("Manage Whisper models"), self.get_model_library()),
            ExtraSettings.NestedSetting("advanced_settings", _("Advanced Settings"), _("More advanced settings"), [
                ExtraSettings.ScaleSetting("temperature", _("Temperature"), _("Temperature to use"), 0.0, 0.0, 1.0, 2),
                ExtraSettings.MultilineEntrySetting("prompt", _("Prompt for the recognition"), _("Prompt to use for the recognition"), "")
            ])
        ]
  
    def get_model_library(self):
        res = []
        for model in WHISPER_MODELS:
            res.append(
                ExtraSettings.DownloadSetting(
                    model["model_name"],
                    model["display_name"],
                    "Size: " + model["size"],
                    self.is_model_installed(model["model_name"]),
                    lambda x, model=model : self.install_model(model["model_name"]),
                    lambda x, model=model : self.get_percentage(model["model_name"]),
                )
            )
        return res
    def get_percentage(self, model: str):
        file_size = os.path.getsize(os.path.join(self.path, "whisper", "whisper.cpp/models/ggml-" + model + ".bin")) 
        model_info = [x for x in WHISPER_MODELS if x["model_name"] == model][0]
        return (file_size / model_info["size_bytes"])

    def install_model(self, model_name):
        if self.is_model_installed(model_name):
            os.remove(os.path.join(self.path, "whisper", "whisper.cpp/models/ggml-" + model_name + ".bin"))
        else:
            path = os.path.join(self.path, "whisper/whisper.cpp/models/download-ggml-model.sh")
            f = subprocess.check_output(get_spawn_command() + ["bash", "-c", f"sh {path} " + model_name])
            print(f) 
    
    def is_model_installed(self, model_name):
        return os.path.exists(os.path.join(self.path, "whisper", "whisper.cpp/models/ggml-" + model_name + ".bin"))
    def get_models(self):
        return tuple((model["display_name"], model["model_name"]) for model in WHISPER_MODELS if self.is_model_installed(model["model_name"]))

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai-whisper"]

    def is_installed(self) -> bool:
        path = os.path.join(self.path, "whisper")
        exec_path = os.path.join(path, "whisper.cpp/build/bin/whisper-cli")
        return os.path.exists(exec_path)

    def install(self):
        os.makedirs(os.path.join(self.path, "whisper"), exist_ok=True)
        print("Installing whisper...")
        path = os.path.join(self.path, "whisper")
        exec_path = os.path.join(path, "whisper.cpp/build/bin/whisper-cli")
        installation_script = f"cd {path} && git clone https://github.com/ggerganov/whisper.cpp.git && cd whisper.cpp && sh ./models/download-ggml-model.sh tiny && cmake -B build && cmake --build build -j --config Release" 
        out = subprocess.check_output(get_spawn_command() + ["bash", "-c", installation_script]) 
        if not os.path.exists(exec_path):    
            self.throw("Error installing Whisper: " + out.decode("utf-8"), ErrorSeverity.ERROR)

    def recognize_file(self, path):
        recpath = path
        path = os.path.join(self.path, "whisper")
        exec_path = os.path.join(path, "whisper.cpp/build/bin/whisper-cli")
        recognize_script = exec_path + " -f " + recpath + " -m " + os.path.join(self.path, "whisper", "whisper.cpp/models/ggml-" + self.get_setting("model") + ".bin" + " --no-prints -nt -l " + self.get_setting("language") + " -tp " + str(self.get_setting("temperature")) + "--prompt" + quote_string(self.get_setting("prompt")) )
        try:
            out = subprocess.check_output(get_spawn_command() + ["bash", "-c", recognize_script])
            print(out)
            return out.decode("utf-8").lstrip().strip()
        except Exception as e:
            self.throw("Error recognizing file: " + str(e), ErrorSeverity.ERROR)
            return ""
