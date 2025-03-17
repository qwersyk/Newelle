from gi.repository import GLib, Gio
import os
from .utility.system import is_flatpak
from .utility.pip import install_module
from .constants import DIR_NAME, SCHEMA_ID
import sys
import threading
import pickle

"""
Not yet used in the code.
Manage Newelle Application, create handlers, check integrity, manage settings...

"""
class NewelleController:
    def __init__(self) -> None:
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.init_paths()
        self.check_path_integrity()
        self.load_chats()

    def init_paths(self) -> None:
        self.config_dir = GLib.get_user_config_dir()
        self.data_dir = GLib.get_user_data_dir()
        self.cache_dir = GLib.get_user_cache_dir()

        if not is_flatpak():
            self.config_dir = os.path.join(self.config_dir, DIR_NAME)
            self.data_dir = os.path.join(self.config_dir, DIR_NAME)
            self.cache_dir = os.path.join(self.cache_dir, DIR_NAME)


        self.pip_path = os.path.join(self.config_dir, "pip")
        self.models_dir = os.path.join(self.config_dir, "models")
        self.extension_path = os.path.join(self.config_dir, "extensions")
        self.extensions_cache = os.path.join(self.cache_dir, "extensions_cache")

    def load_chats(self):
        self.filename = "chats.pkl"
        if os.path.exists(self.data_dir + self.filename):
            with open(self.data_dir + self.filename, 'rb') as f:
                self.chats = pickle.load(f)
        else:
            self.chats = [{"name": _("Chat ") + "1", "chat": []}]
    
    def check_path_integrity(self):
        # Create directories
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        if not os.path.exists(self.extension_path):
            os.makedirs(self.extension_path)
        if not os.path.exists(self.extensions_cache):
            os.makedirs(self.extensions_cache)
        # Fix Pip environment
        if os.path.isdir(self.pip_path):
            sys.path.append(self.pip_path)
        else:
            threading.Thread(target=self.init_pip_path, args=(sys.path,)).start()

    def init_pip_path(self, path):
        """Install a pip module to init a pip path"""
        install_module("pip-install-test", self.pip_path)
        path.append(self.pip_path)
