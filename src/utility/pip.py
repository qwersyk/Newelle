import importlib
import subprocess
import sys
import os 
import threading

LOCK_SEMAPHORE = threading.Semaphore(1)
LOCKS = {}
PIP_INSTALLED = False

def is_module_available(module_name: str) -> bool:
    """
    Checks if a module can be found by the import system without importing it.

    This is generally faster and safer than trying to import the module directly,
    as it avoids executing the module's initialization code and potential side effects.

    Args:
        module_name: The full name of the module (e.g., 'os', 'requests.api').

    Returns:
        True if the module specification can be found, False otherwise.
            Returns True immediately if the module is already imported.
    """
    if module_name in sys.modules:
        return True
    try:
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except ModuleNotFoundError:
        return False
    except ImportError:
        return False

def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    return is_module_available(full_module_name) if is_module_available(full_module_name) else None

def runtime_find_module(full_module_name):
    try:
        return importlib.import_module(full_module_name)
    except Exception as _:
        return None

def install_module(module, path, update=True):
    # Manage pip path locking
    global PIP_INSTALLED 
    LOCK_SEMAPHORE.acquire()
    lock = LOCKS.get(path, None)
    if lock is None:
        lock = threading.Semaphore(1)
        LOCKS[path] = lock
    LOCK_SEMAPHORE.release()
    lock.acquire()
    # Set temp path 
    origTemp = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = os.path.join(os.getcwd(), "tmp")
    try:
        if find_module("pip") is None and not PIP_INSTALLED:
            print("Downloading pip...")
            subprocess.check_output(["bash", "-c", "cd " + os.path.dirname(path) + " && wget https://bootstrap.pypa.io/get-pip.py && python get-pip.py"])
            subprocess.check_output(["bash", "-c", "cd " + os.path.dirname(path) + " && rm get-pip.py || true"])
            PIP_INSTALLED = True
        command = [sys.executable, "-m", "pip", "install","--target", path]
        if update:
            command.append("--upgrade")
        r = subprocess.run(command + module.split(" ") , capture_output=False) 
        print(module + " installed")
    except Exception as e:
        PIP_INSTALLED = False
        print("Error installing " + module + " " + str(e))
        r = None
    if origTemp:
        os.environ["TMPDIR"] = origTemp
    lock.release()
    return r
