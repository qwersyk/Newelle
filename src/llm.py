from abc import abstractmethod
from subprocess import check_output
import os, threading
from typing import Callable, Any
import time, json

from .extra import find_module, install_module

class LLMHandler():
    """Every LLM model handler should extend this class."""
    history = []
    prompts = []
    key = "llm"

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return False

    @staticmethod
    def get_extra_settings() -> list:
        """
        Extra settings format:
            Required parameters:
            - title: small title for the setting 
            - description: description for the setting
            - default: default value for the setting
            - type: What type of row to create, possible rows:
                - entry: input text 
                - toggle: bool
                - combo: for multiple choice
                    - values: list of touples of possible values (display_value, actual_value)
                - range: for number input with a slider 
                    - min: minimum value
                    - max: maximum value 
                    - round: how many digits to round 
            Optional parameters:
                - folder: add a button that opens a folder with the specified path
                - website: add a button that opens a website with the specified path 
        """
        return []

    @staticmethod
    def get_extra_requirements() -> list:
        """The list of extra pip requirements needed by the handler"""
        return []

    def stream_enabled(self) -> bool:
        """ Return if the LLM supports token streaming"""
        enabled = self.get_setting("streaming")
        if enabled is None:
            return False
        return enabled

    def install(self):
        """Install the LLM requirements"""
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        for module in self.get_extra_requirements():
            install_module(module, pip_path)

    def is_installed(self) -> bool:
        """Return if the LLM is installed"""
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                return False
        return True

    def load_model(self, model):
        """ Load the specified model """
        return True

    def set_history(self, prompts : list[str], window):
        """Set the current history and prompts

        Args:
            prompts (list[str]): list of sytem prompts
            window : Application window
        """        
        self.prompts = prompts
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]

    def get_setting(self, key: str) -> Any:
        """Get a setting from the given key

        Args:
            key (str): key of the setting

        Returns:
            object: value of the setting
        """        
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j or key not in j[self.key]:
            return self.get_default_setting(key)
        return j[self.key][key]

    def set_setting(self, key : str, value):
        """Set a setting from key and value for this handler

        Args:
            key (str): key of the setting
            value (object): value of the setting
        """        
        j = json.loads(self.settings.get_string("llm-settings"))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][key] = value
        self.settings.set_string("llm-settings", json.dumps(j))

    def get_default_setting(self, key) -> object:
        """Get the default setting from a certain key

        Args:
            key (str): key of the setting

        Returns:
            object: setting value
        """
        extra_settings = self.get_extra_settings()
        for s in extra_settings:
            if s["key"] == key:
                return s["default"]
        return None

    @abstractmethod
    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        """Generate test from the given prompt, history and system prompt

        Args:
            prompt (str): text of the prompt
            history (dict[str, str], optional): history of the chat. Defaults to {}.
            system_prompt (list[str], optional): content of the system prompt. Defaults to [].

        Returns:
            str: generated text
        """        
        pass

    @abstractmethod
    def generate_text_stream(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = [], on_update: Callable[[str], Any] = (), extra_args : list = []) -> str:
        """_summary_

        Args:
            prompt (str): text of the prompt
            history (dict[str, str], optional): history of the chat. Defaults to {}.
            system_prompt (list[str], optional): content of the system prompt. Defaults to [].
            on_update (Callable[[str], Any], optional): Function to call when text is generated. The partial message is the first agrument Defaults to ().
            extra_args (list, optional): extra arguments to pass to the on_update function. Defaults to [].
        
        Returns:
            str: generated text
        """  
        pass

    def send_message(self, window, message:str) -> str:
        """Send a message to the bot

        Args:
            window: The window
            message: Text of the message

        Returns:
            str: Response of the bot
        """        
        return self.generate_text(message, self.history, self.prompts)

    def send_message_stream(self, window, message:str, on_update: Callable[[str], Any] = (), extra_args : list = []) -> str:
        """Send a message to the bot

        Args:
            window: The window
            message: Text of the message
            on_update (Callable[[str], Any], optional): Function to call when text is generated. The partial message is the first agrument Defaults to ().
            extra_args (list, optional): extra arguments to pass to the on_update function. Defaults to [].

        Returns:
            str: Response of the bot
        """        
        return self.generate_text_stream(message, self.history, self.prompts, on_update, extra_args)

    def get_suggestions(self, request_prompt:str = None, amount:int=1) -> list[str]:
        """Get suggestions for the current chat. The default implementation expects the result as a JSON Array containing the suggestions

        Args:
            request_prompt: The prompt to get the suggestions
            amount: Amount of suggstions to generate

        Returns:
            list[str]: prompt suggestions
        """
        result = []
        history = ""
        # Only get the last four elements and reconstruct partial history
        for message in self.history[-4:] if len(self.history) >= 4 else self.history:
            history += message["User"] + ": " + message["Message"] + "\n"
        for i in range(0, amount):
            generated = self.generate_text(history + "\n\n" + request_prompt)
            generated = generated.replace("```json", "").replace("```", "")
            try:
                j = json.loads(generated)
            except Exception as e:
                continue
            if type(j) is list:
                for suggestion in j:
                    if type(suggestion) is str:
                        result.append(suggestion)
                        i+=1
                        if i >= amount:
                            break
        return result

    def generate_chat_name(self, request_prompt:str = None) -> str:
        """Generate name of the current chat

        Args:
            request_prompt (str, optional): Extra prompt to generate the name. Defaults to None.

        Returns:
            str: name of the chat
        """
        return self.generate_text(request_prompt, self.history)


class G4FHandler(LLMHandler):
    """Common methods for g4f models"""
    key = "g4f"
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["g4f"]

    def convert_history(self, history: dict) -> list:
        result = []
        result.append({"role": "system", "content": "\n".join(self.prompts)})
        for message in history:
            result.append({
                "role": message["User"].lower(),
                "content": message["Message"]
            })
        return result

    def set_history(self, prompts, window):
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]
        self.prompts = prompts


class GPT3AnyHandler(G4FHandler):
    """
    Use any GPT3.5-Turbo providers
    - History is supported by almost all of them
    - System prompts are not well supported, so the prompt is put on top of the message
    """
    key = "GPT3Any"

    def __init__(self, settings, path):
        import g4f
        super().__init__(settings, path)
        self.client = g4f.client.Client()

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

    def convert_history(self, history: dict) -> list:
        """Converts the given history into the correct format for current_chat_history"""
        result = []
        #result.append({"role": "system", "content": "\n".join(self.prompts)})
        for message in history:
            result.append({
                "role": message["User"].lower(),
                "content": message["Message"]
            })
        return result

    def generate_response(self, window, message):
        return self.__generate_response(window, message)

    def generate_response_stream(self, window, message, on_update, extra_args):
        return self.__generate_response_stream(window, message, on_update, extra_args)


    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        # Add prompts in the message since some providers
        # don't support system prompts well
        message = prompt
        if len (self.prompts) > 0:
            message = "SYSTEM:" + "\n".join(system_prompt) + "\n\n" + prompt
        history = self.convert_history(history)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=history,
        )
        return response.choices[0].message.content

    def generate_text_stream(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = [], on_update: Callable[[str], Any] = (), extra_args: list = []) -> str:
        # Add prompts in the message since some providers
        # don't support system prompts well
        import g4f
        message = prompt
        if len (self.prompts) > 0:
            message = "SYSTEM:" + "\n".join(system_prompt) + "\n\n" + prompt
        history = self.convert_history(history)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=history,
            stream=True,
        )
        full_message = ""
        prev_message = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                full_message += chunk.choices[0].delta.content
                args = (full_message.strip(), ) + extra_args
                if len(full_message) - len(prev_message) > 1:
                    on_update(*args)
                    prev_message = full_message
        return full_message.strip()

    def send_message(self, window, message: str) -> str:
        return self.generate_text(window.chat[-1]["User"] + ": " + message, self.history, self.prompts)
     
    def send_message_stream(self, window, message: str, on_update: Callable[[str], Any] = (), extra_args: list = []) -> str:
        return self.generate_text_stream(window.chat[-1]["User"] + ": " + message, self.history, self.prompts, on_update, extra_args)

    def generate_chat_name(self, request_prompt: str = None) -> str:
        history = ""
        for message in self.history[-4:] if len(self.history) >= 4 else self.history:
            history += message["User"] + ": " + message["Message"] + "\n"
        name = self.generate_text(history + "\n\n" + request_prompt)
        return name

class GeminiHandler(LLMHandler):
    key = "gemini"
    """
    Official GOogle Gemini APIs, they support history and system prompts
    """

    @staticmethod
    def get_extra_requirements() -> list:
        return ["google-generativeai"]

    def is_installed(self) -> bool:
        if find_module("google.generativeai") is None:
            return False
        return True

    @staticmethod
    def get_extra_settings() -> list:
        return [
            {
                "key": "apikey",
                "title": _("API Key (required)"),
                "description": _("API Key got from ai.google.dev"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "model",
                "title": _("Model"),
                "description": _("AI Model to use, available: gemini-1.5-pro, gemini-1.0-pro, gemini-1.5-flash"),
                "type": "combo",
                "default": "gemini-1.5-flash",
                "values": [("gemini-1.5-flash","gemini-1.5-flash") , ("gemini-1.0-pro", "gemini-1.0-pro"), ("gemini-1.5-pro","gemini-1.5-pro") ]
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
        ]

    def __convert_history(self, history: list):
        result = []
        for message in history:
            result.append({
                "role": message["User"].lower() if message["User"] == "User" else "model",
                "parts": message["Message"]
            })
        return result

    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.get_setting("apikey"))
        instructions = "\n"+"\n".join(system_prompt)
        model = genai.GenerativeModel(self.get_setting("model"), system_instruction=instructions)
        converted_history = self.__convert_history(history)

        chat = model.start_chat(
            history=converted_history,
        )
        response = chat.send_message(prompt)
        return response.text

    def generate_text_stream(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = [], on_update: Callable[[str], Any] = (), extra_args: list = []) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.get_setting("apikey"))
        instructions = "\n".join(system_prompt)
        model = genai.GenerativeModel(self.get_setting("model"), system_instruction=instructions)
        converted_history = self.__convert_history(history)
        chat = model.start_chat(
            history=converted_history,
        )

        response = chat.send_message(prompt, stream=True)
        full_message = ""
        for chunk in response:
            full_message += chunk.text
            args = (full_message.strip(), ) + extra_args
            on_update(*args)
        return full_message.strip()


class CustomLLMHandler(LLMHandler):
    key = "custom_command"
    
    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

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

    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        command = self.get_setting("command")
        command = command.replace("{0}", json.dumps(self.history))
        command = command.replace("{1}", json.dumps(self.prompts))
        out = check_output(["flatpak-spawn", "--host", "bash", "-c", command])
        return out.decode("utf-8")

    def get_suggestions(self, prompt, amount):
        command = self.get_setting("suggestion")
        command = command.replace("{0}", json.dumps(self.history))
        command = command.replace("{1}", json.dumps(self.prompts))
        out = check_output(["flatpak-spawn", "--host", "bash", "-c", command])
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
                "key": "endpoint",
                "title": _("API Endpoint"),
                "description": _("API base url, change this to use interference APIs"),
                "type": "entry",
                "default": "https://api.openai.com/v1/"
            },
            {
                "key": "model",
                "title": _("OpenAI Model"),
                "description": _("Name of the OpenAI Model"),
                "type": "entry",
                "default": "gpt3.5-turbo"
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

    def convert_history(self, history: dict, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        result = []
        result.append({"role": "system", "content": "\n".join(prompts)})
        for message in history:
            result.append({
                "role": message["User"].lower(),
                "content": message["Message"]
            })
        return result

    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        from openai import OpenAI
        messages = self.convert_history(history, system_prompt)
        messages.append({"role": "user", "content": prompt})
        client = OpenAI(
            api_key=self.get_setting("api"),
            base_url=self.get_setting("endpoint")
        )
        try:
            response = client.chat.completions.create(
                model=self.get_setting("model"),
                messages=messages,
                top_p=self.get_setting("top-p"),
                max_tokens=self.get_setting("max_tokens"),
                temperature=self.get_setting("temperature"),
                presence_penalty=self.get_setting("presence_penalty"),
                frequency_penalty=self.get_setting("frequency_penalty")
            )
            return response.choices[0].message.content
        except Exception as e:
            return str(e)
    
    def generate_text_stream(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = [], on_update: Callable[[str], Any] = (), extra_args: list = []) -> str:
        from openai import OpenAI
        messages = self.convert_history(history, system_prompt)
        messages.append({"role": "user", "content": prompt})
        client = OpenAI(
            api_key=self.get_setting("api"),
            base_url=self.get_setting("endpoint")
        )
        try:
            response = client.chat.completions.create(
                model=self.get_setting("model"),
                messages=messages,
                top_p=self.get_setting("top-p"),
                max_tokens=self.get_setting("max_tokens"),
                temperature=self.get_setting("temperature"),
                presence_penalty=self.get_setting("presence_penalty"),
                frequency_penalty=self.get_setting("frequency_penalty"),
                stream=True
            )
            full_message = ""
            prev_message = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    full_message += chunk.choices[0].delta.content
                    args = (full_message.strip(), ) + extra_args
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
            return full_message.strip()
        except Exception as e:
            return str(e)
    



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
        self.history = window.chat[len(window.chat) - window.memory:len(window.chat)-1]
        newchat = False
        for message in self.oldhistory:
            if not any(message == item["Message"] for item in self.history):
               newchat = True
               break
        
        # Create a new chat
        system_prompt = "\n".join(prompts)
        if len(self.oldhistory) > 1 and newchat:
            self.session.__exit__(None, None, None)
            self.session = self.model.chat_session(system_prompt)
            self.session.__enter__()
        self.oldhistory = list()
        for message in self.history:
            self.oldhistory.append(message["Message"])
        self.prompts = prompts



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
        if self.model is None or self.session is None:
            return "Model not loaded"
        response = self.model.generate(prompt=message, top_k=1)
        return response

    def generate_text(self, prompt: str, history: dict[str, str] = {}, system_prompt: list[str] = []) -> str:
        # History not working for text generation
        oldsession = self.session
        self.session.__exit__(None, None, None)
        system_prompt = "\n".join(prompt)
        self.session = self.model.chat_session(system_prompt)
        self.session.__enter__()
        response = self.model.generate(prompt=prompt, top_k=1)
        self.session.__exit__(None, None, None)
        self.session = oldsession
        self.session.__enter__()
        return response

    def get_suggestions(self, request_prompt:str = None) -> list[str]:
        # Avoid to generate suggestions
        return []

    def generate_chat_name(self, request_prompt:str = None) -> str:
        # Avoid to generate chat name
        return ""
