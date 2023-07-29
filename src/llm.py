from gi.repository import Gtk, Adw, Gio, GLib
from gpt4all import GPT4All
import os, threading, subprocess
from .bai import BAIChat
import time, json
from .extra import find_module, install_module

class LLMHandler():
    def __init__(self, settings, path, llm):
        self.history = []
        self.propmts = []
        self.settings = settings
        self.path = path
        self.llm = llm

    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        for module in self.llm["extra_requirements"]:
            install_module(module, pip_path)

    def is_installed(self):
        for module in self.llm["extra_requirements"]:
            if find_module(module) is None:
                return False
        return True

    def load_model(self, model):
        return True

    def send_message(self, window, message):
        return "Not yet implemented"

    def get_suggestions(self, window, message):
        return "Not yet implemented"

    def set_history(self, prompts, window):
        self.prompts = prompts
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]

    def get_setting(self, key):
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j or key not in j[self.key]:
            return self.get_default_setting(key)
        return j[self.key][key]

    def set_setting(self, key, value):
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][key] = value
        self.settings.set_string("llm-settings", json.dumps(j))

    def get_default_setting(self, key):
        extra_settings = self.llm["extra_settings"]
        for s in extra_settings:
            if s["key"] == key:
                return s["default"]
        return None

class PoeHandler(LLMHandler):
    def __init__(self, settings, path, llm):
        self.history = ""
        self.propmts = []
        self.key = "poe"
        self.settings = settings
        self.path = path
        self.llm = llm
        self.client = None

    def load_model(self, model:str = None):
        """Loads the local model on another thread"""
        t = threading.Thread(target=self.load_model_async)
        t.start()
        return True

    def load_model_async(self):
        import poe
        token = self.get_setting("token")
        self.client = poe.Client(token)

    def is_installed(self):
        if find_module("poe") is None:
            return False
        return True

    def send_message(self, window, message):
        """Get a response to a message"""
        return self.__generate_response(window, message)

    def __generate_response(self, window, message):
        if self.client is None:
            self.load_model_async()
        codename = self.get_setting("codename")
        #chunks = []
        for chunk in self.client.send_message(codename, message):
            #chunks.append(chunk["text_new"])
            pass
        response = chunk["text"].lstrip("\n")
        return response

    def clear_conversation(self):
        codename = self.get_setting("codename")
        self.client.send_chat_break(codename)

    def set_history(self, prompts, window):
        """Manages messages history"""
        if len(window.chat) == 1:
            t = threading.Thread(target=self.clear_conversation)
            t.start()

    def get_suggestions(self, window, message):
        return ""


class CustomLLMHandler(LLMHandler):
    def __init__(self, settings, path, llm):
        self.history = []
        self.propmts = []
        self.key = "custom_command"
        self.settings = settings
        self.path = path
        self.llm = llm

    def set_history(self, prompts, window):
        self.prompts = prompts
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)]

    def send_message(self, window, message):
        command = self.get_setting("command")
        command = command.replace("{0}", json.dumps(self.history))
        command = command.replace("{1}", json.dumps(self.prompts))
        out = subprocess.check_output(["flatpak-spawn", "--host", "bash", "-c", command])
        return out.decode("utf-8")

    def get_suggestions(self, window, message):
        command = self.get_setting("suggestion")
        command = command.replace("{0}", json.dumps(self.history))
        command = command.replace("{1}", json.dumps(self.prompts))
        out = subprocess.check_output(["flatpak-spawn", "--host", "bash", "-c", command])
        return out.decode("utf-8")

class OpenAIHandler(LLMHandler):
    def __init__(self, settings, path, llm):
        self.history = ""
        self.propmts = []
        self.key = "openai"
        self.settings = settings
        self.path = path
        self.llm = llm

    def send_message(self, window, message):
        """Get a response to a message"""
        message = self.history + "\nUser:" + str(message) + "\nAssistant:"
        return self.__generate_response(window, message)

    def __generate_response(self, window, message):
        engine = self.get_setting("engine")
        max_tokens = int(self.get_setting("max-tokens"))
        top_p = self.get_setting("top-p")
        frequency_penalty = self.get_setting("frequency-penalty")
        presence_penalty = self.get_setting("presence-penalty")
        temperature = self.get_setting("temperature")
        import openai
        openai.api_key = self.get_setting("api")
        response = openai.Completion.create(
            engine=engine,
            prompt=message,
            max_tokens=max_tokens,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            temperature=temperature,
        ).choices[0].text.strip()
        return response

    def get_suggestions(self, window, message):
        """Gets chat suggestions"""
        #message = message + "\nUser:"
        #return self.__generate_response(window, message)
        # It will get API limited if I leave this
        return ""

    def set_history(self, prompts, window):
        """Manages messages history"""
        self.history = window.bot_prompt+"\n"+"\n".join(prompts)+"\n" + window.get_chat(
            window.chat[len(window.chat) - window.memory:len(window.chat)-1])

class BaiHandler(LLMHandler):
    def __init__(self, settings, path, llm):
        """This class Handles BAI Chat generating"""
        self.history = ""
        self.key = "bai"
        self.llm = llm

    def load_model(self, model):
        """Does nothing since it is not required to load the model"""
        return True

    def send_message(self, window, message):
        """Get a response to a message"""
        message = self.history + "\nUser:" + str(message) + "\nAssistant:"
        return self.__generate_response(window, message)

    def __generate_response(self, window, message):
            """Generates a response given text and history"""
            stream_number_variable = window.stream_number_variable
            loop_interval_variable = 1
            while stream_number_variable == window.stream_number_variable:
                loop_interval_variable *= 2
                loop_interval_variable = min(60,loop_interval_variable)
                try:
                    t = re.split(r'Assistant:|Console:|User:|File:|Folder:', BAIChat(sync=True).sync_ask(message).text,1)[0]
                    return t
                except Exception as e:
                    # self.notification_block.add_toast(Adw.Toast(title=_('Failed to send bot a message'), timeout=2))
                    pass
                time.sleep(loop_interval_variable)
            return _("Chat has been stopped")

    def get_suggestions(self, window, message):
        """Gets chat suggestions"""
        message = message + "\nUser:"
        return self.__generate_response(window, message)

    def set_history(self, prompts, window):
        """Manages messages history"""
        self.history = window.bot_prompt+"\n"+"\n".join(prompts)+"\n" + window.get_chat(
            window.chat[len(window.chat) - window.memory:len(window.chat)-1])

class GPT4AllHandler(LLMHandler):

    def __init__(self, settings, modelspath, llm):
        """This class handles downloading, generating and history managing for Local Models using GPT4All library
        """
        self.key = "local"
        self.settings = settings
        self.modelspath = modelspath
        self.model = None
        self.llm = llm
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
            response = self.model.generate(prompt=message, top_k=1)
        return response
