from ...handlers.llm import OpenAIHandler
from ...handlers.extra_settings import ExtraSettings
from ...utility.system import can_escape_sandbox, is_flatpak, get_spawn_command
from ...handlers import ErrorSeverity
import subprocess
import os
import threading
import socket
import time
from gi.repository import Gtk, Adw, GLib
from ...ui.model_library import ModelLibraryWindow, LibraryModel
import requests

class LlamaCPPHandler(OpenAIHandler):
    key = "llamacpp"
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.venv_path = os.path.join(self.path, "venv")
        self.python_path = os.path.join(self.venv_path, "bin", "python3")
        self.pip_path_venv = os.path.join(self.venv_path, "bin", "pip")
        self.model_folder = self.path
        self.server_process = None
        self.port = None
        self.loaded_model = None
        self.models = self.get_custom_model_list()
        self.set_setting("endpoint", "http://localhost")
        self.set_setting("api", "no")
        self.downloading = {}
        if not os.path.exists(self.path):
            try:
                os.makedirs(self.path)
            except:
                pass
    
    def get_custom_model_list(self): 
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
        return file_list
    
    def is_gpu_installed(self) -> bool:
        if not os.path.exists(self.python_path):
            return False
        
        cmd = [self.python_path, "-c", "import llama_cpp"]
        if is_flatpak():
            cmd = get_spawn_command() + cmd
        
        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except:
            return False

    def get_extra_settings(self) -> list:
        settings =  [
                ExtraSettings.ComboSetting("model", "Model", "Model to use", self.get_custom_model_list(), 
                self.get_custom_model_list()[0][1] if len(self.get_custom_model_list()) > 0 else "", 
                refresh=lambda button: self.get_custom_model_list(),
                folder=self.model_folder)
            ]
        if not self.is_gpu_installed():
            settings.append(
                ExtraSettings.ButtonSetting("install", "Install LlamaCPP (Hardware Acceleration)", "Install LlamaCPP Python bindings", self.show_install_dialog, label="Install")
            )
        else:
            settings.extend([
                ExtraSettings.ToggleSetting("gpu_acceleration", "Hardware Acceleration", "Enable hardware acceleration", False),
                ExtraSettings.ButtonSetting("reinstall", "Reinstall", "Reinstall LlamaCPP", self.show_install_dialog, label="Reinstall")
            ])
        settings.extend(
            [
                ExtraSettings.ButtonSetting("library", "Model Library", "Open the model library", self.open_model_library, label="Model Library")
            ]
        )
        return settings

    def fetch_models(self):
        url = "https://raw.githubusercontent.com/FrancescoCaracciolo/llm-library-scraper/refs/heads/main/lmstudio/model_list.json"
        response = requests.get(url)
        data = response.json()
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
        url = "https://raw.githubusercontent.com/FrancescoCaracciolo/llm-library-scraper/refs/heads/main/lmstudio/model_list.json"
        response = requests.get(url)
        data = response.json()
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

    def install_model(self, model: str):
        if self.model_installed(model):
            os.remove(os.path.join(self.model_folder, model + ".gguf"))
            self.settings_update()
        else:
            info = self.get_model_info_by_title(model)
            links = info["links"]
            gguf_link = next((link for link in links if link.endswith("-GGUF")), None)
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
        win = Adw.Window(title="Install LlamaCPP")
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
        box1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box1.set_halign(Gtk.Align.CENTER)
        
        self.hw_options = {}
        group = None
        for hw in ["CPU", "CPU (OpenBLAS)", "Nvidia (CUDA)", "AMD (ROCm)", "AMD (Vulkan)"]:
            btn = Gtk.CheckButton(label=hw, group=group)
            if group is None:
                group = btn
                btn.set_active(True)
            self.hw_options[hw] = btn
            box1.append(btn)
            
        btn_next1 = Gtk.Button(label="Next")
        btn_next1.set_halign(Gtk.Align.CENTER)
        btn_next1.set_margin_top(12)
        btn_next1.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        box1.append(btn_next1)
            
        page1.set_child(box1)
        content.append(page1)

        # Page 2: Install Button
        page2 = Adw.StatusPage(title="Ready to Install", description="Click start to begin compilation", icon_name="tools-symbolic")
        box2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box2.set_halign(Gtk.Align.CENTER)
        
        btn_start = Gtk.Button(label="Start Installation")
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
        elif self.hw_options["AMD (Vulkan)"].get_active():
            backend = "vulkan"
        elif self.hw_options["CPU (OpenBLAS)"].get_active():
            backend = "cpu_openblas"
            
        carousel.scroll_to(carousel.get_nth_page(2), True)
        threading.Thread(target=self.run_install_process, args=(backend, carousel)).start()
        
    def run_install_process(self, backend, carousel):
        if not can_escape_sandbox():
            self.throw("You have to escape the sandbox to install LlamaCPP", ErrorSeverity.ERROR)
        try:
            env = os.environ.copy()
            cmake_args = []
            if backend == "cuda":
                cmake_args.append("-DGGML_CUDA=on")
            elif backend == "rocm":
                cmake_args.append("-DGGML_HIPBLAS=on")
            elif backend == "vulkan":
                cmake_args.append("-DGGML_VULKAN=on")
            elif backend == "cpu_openblas":
                cmake_args.append("-DGGML_BLAS=ON")
                cmake_args.append("-DGGML_BLAS_VENDOR=OpenBLAS")
            env_vars = {}
            if cmake_args:
                env_vars["CMAKE_ARGS"] = " ".join(cmake_args)
                
            def append_log(text):
                buffer = self.log_view.get_buffer()
                buffer.insert(buffer.get_end_iter(), text)
                return False

            def set_progress(fraction):
                self.progress_bar.set_fraction(fraction)
                return False

            def run_cmd(cmd_list, extra_env=None):
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
                    env=env if not is_flatpak() else None
                )
                
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        GLib.idle_add(append_log, line)
                return process.poll() == 0

            GLib.idle_add(set_progress, 0.1)
            GLib.idle_add(append_log, "Creating virtual environment...\n")
            
            abs_venv_path = os.path.abspath(self.venv_path)
            cmd_venv = ["python3", "-m", "venv", abs_venv_path]
            if not run_cmd(cmd_venv):
                raise Exception("Failed to create venv")

            GLib.idle_add(set_progress, 0.3)
            GLib.idle_add(append_log, "Upgrading pip...\n")
            
            pip_bin = os.path.join(abs_venv_path, "bin", "pip")
            if not run_cmd([pip_bin, "install", "--upgrade", "pip"]):
                 raise Exception("Failed to upgrade pip")
                 
            GLib.idle_add(set_progress, 0.5)
            GLib.idle_add(append_log, f"Installing llama-cpp-python (Backend: {backend})...\n")

            install_cmd = [pip_bin, "install", "llama-cpp-python[server]", "--verbose", "--force-reinstall", "--no-cache-dir"]
            if not run_cmd(install_cmd, extra_env=env_vars):
                 raise Exception("Failed to install package")

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(3), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", True)
            
        except Exception as e:
            GLib.idle_add(append_log, f"\nError: {e}")

    def finish_install(self, win):
        win.close()
        self.settings_update()

    def get_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def stream_enabled(self) -> bool:
        return True

    def load_model(self, model):
        if self.loaded_model == model:
            return True
        path = os.path.join(self.model_folder, self.get_setting("model"))
        if not path or not os.path.exists(path):
             return False
        
        if self.server_process:
            self.server_process.terminate()
            self.server_process = None
            
        self.port = self.get_free_port()
        if not self.is_gpu_installed():
            self.python_path = "python"
        cmd = [self.python_path, "-m", "llama_cpp.server", "--model", path, "--port", str(self.port)]
        if is_flatpak() and self.is_gpu_installed():
             cmd = get_spawn_command() + cmd
             
        self.server_process = subprocess.Popen(cmd)
        self.set_setting("endpoint", f"http://localhost:{self.port}/v1")
        self.loaded_model = model
        # Wait for server to potentially start
        time.sleep(3) 
        return True