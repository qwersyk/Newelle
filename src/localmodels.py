from gi.repository import Gtk, Adw, Gio, GLib
from gpt4all import GPT4All
import os, threading

class GPT4AllHandler:

    def __init__(self, settings, modelspath):
        """This class handles downloading, generating and history managing for Local Models using GPT4All library
        """
        self.settings = settings
        self.modelspath = modelspath
        self.model = None
        self.history = {}
        self.prompts = []
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
        """Loads the local model on another thread"""
        t = threading.Thread(target=self.load_model_async, args=(model, ))
        t.start()
        return True

    def load_model_async(self, model: str):
        """Loads the local model"""
        if self.model is None:
            try:
                self.model = GPT4All(model, model_path=self.modelspath)
            except Exception as e:
                print(e)
                return False
            return True

    def download_model(self, model:str) -> bool:
        """Downloads GPT4All model"""
        try:
            GPT4All.retrieve_model(model, model_path=self.modelspath, allow_download=True, verbose=False)
        except Exception as e:
            print(e)
            return False
        return True

    def __convert_history(self, history: dict) -> dict:
        """Converts the given history into the correct format for current_chat_history"""
        result = []
        for message in history:
            result.append({
                "role": message["User"].lower(),
                "content": message["Message"]
            })
        return result

    def set_history(self, prompts, window):
        """Manages messages history"""
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]
        self.prompts = prompts

    def get_suggestions(self, window, message):
        """Gets chat suggestions"""
        message = message + "\n User:"
        return self.send_message(window, message)

    def send_message(self, window, message):
        """Get a response to a message"""
        additional_prompts = "\n".join(self.prompts)
        prompt = additional_prompts + "\nUser: " + message
        return self.__generate_response(window, prompt)

    def __generate_response(self, window, message):
        """Generates a response given text and history"""
        if not self.load_model(window.local_model):
            return _('There was an error retriving the model')
        history = self.__convert_history(self.history)
        session = self.model.chat_session()
        with session:
            self.model.current_chat_session = history
            response = self.model.generate(prompt=prompt, top_k=1)
        return response
