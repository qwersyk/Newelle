import gettext
from ...utility.strings import quote_string
from ...utility.system import get_spawn_command, can_escape_sandbox, is_flatpak
from .stt import STTHandler
from ...handlers import ErrorSeverity, ExtraSettings
from ...ui.model_library import ModelLibraryWindow, LibraryModel
import os
import subprocess
import threading
import time
import shutil
import json
from gi.repository import Gtk, Adw, GLib, Gdk
import requests

_ = gettext.gettext

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
        self._process = None
        self._process_lock = threading.Lock()
        self._current_model = None
        self._use_server = False
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
            ExtraSettings.NestedSetting("model_library", _("Model Library"), _("Manage Whisper models"), self.get_model_library()),
            ExtraSettings.NestedSetting("advanced_settings", _("Advanced Settings"), _("More advanced settings"), [
                ExtraSettings.ScaleSetting("temperature", _("Temperature"), _("Temperature to use"), 0.0, 0.0, 1.0, 2),
                ExtraSettings.MultilineEntrySetting("prompt", _("Prompt for the recognition"), _("Prompt to use for the recognition"), "")
            ])
        ]

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
            # Notify UI that download has started
            self.settings_update()
            path = os.path.join(self.whisper_cpp_path, "models/download-ggml-model.sh")

            def run_install():
                try:
                    self.download_model_directly(model_name)
                    self.downloading[model_name]["progress"] = 1.0
                    GLib.idle_add(self.settings_update)
                    # Clean up the downloading entry after completion
                    if model_name in self.downloading:
                        del self.downloading[model_name]
                        GLib.idle_add(self.settings_update)
                except Exception as e:
                    print(f"Error installing model: {e}")
                    self.downloading[model_name]["progress"] = 0.0
                    # Also clean up on error
                    if model_name in self.downloading:
                        del self.downloading[model_name]
                        GLib.idle_add(self.settings_update)

            threading.Thread(target=run_install).start()

    def download_model_directly(self, model_name):
        """Direct download of whisper model as fallback"""
        import urllib.request
        base_url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
        model_filename = f"ggml-{model_name}.bin"
        url = f"{base_url}/{model_filename}"

        def update_progress(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                self.downloading[model_name]["progress"] = min(downloaded / total_size, 0.99)

        target_path = os.path.join(self.model_folder, model_filename)
        os.makedirs(self.model_folder, exist_ok=True)
        urllib.request.urlretrieve(url, target_path, reporthook=update_progress)

    def is_model_installed(self, model_name):
        return os.path.exists(os.path.join(self.model_folder, "ggml-" + model_name + ".bin")) and not self.downloading.get(model_name)

    def get_models(self):
        return tuple((model["display_name"], model["model_name"]) for model in WHISPER_MODELS if self.is_model_installed(model["model_name"]))

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai-whisper"]

    def is_installed(self) -> bool:
        # Check for either GPU build or basic installation
        return self.is_gpu_installed() or os.path.exists(os.path.join(self.path, "whisper", "whisper.cpp"))

    def install(self):
        os.makedirs(os.path.join(self.path, "whisper"), exist_ok=True)
        print("Installing whisper...")
        path = os.path.join(self.path, "whisper")
        installation_script = f"cd {path} && git clone https://github.com/ggerganov/whisper.cpp.git && cd whisper.cpp && sh ./models/download-ggml-model.sh tiny && cmake -B build && cmake --build build -j --config Release"
        out = subprocess.check_output(get_spawn_command() + ["bash", "-c", installation_script])
        exec_path = os.path.join(path, "whisper.cpp/build/bin/whisper-cli")
        if not os.path.exists(exec_path):
            self.throw("Error installing Whisper: " + out.decode("utf-8"), ErrorSeverity.ERROR)

    def _start_process(self):
        """Start the persistent whisper-server process with model loaded"""
        with self._process_lock:
            model_name = self.get_setting("model")

            # If process exists but model changed, restart it
            if self._process is not None:
                if self._current_model != model_name or self._process.poll() is not None:
                    self._stop_process()
                else:
                    return  # Process already running with correct model

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
                if use_system_server:
                    exec_path = "whisper-server"
                    cmd = get_spawn_command() + [exec_path]
                else:
                    exec_path = server_path
                    cmd = [exec_path]

                self._use_server = True
            else:
                exec_path = cli_path
                cmd = [exec_path]
                self._use_server = False

            model_path = os.path.join(self.model_folder, "ggml-" + model_name + ".bin")

            if self._use_server:
                # Start whisper-server on localhost:8080
                cmd.extend([
                    "-m", model_path,
                    "--host", "127.0.0.1",
                    "--port", "8080",
                    "-l", self.get_setting("language"),
                ])
                try:
                    print(f"Starting whisper-server with model: {model_name}")
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1
                    )
                    self._current_model = model_name
                    # Wait for server to start
                    time.sleep(3)
                    # Check if process is still running
                    if self._process.poll() is not None:
                        stdout = self._process.stdout.read() if self._process.stdout else ""
                        stderr = self._process.stderr.read() if self._process.stderr else ""
                        print(f"Server stdout: {stdout}")
                        print(f"Server stderr: {stderr}")
                        raise RuntimeError(f"Server failed to start: {stderr}")
                    print("Server started successfully")
                except Exception as e:
                    self.throw("Error starting Whisper server: " + str(e), ErrorSeverity.ERROR)
                    self._process = None
                    self._use_server = False
            else:
                # For whisper-cli, we can't keep it loaded persistently
                self._process = None
                self._current_model = model_name
                return

    def _stop_process(self):
        """Stop the persistent whisper process"""
        with self._process_lock:
            if self._process is not None:
                try:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait()
                except Exception:
                    pass
                finally:
                    self._process = None
                    self._current_model = None

    def recognize_file(self, path):
        print(f"Recognizing file: {path}")
        self._start_process()

        if self._use_server and self._process is not None and self._process.poll() is None:
            print("Using server mode")
            return self._recognize_with_server(path)
        else:
            print("Using CLI mode")
            return self._recognize_with_cli(path)

    def _recognize_with_server(self, path):
        """Recognize using the whisper-server HTTP API"""
        import urllib.request
        import urllib.error
        import json

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

            url = "http://127.0.0.1:8080/inference"
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
            cmd = [exec_path]
            if is_flatpak():
                cmd = get_spawn_command() + cmd
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
        for model in WHISPER_MODELS:
            models.append(LibraryModel(
                id=model["model_name"],
                name=model["display_name"],
                description=f"Size: {model['size']}",
                tags=["whisper", "stt"],
                is_pinned=False,
                is_installed=self.is_model_installed(model["model_name"]),
            ))
        return models

    def model_installed(self, model: str) -> bool:
        return self.is_model_installed(model)

    def open_model_library(self, button):
        root = button.get_root()
        win = ModelLibraryWindow(self, root)
        win.present()

    # Installation dialog (similar to llama.cpp)
    def show_install_dialog(self, button):
        win = Adw.Window(title="Build whisper.cpp")
        win.set_default_size(600, 600)
        win.set_modal(True)
        try:
            root = button.get_root()
            if root:
                win.set_transient_for(root)
        except:
            pass

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(main_box)

        dots = Adw.CarouselIndicatorDots()
        dots.set_margin_top(12)
        main_box.append(dots)

        content = Adw.Carousel()
        content.set_allow_mouse_drag(False)
        content.set_allow_scroll_wheel(False)
        content.set_hexpand(True)
        content.set_vexpand(True)
        dots.set_carousel(content)
        main_box.append(content)

        # Page 1: Hardware
        page1 = Adw.StatusPage(title="Select Hardware", description="Choose your acceleration backend", icon_name="brain-augemnted-symbolic")
        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        main_container.set_halign(Gtk.Align.CENTER)

        # Show Flatpak warning if needed
        if not can_escape_sandbox():
            warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            warning_box.set_margin_bottom(16)
            warning_box.add_css_class("warning")

            warning_label = Gtk.Label(label="⚠️ Flatpak Sandbox Warning")
            warning_label.add_css_class("heading")
            warning_label.set_halign(Gtk.Align.CENTER)
            warning_box.append(warning_label)

            warning_text = Gtk.Label(label="To build whisper.cpp with hardware acceleration in Flatpak,\nyou need to grant sandbox escape permissions.\nRun the following command in a terminal:")
            warning_text.set_halign(Gtk.Align.CENTER)
            warning_text.set_wrap(True)
            warning_box.append(warning_text)

            command_entry = Gtk.Entry()
            command_entry.set_text("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle")
            command_entry.set_editable(False)
            command_entry.set_halign(Gtk.Align.CENTER)
            command_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            command_box.set_halign(Gtk.Align.CENTER)
            command_box.append(command_entry)
            warning_box.append(command_box)

            copy_btn = Gtk.Button(label="Copy Command")
            copy_btn.set_halign(Gtk.Align.CENTER)
            copy_btn.connect("clicked", lambda btn: self.copy_to_clipboard("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle"))
            warning_box.append(copy_btn)

            main_container.append(warning_box)
            main_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Horizontal box for hardware options and CMake flags
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24, hexpand=True)
        hbox.set_halign(Gtk.Align.CENTER)
        hbox.set_margin_start(24)
        hbox.set_margin_end(24)

        # Left side: Hardware options
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.hw_options = {}
        group = None
        for hw in ["CPU", "CPU (OpenBLAS)", "Nvidia (CUDA)", "AMD (ROCm)", "Any GPU (Vulkan)"]:
            btn = Gtk.CheckButton(label=hw, group=group)
            if group is None:
                group = btn
                btn.set_active(True)
            self.hw_options[hw] = btn
            left_box.append(btn)

        # Right side: CMake flags
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        lbl_flags = Gtk.Label(label="Custom CMake Flags (Optional)")
        lbl_flags.set_halign(Gtk.Align.START)
        right_box.append(lbl_flags)

        self.entry_cmake = Gtk.Entry()
        self.entry_cmake.set_placeholder_text("-DGGML_AVX2=off ...")
        right_box.append(self.entry_cmake)

        hbox.append(left_box)
        hbox.append(right_box)
        main_container.append(hbox)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)

        btn_cancel1 = Gtk.Button(label="Cancel")
        btn_cancel1.add_css_class("destructive-action")
        btn_cancel1.connect("clicked", lambda x: win.close())
        button_box.append(btn_cancel1)

        btn_next1 = Gtk.Button(label="Next")
        if not can_escape_sandbox():
            btn_next1.set_sensitive(False)
            btn_next1.set_tooltip_text("Please run the Flatpak override command first")
        else:
            btn_next1.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        button_box.append(btn_next1)

        main_container.append(button_box)

        page1.set_child(main_container)
        content.append(page1)

        # Page 2: Install Button
        page2 = Adw.StatusPage(title="Ready to Build", description="Click start to begin compilation", icon_name="tools-symbolic")
        box2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box2.set_halign(Gtk.Align.CENTER)

        btn_start = Gtk.Button(label="Start Build")
        btn_start.set_halign(Gtk.Align.CENTER)
        btn_start.connect("clicked", lambda x: self.start_installation(content))
        box2.append(btn_start)

        btn_back2 = Gtk.Button(label="Back")
        btn_back2.set_halign(Gtk.Align.CENTER)
        btn_back2.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(0), True))
        box2.append(btn_back2)

        page2.set_child(box2)
        content.append(page2)

        # Page 3: Progress
        page3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page3.set_hexpand(True)
        page3.set_vexpand(True)
        page3.set_margin_top(24)
        page3.set_margin_bottom(24)
        page3.set_margin_start(24)
        page3.set_margin_end(24)

        icon3 = Gtk.Image.new_from_icon_name("magic-wand-symbolic")
        icon3.set_pixel_size(96)
        icon3.set_halign(Gtk.Align.CENTER)
        page3.append(icon3)

        title3 = Gtk.Label(label="Installing")
        title3.add_css_class("title-1")
        title3.set_halign(Gtk.Align.CENTER)
        page3.append(title3)

        desc3 = Gtk.Label(label="Please wait...")
        desc3.set_halign(Gtk.Align.CENTER)
        page3.append(desc3)

        self.progress_bar = Gtk.ProgressBar()
        page3.append(self.progress_bar)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.log_view)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        page3.append(scroll)
        content.append(page3)

        # Page 4: Done
        page4 = Adw.StatusPage(title="Completed", description="Installation finished successfully", icon_name="emblem-default-symbolic")
        btn_close = Gtk.Button(label="Close")
        btn_close.set_halign(Gtk.Align.CENTER)
        btn_close.connect("clicked", lambda x: self.finish_install(win))
        page4.set_child(btn_close)
        content.append(page4)

        win.present()

    def start_installation(self, carousel):
        backend = "cpu"
        if self.hw_options["Nvidia (CUDA)"].get_active():
            backend = "cuda"
        elif self.hw_options["AMD (ROCm)"].get_active():
            backend = "rocm"
        elif self.hw_options["Any GPU (Vulkan)"].get_active():
            backend = "vulkan"
        elif self.hw_options["CPU (OpenBLAS)"].get_active():
            backend = "cpu_openblas"

        custom_flags = self.entry_cmake.get_text()

        carousel.scroll_to(carousel.get_nth_page(2), True)
        threading.Thread(target=self.run_install_process, args=(backend, carousel, custom_flags)).start()

    def run_install_process(self, backend, carousel, custom_flags=""):
        if not can_escape_sandbox():
            self.throw("You have to escape the sandbox to install WhisperCPP", ErrorSeverity.ERROR)
        try:
            env = os.environ.copy()
            cmake_args = []
            cmake_args.append("-DGGML_NATIVE=ON")
            cmake_args.append("-DGGML_AVX2=ON")
            cmake_args.append("-DGGML_FMA=ON")
            cmake_args.append("-DGGML_AVX512=OFF")
            cmake_args.append("-DBUILD_SHARED_LIBS=OFF")

            # whisper.cpp specific options
            cmake_args.append("-DWHISPER_BUILD_TESTS=OFF")
            cmake_args.append("-DWHISPER_BUILD_EXAMPLES=ON")

            if backend == "cuda":
                cmake_args.append("-DGGML_CUDA=ON")
                cmake_args.append("-DCMAKE_CUDA_ARCHITECTURES=native")
            elif backend == "rocm":
                cmake_args.append("-DGGML_HIPBLAS=ON")
                cmake_args.append("-DAMDGPU_TARGETS=native")
            elif backend == "vulkan":
                cmake_args.append("-DGGML_VULKAN=ON")
            elif backend == "cpu_openblas":
                cmake_args.append("-DGGML_BLAS=ON")
                cmake_args.append("-DGGML_BLAS_VENDOR=OpenBLAS")

            if custom_flags:
                custom_list = custom_flags.split() if isinstance(custom_flags, str) else custom_flags
                cmake_args.extend(custom_list)

            def append_log(text):
                buffer = self.log_view.get_buffer()
                buffer.insert(buffer.get_end_iter(), text)
                return False

            def set_progress(fraction):
                self.progress_bar.set_fraction(fraction)
                return False

            def run_cmd(cmd_list, extra_env=None, cwd=None):
                full_cmd = cmd_list
                if is_flatpak():
                    flatpak_cmd = get_spawn_command()
                    if extra_env:
                        for k, v in extra_env.items():
                             flatpak_cmd.extend([f"--env={k}={v}"])
                    full_cmd = flatpak_cmd + cmd_list
                else:
                    if extra_env:
                        env.update(extra_env)

                process = subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env if not is_flatpak() else None,
                    cwd=cwd
                )

                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        GLib.idle_add(append_log, line)
                return process.poll() == 0

            GLib.idle_add(set_progress, 0.1)
            GLib.idle_add(append_log, "Cloning whisper.cpp repository...\n")

            abs_whisper_cpp_path = os.path.abspath(self.whisper_cpp_path)

            # Remove existing directory if it exists
            if os.path.exists(abs_whisper_cpp_path):
                shutil.rmtree(abs_whisper_cpp_path)

            # Clone whisper.cpp
            clone_cmd = ["git", "clone", "https://github.com/ggerganov/whisper.cpp.git", abs_whisper_cpp_path]
            if not run_cmd(clone_cmd):
                raise Exception("Failed to clone whisper.cpp repository")

            GLib.idle_add(set_progress, 0.2)
            GLib.idle_add(append_log, "Configuring CMake build...\n")

            # Configure CMake
            build_dir = os.path.join(abs_whisper_cpp_path, "build")
            cmake_configure = ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"] + cmake_args
            if not run_cmd(cmake_configure, cwd=abs_whisper_cpp_path):
                raise Exception("Failed to configure CMake build")

            GLib.idle_add(set_progress, 0.4)
            GLib.idle_add(append_log, f"Building whisper.cpp (Backend: {backend})...\n")
            GLib.idle_add(append_log, "This may take several minutes...\n")

            # Build whisper.cpp
            import multiprocessing
            num_jobs = multiprocessing.cpu_count()
            cmake_build = ["cmake", "--build", "build", "--config", "Release", "-j", str(num_jobs)]
            if not run_cmd(cmake_build, cwd=abs_whisper_cpp_path):
                raise Exception("Failed to build whisper.cpp")

            # Verify binaries were built
            server_binary = os.path.join(build_dir, "bin", "whisper-server")
            cli_binary = os.path.join(build_dir, "bin", "whisper-cli")
            if not os.path.exists(server_binary) or not os.path.exists(cli_binary):
                raise Exception("Binaries not found after build")

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Build completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(3), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", True)

        except Exception as e:
            GLib.idle_add(append_log, f"\nError: {e}\n")
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())

    def finish_install(self, win):
        win.close()
        self.settings_update()

    def copy_to_clipboard(self, text):
        """Copy text to system clipboard"""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
