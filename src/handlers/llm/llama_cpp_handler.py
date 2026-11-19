from ...handlers.llm import OpenAIHandler
from ...handlers.extra_settings import ExtraSettings
from ...utility.system import is_flatpak, get_spawn_command, detect_cuda_version
from ...utility.background_process import BackgroundProcess
from ..llama_cpp_installer import LlamaCPPInstallerMixin
from ...utility.download_manager import current_download_task
from ...utility.huggingface_download import download_huggingface_file
from ...handlers import ErrorSeverity
from gettext import gettext as _
import os
import threading
import shlex
import socket
import time
import json
from gi.repository import GLib
from ...ui.model_library import (
    LibraryModel,
    ModelLibraryWindow,
    get_local_backend_label,
)
from ...utility.model_icons import get_model_icon
import requests

class LlamaCPPHandler(LlamaCPPInstallerMixin, OpenAIHandler):
    key = "llamacpp"

    def get_cache_path(self):
        cache_dir = self.path
        return os.path.join(cache_dir, "llamacpp_models.json")

    def update_library_cache(self):
        url = "https://raw.githubusercontent.com/FrancescoCaracciolo/llm-library-scraper/refs/heads/main/lmstudio/model_list.json"
        try:
            response = requests.get(url, timeout=30)
            data = response.json()
            cache_path = self.get_cache_path()
            with open(cache_path, "w") as f:
                json.dump(data, f)
            self.library_data = data
        except Exception as e:
            print(f"Error updating library cache: {e}")
    
    def get_models(self, manual=False, multithread=False):
        self.set_setting("models", json.dumps(["custom"]))
        self.update_library_cache()

    def __init__(self, settings, path):
        super().__init__(settings, path)
        print(detect_cuda_version())
        self.venv_path = os.path.join(self.path, "venv")
        self.llama_cpp_path = os.path.join(self.path, "llama.cpp")
        self.llama_server_path = os.path.join(self.llama_cpp_path, "build", "bin", "llama-server")
        self.model_folder = os.path.join(self.path, "custom_models")
        self.server = BackgroundProcess("llama-server")
        self.server.register_atexit()
        self._killing_server = False
        self.port = None
        self.loaded_model = None
        self.loaded_mmproj = None
        self.loaded_custom_args = None
        self.models = self.get_custom_model_list()
        self.loaded_on = self.get_setting("gpu_acceleration", False, False)
        self.set_setting("api", "no")
        self.downloading = {}
         
        self.library_data = []
        cache_path = self.get_cache_path()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    self.library_data = json.load(f)
            except:
                self.library_data = []
                threading.Thread(target=self.update_library_cache).start()
        else:
            threading.Thread(target=self.update_library_cache).start()

        if not os.path.exists(self.model_folder):
            try:
                os.makedirs(self.model_folder)
            except:
                pass
    
    def get_custom_models_dir(self) -> str:
        """Return the user-configured custom models directory, expanded, or empty string."""
        custom_dir = self.get_setting("custom_models_dir", False, "") or ""
        if custom_dir:
            custom_dir = os.path.expanduser(custom_dir)
        return custom_dir

    def get_custom_model_list(self, update=False): 
        """Get models in the user folder

        Returns:
            list: list of models 
        """
        file_list = tuple()
        seen = set()
        for root, _, files in os.walk(self.model_folder):
            for file in files:
                if file.endswith('.gguf'):
                    file_name = file.removesuffix('.gguf')
                    if "mmproj" in file_name:
                        continue
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list += ((file_name, relative_path), )
                    seen.add(file_name)

        custom_dir = self.get_custom_models_dir()
        if custom_dir and os.path.isdir(custom_dir) and os.path.abspath(custom_dir) != os.path.abspath(self.model_folder):
            for root, _, files in os.walk(custom_dir):
                for file in files:
                    if file.endswith('.gguf'):
                        file_name = file.removesuffix('.gguf')
                        if "mmproj" in file_name:
                            continue
                        abs_path = os.path.abspath(os.path.join(root, file))
                        display_name = file_name
                        # Disambiguate if a model with the same name exists in the default folder
                        if display_name in seen:
                            display_name = f"{file_name} (custom)"
                        file_list += ((display_name, abs_path), )
                        seen.add(display_name)
        if update:
            self.settings_update()
        return file_list
    
    def get_mmproj_list(self, update=False):
        """Get mmproj files in the model folder for vision support

        Returns:
            list: list of mmproj files
        """
        file_list = tuple()
        seen = set()
        for root, _, files in os.walk(self.model_folder):
            for file in files:
                if 'mmproj' in file.lower() and file.endswith('.gguf'):
                    file_name = file.removesuffix('.gguf')
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list+=((file_name, relative_path),)
                    seen.add(file_name)

        custom_dir = self.get_custom_models_dir()
        if custom_dir and os.path.isdir(custom_dir) and os.path.abspath(custom_dir) != os.path.abspath(self.model_folder):
            for root, _, files in os.walk(custom_dir):
                for file in files:
                    if 'mmproj' in file.lower() and file.endswith('.gguf'):
                        file_name = file.removesuffix('.gguf')
                        abs_path = os.path.abspath(os.path.join(root, file))
                        display_name = file_name
                        if display_name in seen:
                            display_name = f"{file_name} (custom)"
                        file_list += ((display_name, abs_path), )
                        seen.add(display_name)
        if update:
            self.settings_update()
        return file_list

    def _resolve_model_path(self, value: str) -> str:
        """Resolve a model setting value (which may be a relative path under the
        default model folder or an absolute path from a custom directory) to an
        absolute path on disk."""
        if not value:
            return ""
        if os.path.isabs(value):
            return value
        return os.path.join(self.model_folder, value)
    
    def is_gpu_installed(self) -> bool:
        # Check if llama.cpp is built (hardware backend installation)
        if os.path.exists(self.llama_server_path) and os.access(self.llama_server_path, os.X_OK):
            return True
        return False

    def get_extra_settings(self) -> list:
        custom_model_list = self.get_custom_model_list()
        mmproj_list = self.get_mmproj_list()
        settings =  [
                ExtraSettings.InfoSetting(
                    "installed_backend_status", _("Installed built-in backend"),
                    get_local_backend_label(self),
                ),
                ExtraSettings.ComboSetting("model", _("Model"), _("Model to use"), self.get_custom_model_list(), 
                custom_model_list[0][1] if len(custom_model_list) > 0 else "", 
                refresh=lambda button: self.get_custom_model_list(True),
                folder=self.model_folder),
            ]
        settings.extend(
            [
                ExtraSettings.ButtonSetting("library", _("Model Library"), _("Open the model library"), self.open_model_library, label=_("Model Library")),
                ExtraSettings.ToggleSetting("enable_mmproj", _("Enable Vision (MMProj)"), _("Enable vision support using mmproj file"), False, update_settings=True),
            ]
        )
        settings += [ExtraSettings.ComboSetting("mmproj", _("MMProj (Vision)"), _("Multimodal projection file for vision support"), 
                mmproj_list,
                mmproj_list[0][1] if len(mmproj_list) > 0 else "",
                refresh=lambda button: self.get_mmproj_list(True),
                folder=self.model_folder)]

        settings.append(
            ExtraSettings.EntrySetting(
                "custom_models_dir",
                _("Custom Models Directory"),
                _("Additional directory to scan for .gguf model files (leave empty to disable)"),
                "",
            )
        )

        settings.append(
            ExtraSettings.MultilineEntrySetting(
                "custom_args",
                _("Custom Arguments"),
                _("Additional command-line arguments passed to llama-server (e.g. --threads 4 --no-mmap). Leave empty for none."),
                "",
            )
        )

        if not self.is_gpu_installed():
            settings.append(
                ExtraSettings.ButtonSetting("install", _("Install LlamaCPP (Hardware Acceleration)"), _("Build llama.cpp with hardware acceleration"), self.show_install_dialog, label=_("Install"))
            )
        else:
            settings.extend([
                ExtraSettings.ToggleSetting("gpu_acceleration", _("Hardware Acceleration"), _("Enable hardware acceleration"), False),
            ])
            if is_flatpak():
                settings.append(
                    ExtraSettings.ToggleSetting("use_system_server", _("Use System llama-server"), _("Use system-installed llama-server instead of built-in (requires llama-server on host and sandbox escape)"), False)
                )
            settings.append(
                ExtraSettings.ButtonSetting("reinstall", _("Reinstall"), _("Rebuild llama.cpp"), self.show_install_dialog, label=_("Reinstall"))
            )
        extra_settings = self.build_extra_settings("LlamaCPP", False, True, False, True, False, None, None, False, False, True)
        extra_settings.extend([
            ExtraSettings.SpinSetting("ctx", _("Context Size"), _("Context size to use, 0 = load from model"), default=0, min=0, max=1200000, page=1024, step=512),
        ])
        settings.extend(extra_settings)
        return settings

    # Model Loading
    def get_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def load_model(self, model):
        model = self.get_setting("model")
        ctx = self.get_setting("ctx", 2048, 0)
        enable_mmproj = self.get_setting("enable_mmproj", False, False)
        mmproj = self.get_setting("mmproj", False, None) if enable_mmproj else ""
        if mmproj is None and len(self.get_mmproj_list()) > 0:
            mmproj = self.get_mmproj_list()[0][1]
        custom_args = self.get_setting("custom_args", False, "")
        if self.loaded_model == model and self.loaded_on == self.get_setting("gpu_acceleration", False, False) and self.loaded_ctx == ctx and self.loaded_mmproj == mmproj and self.loaded_custom_args == custom_args:
            return True
        path = self._resolve_model_path(self.get_setting("model"))
        if not path or not os.path.exists(path):
             return False
        
        # Kill existing server process before starting a new one
        self.kill_server()
        
        self.port = self.get_free_port()

        # Use built llama.cpp server if hardware acceleration is enabled and available
        # Or use system server if specified (for Flatpak)
        use_system_server = is_flatpak() and self.get_setting("use_system_server", False, False)
        if use_system_server:
            cmd_path = "llama-server"
        elif self.get_setting("gpu_acceleration", False, False) and self.is_gpu_installed():
            cmd_path = self.llama_server_path
        else:
            cmd_path = "llama-server"
        cmd = [cmd_path, "--model", path, "--port", str(self.port), "--host", "127.0.0.1", "-c", str(ctx), "--reasoning-format", "none"]

        # Append user-supplied custom arguments
        if custom_args:
            cmd.extend(shlex.split(custom_args))

        # Add mmproj for vision support if enabled and configured
        if self.get_setting("enable_mmproj") and mmproj:
            mmproj_path = self._resolve_model_path(mmproj)
            if os.path.exists(mmproj_path):
                cmd.extend(["--mmproj", mmproj_path])
        # Use flatpak-spawn for compiled or GPU-accelerated prebuilt binaries in Flatpak
        is_prebuilt = self.get_setting("prebuilt", False, False)
        prebuilt_backend = self.get_setting("prebuilt_backend", False, None)
        if prebuilt_backend is None:
            # Backward compatibility: infer from the legacy prebuilt_cuda flag
            prebuilt_backend = "cuda" if self.get_setting("prebuilt_cuda", False, False) else "cpu"
        # GPU backends (cuda/rocm/vulkan/openvino/sycl) bundle native libs and
        # need host GPU drivers, so their prebuilts must spawn on the host.
        # CPU prebuilts run fine inside the sandbox.
        is_gpu_prebuilt = is_prebuilt and prebuilt_backend != "cpu"
        uses_built_server = cmd_path == self.llama_server_path
        spawn_on_host = (is_flatpak() and self.is_gpu_installed() and self.get_setting("gpu_acceleration", False, False) and (is_gpu_prebuilt or not is_prebuilt)) or use_system_server
        if spawn_on_host:
            cmd = get_spawn_command() + cmd

        # The built llama-server links against sibling libllama.so / libggml*.so
        # shipped next to it in build/bin. Run it from that directory and expose
        # it via LD_LIBRARY_PATH so the correct libraries are loaded instead of
        # any system-wide libllama.so (which causes "undefined symbol" errors).
        start_kwargs = {}
        if uses_built_server:
            bin_dir = os.path.dirname(self.llama_server_path)
            start_kwargs["cwd"] = bin_dir
            env = os.environ.copy()
            ld_path = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{bin_dir}{os.pathsep}{ld_path}" if ld_path else bin_dir
            if spawn_on_host:
                # flatpak-spawn runs on the host; pass LD_LIBRARY_PATH through --env
                cmd[1:1] = [f"--env=LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']}"]
            else:
                start_kwargs["env"] = env

        self.server.start(
            cmd,
            on_crash=lambda: GLib.idle_add(
                self.throw,
                "llama-server crashed unexpectedly. Check terminal output for details.",
                ErrorSeverity.ERROR,
            ),
            **start_kwargs,
        )
        self._killing_server = False
        self.loaded_model = model
        self.loaded_on = self.get_setting("gpu_acceleration", False, False)
        self.loaded_ctx = ctx
        self.loaded_mmproj = mmproj
        self.loaded_custom_args = custom_args
        # Wait for server to potentially start
        url = f"http://localhost:{self.port}/v1/models"
        start_time = time.time()
        while time.time() - start_time < 60: # 60 seconds timeout
            try:
                if requests.get(url, timeout=1).status_code == 200:
                    return True
            except:
                pass
            time.sleep(0.5) 
        return False
    
    def kill_server(self):
        self.loaded_model = None
        self._killing_server = True
        self.server.stop()

    def destroy(self):
        self.kill_server()

    # Model library
    def fetch_models(self):
        data = self.library_data
        models = []
        for model in data:
            tags = model["tags"] + model["capabilities"].split("\n")
            installed = self.model_installed(model["title"])
            model_path = os.path.join(self.model_folder, model["title"] + ".gguf")
            icon_name, icon_color = get_model_icon(
                model["title"],
                tags,
            )
            models.append(LibraryModel(
                id=model["title"],
                name=model["title"],
                description=model["description"],
                tags=tags,
                is_pinned=installed,
                is_installed=installed,
                icon_name=icon_name,
                icon_color=icon_color,
                size_bytes=os.path.getsize(model_path) if installed else None,
                can_offload=True,
            ))
        for model_name, model_file in self.models:
            if model_name not in [m.id for m in models]:
                icon_name, icon_color = get_model_icon(model_name)
                models = [LibraryModel(
                    id=model_name,
                    name=model_name,
                    description=model_file + " - custom added model",
                    tags=["custom"],
                    is_pinned=True,
                    is_installed=True,
                    icon_name=icon_name,
                    icon_color=icon_color,
                    size_bytes=os.path.getsize(self._resolve_model_path(model_file)),
                    can_offload=True,
                )] + models
        return models

    def get_model_info_by_title(self, title: str) -> dict:
        data = self.library_data
        for model in data:
            if model["title"] == title:
                return model
        return None

    def model_installed(self, model: str) -> bool:
        return os.path.exists(os.path.join(self.model_folder, model + ".gguf"))

    def get_percentage(self, model: str) -> float:
        if model in self.downloading:
            return self.downloading[model]["progress"]
        return 0.0

    def select_best_gguf(self, files):
        """
        Selects the best GGUF file based on a priority list of quantizations.
        Priority is given to Q4_K_M (balanced) and Q5_K_M (higher quality).
        """
        gguf_files = [f for f in files if f.endswith(".gguf")]
        
        if not gguf_files:
            return None

        # Priority list for automatic selection
        # Q4_K_M is widely considered the best balance of speed/size/perplexity
        priorities = ["Q4_K_M", "Q5_K_M", "Q4_K_S", "Q4_0", "Q8_0"]
        
        print(f"Found {len(gguf_files)} GGUF files.")
        
        for priority in priorities:
            for file in gguf_files:
                if priority.lower() in file.lower():
                    print(f"Selected recommended quantization: {priority}")
                    return file
        
        # Fallback: Sort by length (shorter usually means cleaner names) and pick first
        fallback = sorted(gguf_files, key=len)[0]
        print(f"No standard quantization tag found in priority list. Defaulting to: {fallback}")
        return fallback

    def pull_model(self, url: str):
        if "/" in url:
            if not url.startswith("https://huggingface.co/"):
                url = "https://huggingface.co/" + url
            info = self.get_model_info_by_title(url)
            if not info:
                self.library_data = [{"title": url, "description": "User added model", "tags": ["custom"], "links": [url], "capabilities": ""}] + self.library_data
            self.install_model(url, url)
        else:
            self.install_model(url)
    
    def install_model(self, model: str, gguf_file: str = None):
        if self.model_installed(model):
            os.remove(os.path.join(self.model_folder, model + ".gguf"))
            self.settings_update()
        else:
            info = self.get_model_info_by_title(model)
            links = info["links"]
            if gguf_file is None:
                gguf_link = next((link for link in links if link.endswith("-GGUF")), None)
            else:
                gguf_link = gguf_file    
            parts = gguf_link.split("huggingface.co/")[-1].split("/")
            repo_id = f"{parts[0]}/{parts[1]}"
            print(f"Repo ID: {repo_id}")
            from huggingface_hub import HfApi
            api = HfApi()
            files = api.list_repo_files(repo_id)
            gguf_file = self.select_best_gguf(files)
            self.downloading[model] = {"status": True, "progress": 0.0}
            task = current_download_task()
            if task is not None:
                task.update(
                    phase=_("Downloading {name}").format(name=gguf_file),
                    reset_progress=True,
                    cancellable=True,
                )
            try:
                def update_local_progress(transferred, total):
                    self.downloading[model]["progress"] = (
                        transferred / total if total else 0.0
                    )

                download_huggingface_file(
                    repo_id,
                    gguf_file,
                    os.path.join(self.model_folder, model + ".gguf"),
                    task=task,
                    progress_callback=update_local_progress,
                )
                self.settings_update()
            finally:
                self.downloading.pop(model, None)

    def open_model_library(self, button):
        root = button.get_root()
        win = ModelLibraryWindow(self, root)
        win.present()


    # Override get_setting and set_setting to avoid reloading the model
    def get_setting(self, key: str, search_default = True, return_value = None):
        if key == "endpoint":
            return f"http://localhost:{self.port}/v1"
        return super().get_setting(key, search_default, return_value)

    def set_setting(self, key: str, value):
        return super().set_setting(key, value)
