import os, sys, subprocess
import importlib

def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    try:
        return importlib.import_module(full_module_name)
    except ImportError as exc:
        if not (full_module_name + '.').startswith(exc.name + '.'):
            raise


def install_module(module, path):
    r = subprocess.check_output(["pip3", "install", "--target", path, module]).decode("utf-8")
    return r

class STTHandler:
    def __init__(self, settings, pip_path, stt):
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt

    def install(self):
        for module in self.stt["extra_requirements"]:
            install_module(module, self.pip_path)

    def is_installed(self):
        for module in self.stt["extra_requirements"]:
            if find_module(module) is None:
                return False
        return True

class SphinxHandler(STTHandler):
    def __init__(self, settings, pip_path, stt):
        self.key = "Sphinx"
        self.settings = settings
        self.pip_path = pip_path
        self.stt = stt




                    
