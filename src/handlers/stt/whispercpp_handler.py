from gettext import gettext as _
from ...utility.system import get_spawn_command, is_flatpak
from ...utility.background_process import BackgroundProcess
from ...utility.runtime_installer import RuntimeProject, fetch_json, get_runtime_installer
from ...utility.download_manager import current_download_task
from .stt import STTHandler
from ...handlers import ErrorSeverity, ExtraSettings
from ...ui.model_library import ModelLibraryWindow, LibraryModel, get_local_backend_label
from ...utility.model_icons import get_model_icon
import os
import subprocess
import time
import json
import socket
from gi.repository import GLib

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

GGML_FLAGS = (
    "-DGGML_NATIVE=ON",
    "-DGGML_AVX2=ON",
    "-DGGML_FMA=ON",
    "-DGGML_AVX512=OFF",
)

GGML_BACKENDS = {
    "cpu": (),
    "cpu_openblas": ("-DGGML_BLAS=ON", "-DGGML_BLAS_VENDOR=OpenBLAS"),
    "cuda": ("-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=native"),
    "rocm": ("-DGGML_HIPBLAS=ON", "-DAMDGPU_TARGETS=native"),
    "vulkan": ("-DGGML_VULKAN=ON",),
}


def fetch_whisper_cpp_versions():
    tags = fetch_json(
        "https://api.github.com/repos/ggerganov/whisper.cpp/tags?per_page=30"
    )
    versions = list(
        dict.fromkeys(
            tag["name"] for tag in tags if tag.get("name", "").startswith("v")
        )
    )
    return versions + ["master"] if versions else list(WHISPER_CPP.versions)


WHISPER_CPP = RuntimeProject(
    name="whisper.cpp",
    repository="https://github.com/ggerganov/whisper.cpp.git",
    binaries=("whisper-server", "whisper-cli"),
    cmake_flags=GGML_FLAGS
    + (
        "-DBUILD_SHARED_LIBS=OFF",
        "-DWHISPER_BUILD_TESTS=OFF",
        "-DWHISPER_BUILD_EXAMPLES=ON",
    ),
    backend_flags=GGML_BACKENDS,
    backend_labels={
        "cpu": _("CPU"),
        "cpu_openblas": _("CPU (OpenBLAS)"),
        "cuda": _("Nvidia (CUDA)"),
        "rocm": _("AMD (ROCm)"),
        "vulkan": _("Any GPU (Vulkan)"),
    },
    versions=(
        "v1.9.5",
        "v1.9.4",
        "v1.9.3",
        "v1.9.2",
        "v1.9.1",
        "v1.9.0",
        "v1.8.4",
        "master",
    ),
    fetch_versions=fetch_whisper_cpp_versions,
    preserve_patterns=("models/*.bin", "models/*.part"),
)


DEFAULT_WHISPER_CPP_VERSION = WHISPER_CPP.versions[0]


class WhisperCPPHandler(STTHandler):
    key = "whispercpp"
    runtime_project = WHISPER_CPP

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
        self._server = BackgroundProcess("whisper-server")
        self._server.register_atexit()
        self._current_model = None
        self._use_server = False
        self._server_port = None
        self.whisper_cpp_path = os.path.join(self.path, "whisper", "whisper.cpp")
        self.whisper_server_path = os.path.join(self.whisper_cpp_path, "build", "bin", "whisper-server")
        self.model_folder = os.path.join(self.whisper_cpp_path, "models")
        self.downloading = {}

    @staticmethod
    def requires_sandbox_escape() -> bool:
        return True

    def is_gpu_installed(self) -> bool:
        """Check if whisper.cpp is built with hardware acceleration"""
        if os.path.exists(self.whisper_server_path) and os.access(self.whisper_server_path, os.X_OK):
            return True
        return False

    def get_extra_settings(self) -> list:
        installed_models = self.get_models()
        settings = [
            ExtraSettings.InfoSetting(
                "installed_backend_status", _("Installed built-in backend"),
                get_local_backend_label(self),
            ),
            {
                "key": "model",
                "title": _("Model"),
                "description": _("Name of the Whisper model"),
                "type": "combo",
                "values": installed_models if installed_models else (("No models", ""),),
                "default": installed_models[0][1] if len(installed_models) > 0 else "",
                "website": "https://github.com/openai/whisper/blob/main/model-card.md#model-details",
            },
            ExtraSettings.EntrySetting("language", _("Language"), _("Language of the recognition. For example en, it..."), "auto"),
            ExtraSettings.NestedSetting("advanced_settings", _("Advanced Settings"), _("More advanced settings"), [
                ExtraSettings.ScaleSetting("temperature", _("Temperature"), _("Temperature to use"), 0.0, 0.0, 1.0, 2),
                ExtraSettings.MultilineEntrySetting("prompt", _("Prompt for the recognition"), _("Prompt to use for the recognition"), "")
            ])
        ]

        if self.is_installed():
            installed_ver = self.get_setting("installed_version", False, None)
            if installed_ver:
                settings.insert(
                    1,
                    ExtraSettings.InfoSetting(
                        "installed_version_status", _("Installed whisper.cpp version"),
                        installed_ver,
                    ),
                )

        # Hardware acceleration settings
        settings.append(
            ExtraSettings.ButtonSetting("library", _("Browse All Models"), _("Open the model library browser"), self.open_model_library, label=_("Model Library"))
        )

        if not self.is_gpu_installed():
            settings.append(
                ExtraSettings.ButtonSetting("install", _("Install WhisperCPP (Hardware Acceleration)"), _("Build whisper.cpp with hardware acceleration"), self.show_install_dialog, label=_("Install"))
            )
        else:
            settings.extend([
                ExtraSettings.ToggleSetting("gpu_acceleration", _("Hardware Acceleration"), _("Enable hardware acceleration"), False),
            ])
            if is_flatpak():
                settings.append(
                    ExtraSettings.ToggleSetting("use_system_server", _("Use System whisper-server"), _("Use system-installed whisper-server instead of built-in (requires whisper-server on host and sandbox escape)"), False)
                )
            settings.append(
                ExtraSettings.ButtonSetting("reinstall", _("Reinstall"), _("Rebuild whisper.cpp"), self.show_install_dialog, label=_("Reinstall"))
            )

        return settings

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
        # Only return download progress if actively downloading (progress < 1.0)
        if model in self.downloading and self.downloading[model]["progress"] < 1.0:
            return self.downloading[model]["progress"]
        # If model is installed, return 1.0 to indicate completion
        if self.is_model_installed(model):
            return 1.0
        return 0.0

    def install_model(self, model_name):
        if self.is_model_installed(model_name):
            os.remove(os.path.join(self.model_folder, "ggml-" + model_name + ".bin"))
            self.settings_update()
        else:
            self.downloading[model_name] = {"status": True, "progress": 0.0}
            self.settings_update()
            try:
                self.download_model_directly(model_name)
                self.downloading[model_name]["progress"] = 1.0
                GLib.idle_add(self.settings_update)
            finally:
                self.downloading.pop(model_name, None)
                GLib.idle_add(self.settings_update)

    def download_model_directly(self, model_name):
        """Direct download of whisper model as fallback"""
        import urllib.request
        base_url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
        model_filename = f"ggml-{model_name}.bin"
        url = f"{base_url}/{model_filename}"

        def update_progress(block_num, block_size, total_size):
            downloaded = min(block_num * block_size, total_size) if total_size > 0 else block_num * block_size
            if total_size > 0:
                self.downloading[model_name]["progress"] = min(downloaded / total_size, 0.99)
            if task is not None:
                task.check_cancelled()
                task.update(
                    phase=_("Downloading {name}").format(name=model_filename),
                    fraction=downloaded / total_size if total_size > 0 else None,
                    transferred_bytes=downloaded,
                    total_bytes=total_size if total_size > 0 else None,
                    cancellable=True,
                )

        target_path = os.path.join(self.model_folder, model_filename)
        partial_path = target_path + ".part"
        os.makedirs(self.model_folder, exist_ok=True)
        task = current_download_task()
        try:
            urllib.request.urlretrieve(url, partial_path, reporthook=update_progress)
            if task is not None:
                task.update(cancellable=False, phase=_("Finalizing model"))
            os.replace(partial_path, target_path)
        except Exception:
            if os.path.exists(partial_path):
                os.remove(partial_path)
            raise

    def is_model_installed(self, model_name):
        return os.path.exists(os.path.join(self.model_folder, "ggml-" + model_name + ".bin")) and not self.downloading.get(model_name)

    def get_models(self):
        return tuple((model["display_name"], model["model_name"]) for model in WHISPER_MODELS if self.is_model_installed(model["model_name"]))

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai-whisper"]

    def is_installed(self) -> bool:
        return all(
            os.path.isfile(os.path.join(self.whisper_cpp_path, "build", "bin", name))
            for name in self.runtime_project.binaries
        )

    def install(self):
        version = self.get_setting("installed_version", False, DEFAULT_WHISPER_CPP_VERSION)
        installer = get_runtime_installer(self.runtime_project, self.whisper_cpp_path)
        installer.build(version=version, log=print)
        self.on_runtime_installed("cpu", version)
        if not self.is_model_installed("tiny"):
            self.install_model("tiny")
        self._is_installed_cache = None

    @staticmethod
    def _find_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _start_process(self):
        """Start the persistent whisper-server process with model loaded"""
        model_name = self.get_setting("model")

        # Restart only when the model changes or the server isn't running.
        if self._server.is_running:
            if self._current_model == model_name:
                return
            self._stop_process()

        path = os.path.join(self.path, "whisper")

        # Try to use whisper-server if available, otherwise fall back to whisper-cli
        if self.is_gpu_installed():
            server_path = self.whisper_server_path
            cli_path = os.path.join(self.whisper_cpp_path, "build", "bin", "whisper-cli")
        else:
            server_path = os.path.join(path, "whisper.cpp/build/bin/whisper-server")
            cli_path = os.path.join(path, "whisper.cpp/build/bin/whisper-cli")

        # Check if we should use hardware acceleration
        use_gpu = self.get_setting("gpu_acceleration", False, False)
        use_system_server = is_flatpak() and self.get_setting("use_system_server", False, False)

        if os.path.exists(server_path) and (use_gpu or use_system_server):
            uses_built_binary = not use_system_server
            # The built GPU binary is linked against host CUDA (libcudart.so, ...)
            # and sibling libggml*.so shipped next to it in build/bin. Inside the
            # Flatpak sandbox neither is available, so run it on the host via
            # flatpak-spawn and expose the bin dir through LD_LIBRARY_PATH.
            spawn_on_host = is_flatpak() and (use_system_server or (uses_built_binary and use_gpu))
            if use_system_server:
                cmd = get_spawn_command() + ["whisper-server"]
            elif spawn_on_host:
                cmd = get_spawn_command() + [server_path]
            else:
                cmd = [server_path]

            self._use_server = True
        else:
            cmd = [cli_path]
            self._use_server = False

        model_path = os.path.join(self.model_folder, "ggml-" + model_name + ".bin")

        if self._use_server:
            self._server_port = self._find_free_port()
            cmd.extend([
                "-m", model_path,
                "--host", "127.0.0.1",
                "--port", str(self._server_port),
                "-l", self.get_setting("language"),
            ])
            try:
                print(f"Starting whisper-server with model: {model_name}")
                start_kwargs = {
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                }
                # Run the built server from its bin dir and make its sibling
                # libggml*.so discoverable via LD_LIBRARY_PATH (otherwise a
                # system-wide libllama/ggml can be loaded, causing symbol errors).
                if uses_built_binary:
                    bin_dir = os.path.dirname(server_path)
                    start_kwargs["cwd"] = bin_dir
                    env = os.environ.copy()
                    ld_path = env.get("LD_LIBRARY_PATH", "")
                    env["LD_LIBRARY_PATH"] = f"{bin_dir}{os.pathsep}{ld_path}" if ld_path else bin_dir
                    if spawn_on_host:
                        # flatpak-spawn runs on the host; forward via --env
                        cmd[1:1] = [f"--env=LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']}"]
                    else:
                        start_kwargs["env"] = env
                proc = self._server.start(cmd, **start_kwargs)
                self._current_model = model_name
                # Wait for server to start
                time.sleep(3)
                # Check if process is still running
                if proc.poll() is not None:
                    stdout = self._server.get_output(which="stdout")
                    stderr = self._server.get_output(which="stderr")
                    print(f"Server stdout: {stdout}")
                    print(f"Server stderr: {stderr}")
                    raise RuntimeError(f"Server failed to start: {stderr}")
                print("Server started successfully")
            except Exception as e:
                self.throw("Error starting Whisper server: " + str(e), ErrorSeverity.ERROR)
                self._server.stop()
                self._use_server = False
        else:
            # For whisper-cli, we can't keep it loaded persistently
            self._current_model = model_name
            return

    def _stop_process(self):
        """Stop the persistent whisper process"""
        self._server.stop()
        self._current_model = None

    def recognize_file(self, path):
        print(f"Recognizing file: {path}")
        self._start_process()

        if self._use_server and self._server.is_running:
            print("Using server mode")
            return self._recognize_with_server(path)
        else:
            print("Using CLI mode")
            return self._recognize_with_cli(path)

    def _recognize_with_server(self, path):
        """Recognize using the whisper-server HTTP API"""
        import urllib.request
        import urllib.error

        try:
            boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'

            with open(path, 'rb') as f:
                audio_data = f.read()

            filename = os.path.basename(path)

            body = []
            body.append(f'--{boundary}'.encode())
            body.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode())
            body.append(b'Content-Type: audio/wav')
            body.append(b'')
            body.append(audio_data)
            body.append(f'--{boundary}--'.encode())

            body = b'\r\n'.join(body)

            url = f"http://127.0.0.1:{self._server_port}/inference"
            headers = {
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'Content-Length': str(len(body))
            }

            req = urllib.request.Request(url, data=body, headers=headers, method='POST')

            with urllib.request.urlopen(req, timeout=300) as response:
                response_data = response.read().decode('utf-8')
                print(f"Server response: {response_data}")
                result = json.loads(response_data)

                text = result.get('text', '')
                if not text:
                    text = result.get('transcription', '')
                if not text:
                    text = result.get('result', '')
                text = text.strip()
                print(f"Whisper output: {text}")
                return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.read() else ""
            print(f"HTTP Error {e.code}: {error_body}")
            self.throw(f"Server HTTP error {e.code}: {error_body}", ErrorSeverity.ERROR)
            self._stop_process()
            return ""
        except Exception as e:
            print(f"Recognition error: {str(e)}")
            self.throw("Error recognizing file with server: " + str(e), ErrorSeverity.ERROR)
            self._stop_process()
            return ""

    def _recognize_with_cli(self, path):
        """Recognize using whisper-cli subprocess"""
        model_path = os.path.join(self.model_folder, "ggml-" + self.get_setting("model") + ".bin")

        # Check if we should use GPU build
        if self.is_gpu_installed() and self.get_setting("gpu_acceleration", False, False):
            exec_path = os.path.join(self.whisper_cpp_path, "build", "bin", "whisper-cli")
            bin_dir = os.path.dirname(exec_path)
            # Built GPU binary needs host CUDA + sibling libggml*.so; run on host
            # with LD_LIBRARY_PATH pointing at its bin dir.
            ld_path = bin_dir
            if is_flatpak():
                cmd = get_spawn_command() + [f"--env=LD_LIBRARY_PATH={ld_path}", exec_path]
            else:
                cmd = [exec_path]
        else:
            exec_path = os.path.join(self.path, "whisper", "whisper.cpp/build/bin/whisper-cli")
            cmd = [exec_path]

        cmd.extend([
            "-f", path,
            "-m", model_path,
            "--no-prints",
            "-nt",
            "-l", self.get_setting("language"),
            "-tp", str(self.get_setting("temperature")),
        ])

        prompt = self.get_setting("prompt")
        if prompt:
            cmd.extend(["--prompt", prompt])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                text = result.stdout.strip()
                print(f"Whisper output: {text}")
                return text
            else:
                self.throw(f"Whisper CLI error: {result.stderr}", ErrorSeverity.ERROR)
                return ""
        except Exception as e:
            self.throw("Error recognizing file: " + str(e), ErrorSeverity.ERROR)
            return ""

    # Model library integration
    def fetch_models(self):
        """Fetch models for the model library window"""
        models = []
        icon_name, icon_color = get_model_icon("whisper")
        for model in WHISPER_MODELS:
            models.append(LibraryModel(
                id=model["model_name"],
                name=model["display_name"],
                description=f"Size: {model['size']}",
                tags=["whisper", "stt"],
                is_pinned=False,
                is_installed=self.is_model_installed(model["model_name"]),
                icon_name=icon_name,
                icon_color=icon_color,
                size_bytes=model["size_bytes"],
            ))
        return models

    def model_installed(self, model: str) -> bool:
        return self.is_model_installed(model)

    def open_model_library(self, button):
        root = button.get_root()
        win = ModelLibraryWindow(self, root)
        win.present()

    def show_install_dialog(self, button):
        from ...ui.widgets.runtime_installer import show_runtime_installer

        show_runtime_installer(
            self,
            self.runtime_project,
            self.whisper_cpp_path,
            button,
            on_installed=self.on_runtime_installed,
            selected_version=self.get_setting(
                "installed_version", False, DEFAULT_WHISPER_CPP_VERSION
            ),
        )

    def on_runtime_installed(self, backend, version=None, prebuilt=False):
        self.set_setting("gpu_acceleration", True)
        self.set_setting("installed_backend", backend)
        if version:
            self.set_setting("installed_version", version)
