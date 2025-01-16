import os
import re
import threading
from typing import Callable, Any

from .llm import LLMHandler
from ...utility.strings import human_readable_size

class GPT4AllHandler(LLMHandler):
    key = "local"
    model_library = []

    def __init__(self, settings, modelspath):
        """This class handles downloading, generating and history managing for Local Models using GPT4All library
        """
        self.settings = settings
        self.modelspath = modelspath
        self.history = {}
        self.model_folder = os.path.join(self.modelspath, "custom_models")
        if not os.path.isdir(self.model_folder):
            os.makedirs(self.model_folder)
        self.oldhistory = {}
        self.prompts = []
        self.model = None
        self.session = None
        if not os.path.isdir(self.modelspath):
            os.makedirs(self.modelspath) 
        self.downloading = {}
        if self.get_setting("model-library", False) is not None:
            self.model_library = self.get_setting("model-library", False)
        if self.get_setting("models-info", False) is not None:
            self.models = self.tup(self.get_setting("models", False))
            self.models_info = self.get_setting("models-info", False)
        else:
            self.models = tuple()
            self.models_info = {}
            threading.Thread(target=self.get_models_infomation, args=()).start()
    

    @staticmethod
    def tup(l):
        t = tuple()
        for x in l:
            t += ((x[0], x[1]), )
        return t

    def get_models_infomation(self):
        """Get gpt4all models information""" 
        from gpt4all import GPT4All
        models = GPT4All.list_models()
        self.set_setting("models-info", models)
        self.models_info = models
        self.add_library_information()

    def add_library_information(self):
        library = []
        models = tuple()
        for model in self.models_info:
            available = self.model_available(model["filename"])
            if available:
                models += ((model["name"], model["filename"]), )
            subtitle = _(" RAM Required: ") + str(model["ramrequired"]) + "GB"
            subtitle += "\n" + _(" Parameters: ") + model["parameters"]
            subtitle += "\n" + _(" Size: ") + human_readable_size(model["filesize"], 1)
            subtitle += "\n" + re.sub('<[^<]+?>', '', model["description"]).replace("</ul", "")
            # Configure buttons and model's row 
            library.append({
                "key": model["filename"],
                "title": model["name"],
                "description": subtitle
            })
        self.set_setting("model-library", library)
        self.model_library = library
        self.models = models
        self.set_setting("models", self.models)
        self.settings_update()

    def get_model_library(self) -> list:
        """Create extra settings to download models from the mode library

        Returns:
           extra settings 
        """
        res = []
        for model in self.model_library:
            s = {
                "type": "download",
                "key": model["key"],
                "title": model["title"],
                "description": model["description"],
                "is_installed": self.model_available(model["key"]),
                "callback": self.install_model,
                "download_percentage": self.get_percentage,
                "default": None,
            }
            res.append(s)
        return res

    def install_model(self, model: str):
        """Install a local model

        Args:
            model (str): name of the model
        """
        if self.model_available(model):
            self.remove_local_model(model)
            return
        self.downloading[model] = True
        self.download_model(model)

    def get_percentage(self, model: str):
        filesize = None
        for x in self.models_info:
            if x["filename"] == model:
                filesize = x["filesize"]
        if filesize is None:
            return
        file = os.path.join(self.modelspath, model) + ".part"
        currentsize = os.path.getsize(file)
        perc = currentsize/int(filesize)
        return perc
    
    def remove_local_model(self, model):
        """Remove a local model

        Args:
            button (): button for the local model
        """
        try:
            os.remove(os.path.join(self.modelspath, model))
            if model in self.downloading:
                self.downloading[model] = False
            self.get_models_infomation()
            self.settings_update()
        except Exception as e:
            print(e)

    def get_extra_settings(self) -> list:
        models = self.get_custom_model_list() + self.models
        default = models[0][1] if len(models) > 0 else ""
        settings = [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True,
            }, 
            {
                "key": "model",
                "title": _("Model to use"),
                "description": _("Name of the model to use. You can download models from the model manager. You can add custom gguf files in the specified folder"),
                "type": "combo",
                "default": default,
                "values": models,
                "folder": self.model_folder,
                "refresh": lambda _: self.get_models_infomation()
            },
        ]
        settings += [{
            "key": "model_manager",
            "title": _("Model Manager"),
            "description": _("List of models available"),
            "type": "nested",
            "extra_settings": self.get_model_library()
        }]
        return settings
    
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

    def model_available(self, model:str) -> bool:
        """ Returns if a model has already been downloaded"""
        from gpt4all import GPT4All
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
            print(model)
            try:
                from gpt4all import GPT4All
                if model == "custom":
                    model = self.get_setting("custom_model")
                    models = self.get_custom_model_list()
                    if model not in models:
                        if len(models) > 0:
                            model = models[0][1]
                    self.model = GPT4All(model, model_path=self.model_folder)
                else:
                    self.model = GPT4All(model, model_path=self.modelspath)
                self.session = self.model.chat_session()
                self.session.__enter__()
            except Exception as e:
                print("Error loading the model: ", e)
                return False
            return True

    def download_model(self, model:str) -> bool:
        """Downloads GPT4All model"""
        try:
            from gpt4all import GPT4All
            GPT4All.retrieve_model(model, model_path=self.modelspath, allow_download=True, verbose=False)
        except Exception as e:
            print(e)
            return False 
        self.get_models_infomation()
        return True

    def __convert_history_text(self, history: list) -> str:
        """Converts the given history into the correct format for current_chat_history"""
        result = "### Previous History"
        for message in history:
            result += "\n" + message["User"] + ":" + message["Message"]
        return result
    
    def set_history(self, prompts, history):
        """Manages messages history"""
        self.history = history 
        newchat = False
        for message in self.oldhistory:
            if not any(message == item["Message"] for item in self.history):
               newchat = True
               break
        
        # Create a new chat
        system_prompt = "\n".join(prompts)
        if len(self.oldhistory) > 1 and newchat:
            if self.session is not None and self.model is not None:
                self.session.__exit__(None, None, None)
                self.session = self.model.chat_session(system_prompt)
                self.session.__enter__()
        self.oldhistory = list()
        for message in self.history:
            self.oldhistory.append(message["Message"])
        self.prompts = prompts

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        if self.session is None or self.model is None:
            return "Model not yet loaded..."
        # Temporary history management
        if len(history) > 0:
            system_prompt.append(self.__convert_history_text(history))
        prompts = "\n".join(system_prompt)
        print(prompts)
        self.session = self.model.chat_session(prompts)
        self.session.__enter__()
        response = self.model.generate(prompt=prompt, top_k=1, streaming=True)
        
        full_message = ""
        prev_message = ""
        for chunk in response:
            if chunk is not None:
                    full_message += chunk
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
        return full_message.strip()

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        # History not working for text generation
        if self.session is None or self.model is None:
            return "Model not yet loaded..."
        if len(history) > 0:
            system_prompt.append(self.__convert_history_text(history)) 
        prompts = "\n".join(system_prompt)
        self.session = self.model.chat_session(prompts)
        self.session.__enter__()
        response = self.model.generate(prompt=prompt, top_k=1)
        self.session.__exit__(None, None, None)
        return response
    
    def get_suggestions(self, request_prompt: str = "", amount: int = 1) -> list[str]:
        # Avoid generating suggestions
        return []

    def generate_chat_name(self, request_prompt: str = "") -> str:
        # Avoid generating chat name
        return "Chat"

