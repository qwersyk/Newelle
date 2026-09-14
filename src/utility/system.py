import subprocess
import os
import re

def is_wayland() -> bool:
    """
    Check if we are in a Wayland environment

    Returns:
        bool: True if we are in a Wayland environment
    """
    if os.getenv("WAYLAND_DISPLAY"):
        return True
    return False

def is_flatpak() -> bool:
    """
    Check if we are in a flatpak

    Returns:
        bool: True if we are in a flatpak
    """
    if os.getenv("container"):
        return True
    return False


FLATPAK_X11_OVERRIDE_COMMAND = (
    "flatpak override --user --socket=x11 io.github.qwersyk.Newelle"
)
_FLATPAK_INFO_PATH = "/.flatpak-info"


def get_flatpak_x11_override_command() -> str:
    """Return the user-level Flatpak override that grants X11 access."""
    return FLATPAK_X11_OVERRIDE_COMMAND


def _flatpak_sockets():
    if not os.path.exists(_FLATPAK_INFO_PATH):
        return []
    try:
        with open(_FLATPAK_INFO_PATH, encoding="utf-8") as info:
            for line in info:
                if line.startswith("sockets="):
                    return [
                        socket.strip()
                        for socket in line.split("=", 1)[1].split(";")
                        if socket.strip()
                    ]
    except OSError:
        return []
    return []


def has_flatpak_x11_permission() -> bool:
    """Return whether the sandbox was granted a real X11 socket.

    ``fallback-x11`` does not count: Flatpak only exposes that socket when
    Wayland is unavailable.  Flathub keeps that fallback in the manifest, so
    Wayland users need a user override for Voice Mode positioning.
    """
    if not is_flatpak():
        return True
    return "x11" in _flatpak_sockets()


def voice_mode_layer_shell_available() -> bool:
    """Return whether GTK Layer Shell can position the Voice Mode pill."""
    try:
        import gi

        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell
    except (ImportError, ValueError):
        return False
    try:
        return bool(Gtk4LayerShell.is_supported())
    except Exception:
        return False


def needs_voice_mode_x11_override() -> bool:
    """True when Voice Mode needs a Flatpak X11 override to position itself."""
    if not is_flatpak() or has_flatpak_x11_permission():
        return False
    if voice_mode_layer_shell_available():
        return False
    return True

def can_escape_sandbox() -> bool:
    """
    Check if we can escape the sandbox 

    Returns:
        bool: True if we can escape the sandbox
    """
    if not is_flatpak():
        return True
    try:
        r = subprocess.check_output(["flatpak-spawn", "--host", "echo", "test"])
    except subprocess.CalledProcessError as _:
        return False
    return True

def get_spawn_command() -> list:
    """
    Get the spawn command to run commands on the user system

    Returns:
        list: space diveded command  
    """
    if is_flatpak():
        return ["flatpak-spawn", "--host"]
    else:
        return []

def open_website(website):
    """Opens a website using xdg-open

    Args:
        website (): url of the website 
    """
    subprocess.Popen(["xdg-open", website])

def open_folder(folder):
    """Opens a website using xdg-open

    Args:
        folder (): location of the folder 
    """
    subprocess.Popen(["xdg-open", folder])


def has_backend(backend: str, spawn: bool = True) -> bool:
    """Check if a GPU/compute backend is available on the system.

    Args:
        backend: One of "cuda", "rocm", "vulkan", "openvino", "sycl"
        spawn: If True, use get_spawn_command() prefix for subprocess calls

    Returns:
        bool: True if the backend appears to be available
    """
    cmd_prefix = get_spawn_command() if spawn else []

    def _run_check(cmd):
        try:
            result = subprocess.run(
                cmd_prefix + cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _path_check(path):
        return os.path.exists(path)

    if backend == "cuda":
        if _run_check(["nvidia-smi"]):
            return True
        return _path_check("/proc/driver/nvidia/version")

    elif backend == "rocm":
        if _run_check(["rocminfo"]):
            return True
        return _path_check("/opt/rocm")

    elif backend == "vulkan":
        if _run_check(["vulkaninfo"]):
            return True
        icd_dir = "/usr/share/vulkan/icd.d"
        if os.path.isdir(icd_dir):
            return any(f.endswith(".json") for f in os.listdir(icd_dir))
        return False

    elif backend == "openvino":
        # OpenVINO runtime ships a benchmark tool, and the Python package is
        # commonly installed alongside it. Accept either signal.
        if _run_check(["benchmark_app", "-h"]):
            return True
        return _run_check(["python3", "-c", "import openvino"])

    elif backend == "sycl":
        # oneAPI / DPC++ SYCL toolchain. The sycl-ls utility lists SYCL devices
        # and is shipped with the Intel oneAPI compiler; ocloc targets Intel GPUs.
        if _run_check(["sycl-ls"]):
            return True
        return _path_check("/opt/intel/oneAPI")

    return False


def detect_cuda_version() -> float | None:
    """Detect the installed CUDA runtime version.

    Tries nvcc first, then falls back to nvidia-smi output.

    Returns:
        The major.minor CUDA version as a float (e.g. 12.8, 13.2, 11.7),
        or None if CUDA is not found.
    """
    cmd_prefix = get_spawn_command()

    try:
        result = subprocess.run(
            cmd_prefix + ["nvcc", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"release\s+(\d+)\.(\d+)", result.stdout)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            cmd_prefix + ["nvidia-smi"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"CUDA Version:\s+(\d+)\.(\d+)", result.stdout)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None
