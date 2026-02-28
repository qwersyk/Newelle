from ...handlers.embeddings import EmbeddingHandler
from ...handlers import ExtraSettings
from ...handlers import ErrorSeverity
from ...utility.system import can_escape_sandbox, is_flatpak, get_spawn_command
from ...ui.model_library import ModelLibraryWindow, LibraryModel
import subprocess
import os
import threading
import socket
import time
import json
import shutil
import atexit
from gi.repository import Gtk, Adw, GLib, Gdk
import requests
import numpy as np

class LlamaCPPEmbeddingHandler(EmbeddingHandler):
    key = "llamacppembedding"

    def get_cache_path(self):
        cache_dir = self.path
        return os.path.join(cache_dir, "llamacpp_embedding_models.json")

    # Todo add a model library
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
        self.venv_path = os.path.join(self.path, "venv")
        self.llama_cpp_path = os.path.join(self.path, "llama.cpp")
        self.llama_server_path = os.path.join(self.llama_cpp_path, "build", "bin", "llama-server")
        self.model_folder = os.path.join(self.path, "custom_models")
        self.server_process = None
        self._atexit_handler = self.kill_server
        atexit.register(self._atexit_handler)
        self.port = None
        self.loaded_model = None
        self.models = self.get_custom_model_list()
        self.loaded_on = self.get_setting("gpu_acceleration", False, False)
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
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list += ((file_name, relative_path), )
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
        settings =  [
                ExtraSettings.ComboSetting("model", "Model", "Model to use", self.get_custom_model_list(), 
                custom_model_list[0][1] if len(custom_model_list) > 0 else "", 
                refresh=lambda button: self.get_custom_model_list(True),
                folder=self.model_folder)
            ]

        #settings.extend(
        #    [
        #        ExtraSettings.ButtonSetting("library", "Model Library", "Open the model library", self.open_model_library, label="Model Library")
        #    ]
        #)
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
        return settings

    # Model Loading
    def get_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def load_model(self, model=None):
        if model is None:
            model = self.get_setting("model")
        if self.loaded_model == model and self.loaded_on == self.get_setting("gpu_acceleration", False, False):
            return True
        path = os.path.join(self.model_folder, model)
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
        cmd = [cmd_path, "--model", path, "--port", str(self.port), "--host", "127.0.0.1", "--embeddings" ]
        # Use flatpak-spawn when running built llama.cpp in Flatpak
        if (is_flatpak() and self.is_gpu_installed() and self.get_setting("gpu_acceleration", False, False)) or use_system_server:
            cmd = get_spawn_command() + cmd
        self.server_process = subprocess.Popen(cmd)
        self.loaded_model = model
        self.loaded_on = self.get_setting("gpu_acceleration", False, False)
        # Wait for server to potentially start
        url = f"http://localhost:{self.port}/v1/models"
        start_time = time.time()
        while time.time() - start_time < 60: # 60 seconds timeout
            try:
                if requests.get(url).status_code == 200:
                    # Verify embeddings work with a test request
                    try:
                        test_url = f"http://localhost:{self.port}/v1/embeddings"
                        test_response = requests.post(
                            test_url,
                            headers={"Content-Type": "application/json"},
                            json={"input": "test", "model": model},
                            timeout=30
                        )
                        if test_response.status_code == 200:
                            return True
                        else:
                            print(f"Model loaded but embeddings test failed: {test_response.status_code} - {test_response.text[:200]}")
                            self.kill_server()
                            raise Exception(f"Model {model} does not support embeddings. Please use a model that supports embeddings (e.g., nomic-embed-text, bge models, or other embedding-specific models).")
                    except requests.exceptions.RequestException as e:
                        print(f"Could not verify embedding support: {e}")
                        # Continue anyway, will fail later if embeddings don't work
                        return True
            except:
                pass
            time.sleep(0.5) 
        return False
    
    def kill_server(self):
        self.loaded_model = None
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

        # Llama CPP install dialog

    def show_install_dialog(self, button):
        win = Adw.Window(title="Build llama.cpp")
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

        # Show Flatpak warning if needed - at the top of page 1
        if not can_escape_sandbox():
            warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            warning_box.set_margin_bottom(16)
            warning_box.add_css_class("warning")

            warning_label = Gtk.Label(label="⚠️ Flatpak Sandbox Warning")
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
        # Block Next button if Flatpak permissions are not set
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
                # Parse custom flags - they might be space-separated or already a list
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
            
            # Remove existing directory if it exists
            if os.path.exists(abs_llama_cpp_path):
                shutil.rmtree(abs_llama_cpp_path)
            
            # Clone llama.cpp
            clone_cmd = ["git", "clone", "https://github.com/ggml-org/llama.cpp.git", abs_llama_cpp_path]
            if not run_cmd(clone_cmd):
                raise Exception("Failed to clone llama.cpp repository")

            GLib.idle_add(set_progress, 0.2)
            GLib.idle_add(append_log, "Configuring CMake build...\n")
            
            # Configure CMake
            build_dir = os.path.join(abs_llama_cpp_path, "build")
            cmake_configure = ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"] + cmake_args
            if not run_cmd(cmake_configure, cwd=abs_llama_cpp_path):
                raise Exception("Failed to configure CMake build")
                 
            GLib.idle_add(set_progress, 0.4)
            GLib.idle_add(append_log, f"Building llama.cpp (Backend: {backend})...\n")
            GLib.idle_add(append_log, "This may take several minutes...\n")
            
            # Build llama.cpp
            import multiprocessing
            num_jobs = multiprocessing.cpu_count()
            cmake_build = ["cmake", "--build", "build", "--config", "Release", "-j", str(num_jobs)]
            if not run_cmd(cmake_build, cwd=abs_llama_cpp_path):
                raise Exception("Failed to build llama.cpp")

            # Verify server binary was built
            server_binary = os.path.join(build_dir, "bin", "llama-server")
            if not os.path.exists(server_binary):
                raise Exception("Server binary not found after build")

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

    # Embedding methods
    def truncate_text(self, text: str, max_tokens: int = 256) -> str:
        """Truncate text to approximately max_tokens to avoid context length issues
        
        The llama-server has a default batch size of 512 tokens, so we use 256
        as a safe limit to account for tokenization variations.
        """
        # Conservative estimate: 1 token ≈ 3 characters on average
        # Using 256 tokens max to stay well under the 512 batch size limit
        max_chars = max_tokens * 3
        if len(text) > max_chars:
            truncated = text[:max_chars]
            # Try to cut at a word boundary
            last_space = truncated.rfind(' ')
            if last_space > max_chars * 0.8:  # Only cut at word if we're not losing too much
                truncated = truncated[:last_space]
            return truncated
        return text

    def get_embedding(self, text: list[str]) -> np.ndarray:
        """Get embeddings for a list of texts using llama-server's embedding endpoint"""
        # Ensure model is loaded and server is running
        if self.loaded_model is None or self.server_process is None:
            if not self.load_model():
                raise Exception("Failed to load model")
        
        # Call the embeddings endpoint
        url = f"http://localhost:{self.port}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        
        # Handle both single text and list of texts
        if isinstance(text, str):
            text = [text]
        
        # Truncate texts to avoid context length issues (500 error)
        truncated_texts = [self.truncate_text(t) for t in text]
        
        # Try batch request first (more efficient)
        try:
            payload = {
                "input": truncated_texts,
                "model": self.loaded_model
            }
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                
                if "data" in data and len(data["data"]) > 0:
                    # Sort by index to maintain order
                    sorted_data = sorted(data["data"], key=lambda x: x.get("index", 0))
                    embeddings = [item["embedding"] for item in sorted_data]
                    return np.array(embeddings)
            else:
                print(f"Batch embedding failed with status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"Batch embedding failed, falling back to individual requests: {e}")
        
        # Fallback: Process individually with retry logic and pacing
        embeddings = []
        for i, t in enumerate(truncated_texts):
            payload = {
                "input": t,
                "model": self.loaded_model
            }
            
            # Retry logic for individual requests
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    response = requests.post(url, headers=headers, json=payload)
                    if response.status_code != 200:
                        error_text = response.text[:500] if response.text else "No error details"
                        print(f"Embedding request failed with status {response.status_code}: {error_text}")
                        raise Exception(f"HTTP {response.status_code}: {error_text}")
                    
                    data = response.json()
                    
                    if "data" in data and len(data["data"]) > 0:
                        embeddings.append(data["data"][0]["embedding"])
                        break
                    else:
                        raise Exception(f"Invalid response format: {data}")
                except Exception as e:
                    print(f"Error getting embedding for text {i+1}/{len(text)} (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(0.5 * (attempt + 1))  # Exponential backoff: 0.5s, 1s, 1.5s, 2s, 2.5s
                    else:
                        raise
            
            # Small delay between requests to avoid overwhelming the server
            if i < len(text) - 1:
                time.sleep(0.1)
        
        return np.array(embeddings)
    
    def get_embedding_size(self) -> int:
        """Get the embedding dimension for the loaded model"""
        if self.dim is None:
            # Get embedding size by making a test request
            test_embedding = self.get_embedding(["test"])
            self.dim = test_embedding.shape[1]
        return self.dim
