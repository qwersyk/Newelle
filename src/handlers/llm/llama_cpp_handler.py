from .openai_handler import OpenAIHandler
from ...handlers.extra_settings import ExtraSettings
from ...utility.system import can_escape_sandbox, is_flatpak, get_spawn_command, has_backend, detect_cuda_version
from ...handlers import ErrorSeverity
import subprocess
import os
import platform
import threading
import socket
import time
import json
import shutil
import tarfile
import tempfile
import atexit
from gi.repository import Gtk, Adw, GLib, Gdk
from ...ui.model_library import ModelLibraryWindow, LibraryModel
import requests

class LlamaCPPHandler(OpenAIHandler):
    key = "llamacpp"

    def get_cache_path(self):
        cache_dir = self.path
        return os.path.join(cache_dir, "llamacpp_models.json")

    def update_library_cache(self):
        url = "https://raw.githubusercontent.com/FrancescoCaracciolo/llm-library-scraper/refs/heads/main/lmstudio/model_list.json"
        try:
            response = requests.get(url)
            data = response.json()
            cache_path = self.get_cache_path()
            with open(cache_path, "w") as f:
                json.dump(data, f)
            self.library_data = data
        except Exception as e:
            print(f"Error updating library cache: {e}")
    
    def get_models(self, manual=False):
        self.set_setting("models", json.dumps(["custom"]))
        self.update_library_cache()

    def __init__(self, settings, path):
        super().__init__(settings, path)
        print(detect_cuda_version())
        self.venv_path = os.path.join(self.path, "venv")
        self.llama_cpp_path = os.path.join(self.path, "llama.cpp")
        self.llama_server_path = os.path.join(self.llama_cpp_path, "build", "bin", "llama-server")
        self.model_folder = os.path.join(self.path, "custom_models")
        self.server_process = None
        self._atexit_handler = self.kill_server
        self._killing_server = False
        atexit.register(self._atexit_handler)
        self.port = None
        self.loaded_model = None
        self.loaded_mmproj = None
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
    
    def get_custom_model_list(self, update=False): 
        """Get models in the user folder

        Returns:
            list: list of models 
        """
        file_list = tuple()
        for root, _, files in os.walk(self.model_folder):
            for file in files:
                if file.endswith('.gguf'):
                    file_name = file.rstrip('.gguf')
                    if "mmproj" in file_name:
                        continue
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list += ((file_name, relative_path), )
        if update:
            self.settings_update()
        return file_list
    
    def get_mmproj_list(self, update=False):
        """Get mmproj files in the model folder for vision support

        Returns:
            list: list of mmproj files
        """
        file_list = tuple()
        for root, _, files in os.walk(self.model_folder):
            for file in files:
                if 'mmproj' in file.lower() and file.endswith('.gguf'):
                    file_name = file.rstrip('.gguf')
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list+=((file_name, relative_path),)
        if update:
            self.settings_update()
        return file_list
    
    def is_gpu_installed(self) -> bool:
        # Check if llama.cpp is built (hardware backend installation)
        if os.path.exists(self.llama_server_path) and os.access(self.llama_server_path, os.X_OK):
            return True
        return False

    def get_extra_settings(self) -> list:
        custom_model_list = self.get_custom_model_list()
        mmproj_list = self.get_mmproj_list()
        settings =  [
                ExtraSettings.ComboSetting("model", "Model", "Model to use", self.get_custom_model_list(), 
                custom_model_list[0][1] if len(custom_model_list) > 0 else "", 
                refresh=lambda button: self.get_custom_model_list(True),
                folder=self.model_folder),
                ExtraSettings.ToggleSetting("enable_mmproj", "Enable Vision (MMProj)", "Enable vision support using mmproj file", False, update_settings=True),
            ]
        settings += [ExtraSettings.ComboSetting("mmproj", "MMProj (Vision)", "Multimodal projection file for vision support", 
                mmproj_list,
                mmproj_list[0][1] if len(mmproj_list) > 0 else "",
                refresh=lambda button: self.get_mmproj_list(True),
                folder=self.model_folder)]

        settings.extend(
            [
                ExtraSettings.ButtonSetting("library", "Model Library", "Open the model library", self.open_model_library, label="Model Library")
            ]
        )
        if not self.is_gpu_installed():
            settings.append(
                ExtraSettings.ButtonSetting("install", "Install LlamaCPP (Hardware Acceleration)", "Build llama.cpp with hardware acceleration", self.show_install_dialog, label="Install")
            )
        else:
            settings.extend([
                ExtraSettings.ToggleSetting("gpu_acceleration", "Hardware Acceleration", "Enable hardware acceleration", False),
            ])
            if is_flatpak():
                settings.append(
                    ExtraSettings.ToggleSetting("use_system_server", "Use System llama-server", "Use system-installed llama-server instead of built-in (requires llama-server on host and sandbox escape)", False)
                )
            settings.append(
                ExtraSettings.ButtonSetting("reinstall", "Reinstall", "Rebuild llama.cpp", self.show_install_dialog, label="Reinstall")
            )
        extra_settings = self.build_extra_settings("LlamaCPP", False, True, False, True, False, None, None, False, False, True)
        extra_settings.extend([
            ExtraSettings.SpinSetting("ctx", "Context Size", "Context size to use, 0 = load from model", default=0, min=0, max=1200000, page=1024, step=512),
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
        if self.loaded_model == model and self.loaded_on == self.get_setting("gpu_acceleration", False, False) and self.loaded_ctx == ctx and self.loaded_mmproj == mmproj:
            return True
        path = os.path.join(self.model_folder, self.get_setting("model"))
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
        
        # Add mmproj for vision support if enabled and configured
        if self.get_setting("enable_mmproj") and mmproj:
            mmproj_path = os.path.join(self.model_folder, mmproj)
            if os.path.exists(mmproj_path):
                cmd.extend(["--mmproj", mmproj_path])
        # Use flatpak-spawn for compiled or prebuilt CUDA binaries in Flatpak
        is_prebuilt = self.get_setting("prebuilt", False, False)
        is_cuda_binary = is_prebuilt and self.get_setting("prebuilt_cuda", False, False)
        if (is_flatpak() and self.is_gpu_installed() and self.get_setting("gpu_acceleration", False, False) and (is_cuda_binary or not is_prebuilt)) or use_system_server:
            cmd = get_spawn_command() + cmd

        self.server_process = subprocess.Popen(cmd)
        self._killing_server = False
        threading.Thread(target=self._monitor_server, daemon=True).start()
        self.loaded_model = model
        self.loaded_on = self.get_setting("gpu_acceleration", False, False)
        self.loaded_ctx = ctx
        self.loaded_mmproj = mmproj
        # Wait for server to potentially start
        url = f"http://localhost:{self.port}/v1/models"
        start_time = time.time()
        while time.time() - start_time < 60: # 60 seconds timeout
            try:
                if requests.get(url).status_code == 200:
                    return True
            except:
                pass
            time.sleep(0.5) 
        return False
    
    def kill_server(self):
        self.loaded_model = None
        self._killing_server = True
        if self.server_process:
            try:
                self.server_process.terminate()
                # Wait up to 5 seconds for graceful termination
                try:
                    self.server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate gracefully
                    self.server_process.kill()
                    self.server_process.wait()
            except (ProcessLookupError, ValueError):
                # Process already terminated
                pass
            finally:
                self.server_process = None

    def _monitor_server(self):
        proc = self.server_process
        if proc is None:
            return
        proc.wait()
        if not self._killing_server and self.server_process is None and self.loaded_model is not None:
            self.loaded_model = None
            GLib.idle_add(self.throw, "llama-server crashed unexpectedly. Check terminal output for details.", ErrorSeverity.ERROR)
    
    def destroy(self):
        self.kill_server()
        try:
            atexit.unregister(self._atexit_handler)
        except AttributeError:
            pass

    # Model library
    def fetch_models(self):
        data = self.library_data
        models = []
        for model in data:
            models.append(LibraryModel(
                id=model["title"],
                name=model["title"],
                description=model["description"],
                tags=model["tags"] + model["capabilities"].split("\n"),
                is_pinned=self.model_installed(model["title"]),
                is_installed=self.model_installed(model["title"]),
            ))
        for model_name, model_file in self.models:
            if model_name not in [m.id for m in models]:
                models = [LibraryModel(
                    id=model_name,
                    name=model_name,
                    description=model_file + " - custom added model",
                    tags=["custom"],
                    is_pinned=True,
                    is_installed=True,
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
            from huggingface_hub import hf_hub_download, HfApi
            api = HfApi()
            files = api.list_repo_files(repo_id)
            gguf_file = self.select_best_gguf(files)
            self.downloading[model] = {"status": True, "progress": 0.0}
            def update_progress(progress):
                completed = progress.get("completed", 0)
                total = progress.get("total", 1) # Avoid division by zero
                percentage = (completed / total)
                self.downloading[model]["progress"] = percentage
            class TqdmProgress:
                def __init__(self, *args, **kwargs):
                    self.n = kwargs.get('initial', 0)
                    self.total = kwargs.get('total', 1)
                    update_progress({"completed": self.n, "total": self.total})

                def update(self, n=1):
                    self.n += n
                    update_progress({"completed": self.n, "total": self.total})

                def close(self):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.close()

                def set_description(self, *args, **kwargs):
                    pass

            hf_hub_download(repo_id, gguf_file, local_dir=self.model_folder, local_dir_use_symlinks=False, tqdm_class=TqdmProgress)
            os.rename(os.path.join(self.model_folder, gguf_file), os.path.join(self.model_folder, model + ".gguf"))
            self.settings_update()

    def open_model_library(self, button):
        root = button.get_root()
        win = ModelLibraryWindow(self, root)
        win.present()

    def show_install_dialog(self, button):
        win = Adw.Window(title="Install llama.cpp")
        win.set_default_size(700, 620)
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

        # Page 0: Choose Method
        page0 = Adw.StatusPage(
            title="Choose Installation Method",
            description="How would you like to install llama.cpp?",
            icon_name="system-software-install-symbolic",
        )
        page0_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True, vexpand=True)
        page0_box.set_margin_start(24)
        page0_box.set_margin_end(24)
        page0_box.set_valign(Gtk.Align.CENTER)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20, homogeneous=True)
        cards_box.set_halign(Gtk.Align.CENTER)

        # Left card: Compile
        compile_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_card.add_css_class("card")
        compile_card.set_margin_top(8)
        compile_card.set_margin_bottom(8)
        compile_card.set_margin_start(12)
        compile_card.set_margin_end(12)

        compile_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_inner.set_margin_top(16)
        compile_inner.set_margin_bottom(16)
        compile_inner.set_margin_start(16)
        compile_inner.set_margin_end(16)
        compile_card.append(compile_inner)

        compile_icon = Gtk.Image.new_from_icon_name("tools-symbolic")
        compile_icon.set_pixel_size(48)
        compile_icon.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_icon)

        compile_title = Gtk.Label(label="Compile from Source")
        compile_title.add_css_class("title-4")
        compile_title.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_title)

        for text in [
            "Optimized for your specific CPU",
            "Full customization via CMake flags",
            "Supports all backends",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            compile_inner.append(lbl)

        for text in [
            "Takes 5-20 minutes to build",
            "Requires build tools (cmake, gcc)",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            compile_inner.append(lbl)

        btn_compile = Gtk.Button(label="Compile")
        btn_compile.add_css_class("suggested-action")
        btn_compile.set_halign(Gtk.Align.CENTER)
        btn_compile.set_margin_top(8)
        btn_compile.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        compile_inner.append(btn_compile)
        cards_box.append(compile_card)

        # Right card: Download
        download_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_card.add_css_class("card")
        download_card.set_margin_top(8)
        download_card.set_margin_bottom(8)
        download_card.set_margin_start(12)
        download_card.set_margin_end(12)

        download_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_inner.set_margin_top(16)
        download_inner.set_margin_bottom(16)
        download_inner.set_margin_start(16)
        download_inner.set_margin_end(16)
        download_card.append(download_inner)

        download_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        download_icon.set_pixel_size(48)
        download_icon.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_icon)

        download_title = Gtk.Label(label="Download Pre-built")
        download_title.add_css_class("title-4")
        download_title.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_title)

        for text in [
            "Ready in under a minute",
            "No build tools required",
            "Pre-tested official binaries",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            download_inner.append(lbl)

        for text in [
            "Generic CPU optimizations",
            "Limited to available releases",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            download_inner.append(lbl)

        btn_download = Gtk.Button(label="Download")
        btn_download.add_css_class("suggested-action")
        btn_download.set_halign(Gtk.Align.CENTER)
        btn_download.set_margin_top(8)
        btn_download.connect("clicked", lambda x: self._on_prebuilt_selected(content))
        download_inner.append(btn_download)
        cards_box.append(download_card)

        page0_box.append(cards_box)

        btn_cancel0 = Gtk.Button(label="Cancel")
        btn_cancel0.add_css_class("destructive-action")
        btn_cancel0.set_halign(Gtk.Align.CENTER)
        btn_cancel0.connect("clicked", lambda x: win.close())
        page0_box.append(btn_cancel0)

        page0.set_child(page0_box)
        content.append(page0)

        # Page 1: Hardware Selection (Compile path)
        page1 = Adw.StatusPage(title="Select Hardware", description="Choose your acceleration backend", icon_name="brain-augemnted-symbolic")
        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        main_container.set_halign(Gtk.Align.CENTER)

        if not can_escape_sandbox():
            warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            warning_box.set_margin_bottom(16)
            warning_box.add_css_class("warning")

            warning_label = Gtk.Label(label="Flatpak Sandbox Warning")
            warning_label.add_css_class("heading")
            warning_label.set_halign(Gtk.Align.CENTER)
            warning_box.append(warning_label)

            warning_text = Gtk.Label(label="To build llama.cpp with hardware acceleration in Flatpak,\nyou need to grant sandbox escape permissions.\nRun the following command in a terminal:")
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

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24, hexpand=True)
        hbox.set_halign(Gtk.Align.CENTER)
        hbox.set_margin_start(24)
        hbox.set_margin_end(24)

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
            btn_next1.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(3), True))
        button_box.append(btn_next1)

        main_container.append(button_box)

        page1.set_child(main_container)
        content.append(page1)

        # Page 2: Pre-built Binary Selection (Download path)
        page2 = Adw.StatusPage(
            title="Select Pre-built Binary",
            description="Choose the binary that matches your hardware",
            icon_name="folder-download-symbolic",
        )
        self.prebuilt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        self.prebuilt_box.set_margin_start(24)
        self.prebuilt_box.set_margin_end(24)
        self.prebuilt_box.set_halign(Gtk.Align.CENTER)

        self.prebuilt_spinner = Gtk.Spinner()
        self.prebuilt_spinner.set_halign(Gtk.Align.CENTER)
        self.prebuilt_spinner.start()
        self.prebuilt_box.append(self.prebuilt_spinner)

        self.prebuilt_list_box = Gtk.ListBox()
        self.prebuilt_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.prebuilt_list_box.add_css_class("boxed-list")
        self.prebuilt_list_box.set_hexpand(True)
        self.prebuilt_box.append(self.prebuilt_list_box)

        self.prebuilt_error_label = Gtk.Label(label="")
        self.prebuilt_error_label.set_halign(Gtk.Align.CENTER)
        self.prebuilt_error_label.set_wrap(True)
        self.prebuilt_box.append(self.prebuilt_error_label)

        prebuilt_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        prebuilt_buttons.set_halign(Gtk.Align.CENTER)
        prebuilt_buttons.set_margin_top(12)

        btn_back_prebuilt = Gtk.Button(label="Back")
        btn_back_prebuilt.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(0), True))
        prebuilt_buttons.append(btn_back_prebuilt)

        self.btn_start_prebuilt = Gtk.Button(label="Download & Install")
        self.btn_start_prebuilt.add_css_class("suggested-action")
        self.btn_start_prebuilt.set_sensitive(False)
        self.btn_start_prebuilt.connect("clicked", lambda x: self._start_prebuilt_install(content))
        prebuilt_buttons.append(self.btn_start_prebuilt)

        self.prebuilt_box.append(prebuilt_buttons)
        page2.set_child(self.prebuilt_box)
        content.append(page2)

        # Page 3: Ready to Build (Compile confirm)
        page3 = Adw.StatusPage(title="Ready to Build", description="Click start to begin compilation", icon_name="tools-symbolic")
        box3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box3.set_halign(Gtk.Align.CENTER)

        btn_start = Gtk.Button(label="Start Build")
        btn_start.set_halign(Gtk.Align.CENTER)
        btn_start.connect("clicked", lambda x: self.start_installation(content))
        box3.append(btn_start)

        btn_back3 = Gtk.Button(label="Back")
        btn_back3.set_halign(Gtk.Align.CENTER)
        btn_back3.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        box3.append(btn_back3)

        page3.set_child(box3)
        content.append(page3)

        # Page 4: Progress
        page4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page4.set_hexpand(True)
        page4.set_vexpand(True)
        page4.set_margin_top(24)
        page4.set_margin_bottom(24)
        page4.set_margin_start(24)
        page4.set_margin_end(24)

        icon4 = Gtk.Image.new_from_icon_name("magic-wand-symbolic")
        icon4.set_pixel_size(96)
        icon4.set_halign(Gtk.Align.CENTER)
        page4.append(icon4)

        title4 = Gtk.Label(label="Installing")
        title4.add_css_class("title-1")
        title4.set_halign(Gtk.Align.CENTER)
        page4.append(title4)

        desc4 = Gtk.Label(label="Please wait...")
        desc4.set_halign(Gtk.Align.CENTER)
        page4.append(desc4)

        self.progress_bar = Gtk.ProgressBar()
        page4.append(self.progress_bar)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.log_view)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        page4.append(scroll)
        content.append(page4)

        # Page 5: Done
        page5 = Adw.StatusPage(title="Completed", description="Installation finished successfully", icon_name="emblem-default-symbolic")
        btn_close = Gtk.Button(label="Close")
        btn_close.set_halign(Gtk.Align.CENTER)
        btn_close.connect("clicked", lambda x: self.finish_install(win))
        page5.set_child(btn_close)
        content.append(page5)

        win.present()

    def _on_prebuilt_selected(self, content):
        content.scroll_to(content.get_nth_page(2), True)
        if not hasattr(self, '_prebuilt_fetched') or not self._prebuilt_fetched:
            self._prebuilt_fetched = True
            threading.Thread(target=self._fetch_prebuilt_releases, args=(content,), daemon=True).start()

    @staticmethod
    def _detect_arch():
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            return "x64"
        elif machine in ("aarch64", "arm64"):
            return "arm64"
        return machine

    @staticmethod
    def _parse_asset_backend(name):
        import re
        name_lower = name.lower()
        if "rocm" in name_lower:
            return "rocm"
        elif "cuda" in name_lower:
            return "cuda"
        elif "vulkan" in name_lower:
            return "vulkan"
        elif "openvino" in name_lower:
            return "openvino"
        return "cpu"

    @staticmethod
    def _parse_cuda_version(name):
        import re
        match = re.search(r"cuda[-_.]?(\d+)\.(\d+)", name.lower())
        if match:
            return float(f"{match.group(1)}.{match.group(2)}")
        return None

    @staticmethod
    def _human_size(size_bytes):
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    @staticmethod
    def _backend_display_name(backend, cuda_version=None):
        name = {
            "cpu": "CPU (Basic)",
            "cuda": "Nvidia CUDA",
            "rocm": "AMD ROCm",
            "vulkan": "Any GPU (Vulkan)",
            "openvino": "Intel OpenVINO",
        }.get(backend, backend)
        if backend == "cuda" and cuda_version is not None:
            name += f" {cuda_version:.1f}"
        return name

    def _fetch_prebuilt_releases(self, carousel):
        arch = self._detect_arch()
        available = []

        def _add_cuda_repo(api_url, source_name):
            try:
                resp = requests.get(api_url, timeout=15)
                resp.raise_for_status()
                release = resp.json()
                tag = release.get("tag_name", "unknown")
                for asset in release.get("assets", []):
                    name = asset["name"]
                    url = asset["browser_download_url"]
                    size = asset.get("size", 0)
                    if not name.endswith(".tar.gz"):
                        continue
                    if "cuda" not in name.lower():
                        continue
                    backend = self._parse_asset_backend(name)
                    cuda_ver = self._parse_cuda_version(name)
                    available.append({
                        "name": name,
                        "url": url,
                        "size": size,
                        "backend": backend,
                        "cuda_version": cuda_ver,
                        "tag": tag,
                        "source": source_name,
                    })
            except Exception:
                pass

        try:
            resp = requests.get(
                "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
                timeout=15,
            )
            resp.raise_for_status()
            release = resp.json()
            tag = release.get("tag_name", "unknown")

            for asset in release.get("assets", []):
                name = asset["name"]
                url = asset["browser_download_url"]
                size = asset.get("size", 0)

                if not name.endswith(".tar.gz"):
                    continue
                if "-bin-ubuntu-" not in name:
                    continue

                if arch == "x64" and "-x64" not in name and "-arm64" not in name:
                    if name.count("-") < 5:
                        continue
                if arch == "x64" and "-arm64" in name:
                    continue
                if arch == "arm64" and "-x64" in name:
                    continue
                if arch == "arm64" and "-arm64" not in name:
                    continue

                backend = self._parse_asset_backend(name)
                available.append({
                    "name": name,
                    "url": url,
                    "size": size,
                    "backend": backend,
                    "cuda_version": None,
                    "tag": tag,
                    "source": "official",
                })

            _add_cuda_repo(
                "https://api.github.com/repos/ai-dock/llama.cpp-cuda/releases/latest",
                "ai-dock",
            )
            _add_cuda_repo(
                "https://api.github.com/repos/Syrunekai/llama.cpp-cuda/releases/latest",
                "syrunekai-cuda13",
            )
            _add_cuda_repo(
                "https://api.github.com/repos/dramaturg/llama.cpp-cuda/releases/latest",
                "dramaturg-cuda11",
            )

        except Exception as e:
            GLib.idle_add(self._show_prebuilt_error, f"Failed to fetch releases: {e}")
            return

        if not available:
            GLib.idle_add(self._show_prebuilt_error, f"No compatible pre-built binaries found for your architecture ({arch}).")
            return

        backend_checks = {}
        for b in ("cuda", "rocm", "vulkan", "openvino"):
            backend_checks[b] = has_backend(b)

        system_cuda = detect_cuda_version()

        for item in available:
            item["compatible"] = item["backend"] == "cpu" or backend_checks.get(item["backend"], False)
            item["cuda_match"] = (
                system_cuda is not None
                and item.get("cuda_version") is not None
                and item["cuda_version"] <= system_cuda
            )

        def _sort_key(x):
            is_compatible = 0 if x["compatible"] else 1
            if x["compatible"] and x["backend"] == "cuda" and x.get("cuda_match"):
                cuda_priority = 0 if x.get("cuda_version") is not None else 1
            elif x["compatible"] and x["backend"] != "cpu":
                cuda_priority = 2
            else:
                cuda_priority = 3
            backend_prio = {"cuda": 0, "rocm": 1, "vulkan": 2, "openvino": 3, "cpu": 4}.get(x["backend"], 99)
            ver = x.get("cuda_version") or 0
            return (is_compatible, cuda_priority, backend_prio, -ver)

        available.sort(key=_sort_key)

        GLib.idle_add(self._populate_prebuilt_list, available)

    def _show_prebuilt_error(self, message):
        if hasattr(self, 'prebuilt_spinner') and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)
        self.prebuilt_error_label.set_text(message)

    def _populate_prebuilt_list(self, available):
        if hasattr(self, 'prebuilt_spinner') and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)

        child = self.prebuilt_list_box.get_first_child()
        while child:
            self.prebuilt_list_box.remove(child)
            child = self.prebuilt_list_box.get_first_child()

        self.prebuilt_assets = available
        group = None
        first_recommended = None
        first_overall = None

        for i, item in enumerate(available):
            row = Adw.ActionRow()

            cuda_ver = item.get("cuda_version")
            row.set_title(self._backend_display_name(item["backend"], cuda_ver))

            if item["compatible"] and item["backend"] != "cpu":
                if item.get("cuda_match"):
                    label_text = "Best Match"
                else:
                    label_text = "Recommended"
                rec = Gtk.Label(label=label_text)
                rec.add_css_class("success")
                rec.add_css_class("caption")
                rec.set_valign(Gtk.Align.CENTER)
                row.add_suffix(rec)

            subtitle_parts = []
            source = item.get("source", "official")
            if source != "official":
                source_labels = {
                    "ai-dock": "CUDA 12 (ai-dock)",
                    "syrunekai-cuda13": "CUDA 13 (Syrunekai)",
                    "dramaturg-cuda11": "CUDA 11 (dramaturg)",
                }
                subtitle_parts.append(source_labels.get(source, source))
            subtitle_parts.append(self._human_size(item["size"]))
            subtitle_parts.append(item["tag"])
            row.set_subtitle("  |  ".join(subtitle_parts))

            check = Gtk.CheckButton()
            if group is None:
                group = check
                check.set_active(True)
            else:
                check.set_group(group)
            row.add_prefix(check)
            row.set_activatable_widget(check)

            if item["compatible"] and first_recommended is None:
                first_recommended = i
                check.set_active(True)
            if first_overall is None:
                first_overall = i

            self.prebuilt_list_box.append(row)

        self._selected_prebuilt = first_recommended if first_recommended is not None else first_overall
        self.btn_start_prebuilt.set_sensitive(True)

        def on_row_activated(listbox, row):
            idx = 0
            child = listbox.get_first_child()
            while child:
                if child == row:
                    break
                child = child.get_next_sibling()
                idx += 1
            self._selected_prebuilt = idx

        self.prebuilt_list_box.connect("row-activated", on_row_activated)

    def _start_prebuilt_install(self, carousel):
        if not hasattr(self, '_selected_prebuilt') or self._selected_prebuilt is None:
            return
        if not hasattr(self, 'prebuilt_assets') or self._selected_prebuilt >= len(self.prebuilt_assets):
            return

        asset = self.prebuilt_assets[self._selected_prebuilt]
        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self._run_prebuilt_install, args=(asset, carousel), daemon=True).start()

    def _run_prebuilt_install(self, asset, carousel):
        def append_log(text):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text)
            return False

        def set_progress(fraction):
            self.progress_bar.set_fraction(fraction)
            return False

        try:
            GLib.idle_add(append_log, f"Downloading {asset['name']}...\n")
            GLib.idle_add(set_progress, 0.0)

            resp = requests.get(asset["url"], stream=True, timeout=120)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", asset["size"]))
            downloaded = 0

            tmp_dir = tempfile.mkdtemp()
            tmp_file = os.path.join(tmp_dir, asset["name"])

            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress = (downloaded / total) * 0.7
                        GLib.idle_add(set_progress, progress)

            GLib.idle_add(set_progress, 0.7)
            GLib.idle_add(append_log, "Download complete. Extracting...\n")

            abs_llama_cpp_path = os.path.abspath(self.llama_cpp_path)
            if os.path.exists(abs_llama_cpp_path):
                shutil.rmtree(abs_llama_cpp_path)

            with tarfile.open(tmp_file, "r:gz") as tar:
                tar.extractall(tmp_dir)

            extracted_dirs = [d for d in os.listdir(tmp_dir)
                             if os.path.isdir(os.path.join(tmp_dir, d)) and d.startswith("llama-")]
            if not extracted_dirs:
                extracted_dirs = [d for d in os.listdir(tmp_dir)
                                 if os.path.isdir(os.path.join(tmp_dir, d))]

            if not extracted_dirs:
                raise Exception("Could not find extracted directory in archive")

            extracted_path = os.path.join(tmp_dir, extracted_dirs[0])

            build_bin = os.path.join(abs_llama_cpp_path, "build", "bin")
            os.makedirs(build_bin, exist_ok=True)

            for item in os.listdir(extracted_path):
                src = os.path.join(extracted_path, item)
                dst = os.path.join(build_bin, item)
                shutil.move(src, dst)

            target = os.path.join(build_bin, "llama-server")
            if not os.path.exists(target):
                raise Exception("llama-server binary not found in the archive")

            os.chmod(target, 0o755)

            so_dir = build_bin
            self.set_setting("prebuilt_so_path", so_dir)

            shutil.rmtree(tmp_dir, ignore_errors=True)

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Installation completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("prebuilt", True)
            self.set_setting("prebuilt_cuda", asset.get("backend") == "cuda")
            self.set_setting("gpu_acceleration", True)

        except Exception as e:
            GLib.idle_add(append_log, f"\nError: {e}\n")
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())

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

        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self.run_install_process, args=(backend, carousel, custom_flags)).start()

    def run_install_process(self, backend, carousel, custom_flags=""):
        if not can_escape_sandbox():
            self.throw("You have to escape the sandbox to install LlamaCPP", ErrorSeverity.ERROR)
        try:
            env = os.environ.copy()
            cmake_args = []
            cmake_args.append("-DGGML_NATIVE=ON")
            cmake_args.append("-DGGML_AVX2=ON")
            cmake_args.append("-DGGML_FMA=ON")
            cmake_args.append("-DGGML_AVX512=OFF")
            if backend == "cuda":
                cmake_args.append("-DGGML_CUDA=ON")
                cmake_args.append("-DCMAKE_CUDA_ARCHITECTURES=native")
                cmake_args.append("-DGGML_CUDA_F16=ON")
                cmake_args.append("-DGGML_CUDA_GRAPHS=ON")

            elif backend == "rocm":
                cmake_args.append("-DGGML_HIPBLAS=ON")
                cmake_args.append("-DAMDGPU_TARGETS=native")
            elif backend == "vulkan":
                cmake_args.append("-DGGML_VULKAN=ON")
                cmake_args.append("-DGGML_VULKAN_F16=ON")
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
            GLib.idle_add(append_log, "Cloning llama.cpp repository...\n")

            abs_llama_cpp_path = os.path.abspath(self.llama_cpp_path)

            if os.path.exists(abs_llama_cpp_path):
                run_cmd(["rm", "-rf", abs_llama_cpp_path])

            clone_cmd = ["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", abs_llama_cpp_path]
            if not run_cmd(clone_cmd):
                raise Exception("Failed to clone llama.cpp repository")

            GLib.idle_add(set_progress, 0.2)
            GLib.idle_add(append_log, "Configuring CMake build...\n")

            build_dir = os.path.join(abs_llama_cpp_path, "build")
            cmake_configure = ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"] + cmake_args
            if not run_cmd(cmake_configure, cwd=abs_llama_cpp_path):
                raise Exception("Failed to configure CMake build")

            GLib.idle_add(set_progress, 0.4)
            GLib.idle_add(append_log, f"Building llama.cpp (Backend: {backend})...\n")
            GLib.idle_add(append_log, "This may take several minutes...\n")

            import multiprocessing
            num_jobs = multiprocessing.cpu_count()
            cmake_build = ["cmake", "--build", "build", "--config", "Release", "-j", str(num_jobs)]
            if not run_cmd(cmake_build, cwd=abs_llama_cpp_path):
                raise Exception("Failed to build llama.cpp")

            server_binary = os.path.join(build_dir, "bin", "llama-server")
            if not os.path.exists(server_binary):
                raise Exception("Server binary not found after build")

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Build completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("prebuilt", False)
            self.set_setting("gpu_acceleration", True)

        except Exception as e:
            GLib.idle_add(append_log, f"\nError: {e}\n")
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())

    def finish_install(self, win):
        win.close()
        self.settings_update()

    def copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    # Override get_setting and set_setting to avoid reloading the model
    def get_setting(self, key: str, search_default = True, return_value = None):
        if key == "endpoint":
            return f"http://localhost:{self.port}/v1"
        return super().get_setting(key, search_default, return_value)

    def set_setting(self, key: str, value):
        return super().set_setting(key, value)
