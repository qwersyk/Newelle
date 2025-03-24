import importlib
import subprocess
import sys
import os 

def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    try:
        return importlib.import_module(full_module_name)
    except Exception as _:
        return None


def install_module(module, path):
    if find_module("pip") is None:
        print("Downloading pip...")
        subprocess.check_output(["bash", "-c", "cd " + os.path.dirname(path) + " && wget https://bootstrap.pypa.io/get-pip.py && python get-pip.py"])
        subprocess.check_output(["bash", "-c", "cd " + os.path.dirname(path) + " && rm get-pip.py"])
    r = subprocess.run([sys.executable, "-m", "pip", "install","--target", path, "--upgrade", module], capture_output=False) 
    return r
