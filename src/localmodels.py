from gi.repository import Gtk, Adw, Gio, GLib
from gpt4all import GPT4All
import os

class GPT4AllHandler:

    def __init__(self, settings, modelspath):
        self.settings = settings
        self.modelspath = modelspath
        self.model = None
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

    def load_model(self, model:str):
        if self.model is None:
            try:
                self.model = GPT4All(model)
            except Exception as e:
                print(e)
                return False
            return True
    def download_model(self, model:str) -> bool:
        try:
            GPT4All.retrieve_model(model, model_path=self.modelspath, allow_download=True, verbose=False)
        except Exception as e:
            print(e)
            return False
        return True

    def convert_history(self, history: dict) -> dict:
        result = []
        for message in history:
            result.append({
                "role": message["User"].lower(),
                "content": message["Message"]
            })
        return result


    def send_message(self, window, message):
        if not self.load_model(window.local_model) and False:
            return _('There was an error retriving the model')
        history = self.convert_history(window.chat)
        session = self.model.chat_session()
        print(history, message)
        with session:
            self.model.current_chat_session = history
            response = self.model.generate(prompt=message, top_k=1)
        return response
