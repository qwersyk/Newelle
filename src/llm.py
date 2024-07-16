from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from gi.repository import Gtk, Adw, Gio, GLib
from gpt4all import GPT4All
import os, threading, subprocess, re

from gpt4all.gpt4all import MessageType
from .bai import BAIChat
import time, json
from .extra import find_module, install_module

class LLMHandler():
    history = []
    prompts = []
    key = "llm"

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path

    @staticmethod 
    def get_extra_settings() -> list:
        return []

    @staticmethod
    def get_extra_requirements() -> list:
        return []

    """ Return if the LLM supports token streaming"""
    def stream_enabled(self) -> bool:
        enabled = self.get_setting("streaming")
        if enabled is None:
            return False
        return enabled

    """ Install the LLM requirements"""
    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        for module in self.get_extra_requirements():
            install_module(module, pip_path)

    """ Return if the LLM is installed"""
    def is_installed(self):
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                return False
        return True

    """ Load the LLM model"""
    def load_model(self, model):
        return True

    """ Get a response to a message"""
    @abstractmethod
    def send_message(self, window, message) -> str:
        pass

    """ Generates a prompt suggestion given text and history"""
    @abstractmethod
    def get_suggestions(self, window, message) -> str:
        pass

    """ Sets chat history"""
    def set_history(self, prompts, window):
        self.prompts = prompts
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]

    """ Get LLM setting"""
    def get_setting(self, key) -> object:
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j or key not in j[self.key]:
            return self.get_default_setting(key)
        return j[self.key][key]

    """ Set LLM setting"""
    def set_setting(self, key, value):
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][key] = value
        self.settings.set_string("llm-settings", json.dumps(j))

    """ Get LLM default setting"""
    def get_default_setting(self, key):
        extra_settings = self.get_extra_settings()
        for s in extra_settings:
            if s["key"] == key:
                return s["default"]
        return None


class G4FHandler(LLMHandler):
    """Common methods for g4f models"""
    key = "g4f"
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["g4f"]

    @abstractmethod
    def generate_response(self, window, message) -> str:
        pass

    @abstractmethod
    def generate_response_stream(self, window, message, on_update, extra_args) -> str:
        pass

    def send_message(self, window, message) -> str:
        """Get a response to a message"""
        self.history.append({"User": "User", "Message": window.bot_prompt+"\n"+"\n".join(self.prompts)})
        return self.generate_response(window, message)

    def send_message_stream(self, window, message, on_update, extra_args):
        """Get a response to a message"""
        self.history.append({"User": "User", "Message": window.bot_prompt+"\n"+"\n".join(self.prompts)})
        return self.generate_response_stream(window, message, on_update, extra_args)

    def get_suggestions(self, window, message):
        """Gets chat suggestions"""
        # Disabled to avoid flood
        return ""
        message = message + "\nUser:"
        return self.generate_response(window, message)

    def convert_history(self, history: dict) -> list:
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


class DeepAIHandler(G4FHandler):
    
    key = "deepai"

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
        ]

    def generate_response(self, window, message):
        return self.__generate_response(window, message)

    def generate_response_stream(self, window, message, on_update, extra_args):
        return self.__generate_response_stream(window, message, on_update, extra_args)

    def __generate_response(self, window, message):
            import g4f
            """Generates a response given text and history"""
            provider = g4f.Provider.DeepAi
            history = self.convert_history(self.history)
            user_prompt = {"role": "user", "content": message}
            history.append(user_prompt)
            response = g4f.ChatCompletion.create(
                model=g4f.models.default,
                messages=history,
                provider=provider
            )
            return response

    def __generate_response_stream(self, window, message, on_update, extra_args):
            import g4f
            """Generates a response given text and history"""
            provider = g4f.Provider.DeepAi
            history = self.convert_history(self.history)
            user_prompt = {"role": "user", "content": message}
            history.append(user_prompt)
            response = g4f.ChatCompletion.create(
                model=g4f.models.default,
                messages=history,
                provider=provider,
                stream=True
            )
            full_message = ""
            for chunk in response:
                full_message += chunk
                print(chunk)
                args = (full_message.strip(), ) + extra_args
                on_update(*args)
            return full_message.strip()

class GoogleBardHandler(G4FHandler):
    key = "bard"

    def generate_response(self, window, message):
        return self.__generate_response(window, message)

    def __generate_response(self, window, message):
            import g4f
            """Generates a response given text and history"""
            provider = g4f.Provider.Bard
            history = self.convert_history(self.history)
            user_prompt = {"role": "user", "content": message}
            history.append(user_prompt)
            response = g4f.ChatCompletion.create(
                model=g4f.models.default,
                messages=history,
                provider=provider,
                auth=True
            )
            return response

class BingHandler(G4FHandler):
    key = "bing"

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
        ]
    def generate_response(self, window, message):
        return self.__generate_response(window, message)

    def generate_response_stream(self, window, message, on_update, extra_args):
        return self.__generate_response_stream(window, message, on_update, extra_args)

    def __generate_response(self, window, message):
            import g4f
            """Generates a response given text and history"""
            provider = g4f.Provider.Bing
            history = self.convert_history(self.history)
            user_prompt = {"role": "user", "content": message}
            history.append(user_prompt)
            response = g4f.ChatCompletion.create(
                model=g4f.models.default,
                messages=history,
                provider=provider,
            )
            return response

    def __generate_response_stream(self, window, message, on_update, extra_args):
            import g4f
            """Generates a response given text and history"""
            provider = g4f.Provider.Bing
            history = self.convert_history(self.history)
            user_prompt = {"role": "user", "content": message}
            history.append(user_prompt)
            response = g4f.ChatCompletion.create(
                model=g4f.models.default,
                messages=history,
                provider=provider,
                stream=True
            )
            full_message = ""
            for chunk in response:
                full_message += chunk
                args = (full_message.strip(), ) + extra_args
                on_update(*args)
            return full_message.strip()

class CustomLLMHandler(LLMHandler):
    key = "custom_command"
    
    @staticmethod
    def get_extra_settings():
        return [
            {
                "key": "command",
                "title": _("Command to execute to get bot output"),
                "description": _("Command to execute to get bot response, {0} will be replaced with a JSON file containing the chat, {1} with the extra prompts"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "suggestion",
                "title": _("Command to execute to get bot's suggestions"),
                "description": _("Command to execute to get chat suggestions, {0} will be replaced with a JSON file containing the chat, {1} with the extra prompts"),
                "type": "entry",
                "default": ""
            },

        ]

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
    key = "openai"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpenAI"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "engine",
                "title": _("OpenAI Engine"),
                "description": _("Name of the OpenAI Engine"),
                "type": "entry",
                "default": "text-davinci-003"
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
            {
                "key": "max-tokens",
                "title": _("Max Tokens"),
                "description": _("Max tokens of the generated text"),
                "website": "https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them",
                "type": "range",
                "min": 3,
                "max": 400,
                "default": 150,
                "round-digits": 0
            },
            {
                "key": "top-p",
                "title": _("Top-P"),
                "description": _("An alternative to sampling with temperature, called nucleus sampling"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-top_p",
                "type": "range",
                "min": 0,
                "max": 1,
                "default": 1,
                "round-digits": 2,
            },
            {
                "key": "temperature",
                "title": _("Temperature"),
                "description": _("What sampling temperature to use. Higher values will make the output more random"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-temperature",
                "type": "range",
                "min": 0,
                "max": 2,
                "default": 1,
                "round-digits": 2,
            },
            {
                "key": "frequency-penalty",
                "title": _("Frequency Penalty"),
                "description": _("Positive values penalize new tokens based on their existing frequency in the text so far, decreasing the model's likelihood to repeat the same line"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-frequency_penalty",
                "type": "range",
                "min": -2,
                "max": 2,
                "default": 0,
                "round-digits": 1,
            },
            {
                "key": "presence-penalty",
                "title": _("Presence Penalty"),
                "description": _("PPositive values penalize new tokens based on whether they appear in the text so far, increasing the model's likelihood to talk about new topics."),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-frequency_penalty",
                "type": "range",
                "min": -2,
                "max": 2,
                "default": 0,
                "round-digits": 1,
            },
        ]
 
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

    def send_message_stream(self, window, message, on_update, extra_args):
        message = self.history + "\nUser:" + str(message) + "\nAssistant:"
        return self.__generate_response_stream(window, message, on_update, extra_args)

    def __generate_response_stream(self, window, message, on_update, extra_args):
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
            stream=True
        )
        full_message = ""
        counter = 0
        counter_size = 1
        for chunk in response:
            counter += 1
            full_message += chunk.choices[0]["text"]
            if counter == counter_size:
                counter = 0
                counter_size=int((counter_size + 3)*1.3)
                args = (full_message.strip(), ) + extra_args
                on_update(*args)
        return full_message.strip()

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
    key = "bai"
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
    key = "local"

    def __init__(self, settings, modelspath):
        """This class handles downloading, generating and history managing for Local Models using GPT4All library
        """
        self.settings = settings
        self.modelspath = modelspath
        self.history = {}
        # Temporary
        self.oldhistory = {}
        self.prompts = []
        self.model = None
        self.session = None
        if not os.path.isdir(self.modelspath):
            os.makedirs(self.modelspath)

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
                self.session = self.model.chat_session()
                self.session.__enter__()
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
        # History not working at the moment because of GPT4All
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]
        print(self.oldhistory)
        print(self.history)
        newchat = False
        for message in self.oldhistory:
            if not any(message == item["Message"] for item in self.history):
               newchat = True
               break
        if len(self.oldhistory) > 1 and newchat:
            print("New session")
            self.session.__exit__(None, None, None)
            self.session = self.model.chat_session()
            self.session.__enter__()
        self.oldhistory = list()
        for message in self.history:
            self.oldhistory.append(message["Message"])
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

    def __create_history(self, history):
        for message in history:
            if message not in self.model.current_chat_session:
                self.model.current_chat_session.append(message)

    def __generate_response(self, window, message):
        """Generates a response given text and history"""
        if not self.load_model(window.local_model):
            return _('There was an error retriving the model')
        history = self.__convert_history(self.history)
        if self.model is None or self.session is None:
            return "Model not loaded"
        #print(self.model.current_chat_session)
        response = self.model.generate(prompt=message, top_k=1)
        #print(self.model.current_chat_session)
        return response


