from gi.repository import Gtk, Adw, Gio, GLib
from gpt4all import GPT4All
import os

class GPT4AllHandler:

    def __init__(self, settings, modelspath):
        self.settings = settings
        self.modelspath = modelspath
        if not os.path.isdir(self.modelspath):
            os.makedirs(self.modelspath)
        print(self.modelspath)

    def model_available(self, model:str) -> bool:
        """ Returns if a model has already been downloaded
        """
        try:
            GPT4All.retrieve_model(model, model_path=self.modelspath, allow_download=False, verbose=False)
        except Exception as e:
            return False
        return True

    def download_model(self, model:str) -> bool:
        try:
            GPT4All.retrieve_model(model, model_path=self.modelspath, allow_download=True, verbose=False)
        except Exception as e:
            print(e)
            return False
        return True
