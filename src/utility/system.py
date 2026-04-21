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
    subprocess.Popen(get_spawn_command() + ["xdg-open", website])

def open_folder(folder):
    """Opens a website using xdg-open

    Args:
        folder (): location of the folder 
    """
    subprocess.Popen(get_spawn_command() + ["xdg-open", folder])


def has_backend(backend: str, spawn: bool = True) -> bool:
    """Check if a GPU/compute backend is available on the system.

    Args:
        backend: One of "cuda", "rocm", "vulkan", "openvino"
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
        return _run_check(["python3", "-c", "import openvino"])

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
