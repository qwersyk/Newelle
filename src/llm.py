from abc import abstractmethod
from subprocess import PIPE, Popen, check_output
import os, threading
from typing import Callable, Any
import json
import base64
from .extra import convert_history_openai, extract_image, extract_json, find_module, get_streaming_extra_setting, install_module, open_website, get_image_path, get_spawn_command, quote_string
from .handler import Handler

class LLMHandler(Handler):
    """Every LLM model handler should extend this class."""
    history = []
    prompts = []
    schema_key = "llm-settings"

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path

    def supports_vision(self) -> bool:
        """ Return if the LLM supports receiving images"""
        return False

    def stream_enabled(self) -> bool:
        """ Return if the LLM supports token streaming"""
        enabled = self.get_setting("streaming")
        if enabled is None:
            return False
        return enabled

    def load_model(self, model):
        """ Load the specified model """
        return True

    def set_history(self, prompts : list[str], history: list[dict[str, str]]):
        """Set the current history and prompts

        Args:
            prompts (list[str]): list of sytem prompts
            window : Application window
        """        
        self.prompts = prompts
        self.history = history

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
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
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
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args : list = []) -> str:
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
 
    def get_suggestions(self, request_prompt:str = "", amount:int=1) -> list[str]:
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
            generated = extract_json(generated)
            try:
                j = json.loads(generated)
            except Exception as _:
                continue
            if type(j) is list:
                for suggestion in j:
                    if type(suggestion) is str:
                        result.append(suggestion)
                        i+=1
                        if i >= amount:
                            break
        return result

    def generate_chat_name(self, request_prompt:str = "") -> str:
        """Generate name of the current chat

        Args:
            request_prompt (str, optional): Extra prompt to generate the name. Defaults to None.

        Returns:
            str: name of the chat
        """
        return self.generate_text(request_prompt, self.history)


class NewelleAPIHandler(LLMHandler):
    key = "newelle"
    url = "https://llm.nyarchlinux.moe"
    api_key = "newelle"
    error_message = """Error calling Newelle API. Please note that Newelle API is **just for demo purposes.**\n\nTo know how to use a more reliable LLM [read our guide to llms](https://github.com/qwersyk/newelle/wiki/User-guide-to-the-available-LLMs). \n\nError: """

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "privacy",
                "title": _("Privacy Policy"),
                "description": _("Open privacy policy website"),
                "type": "button",
                "icon": "internet-symbolic",
                "callback": lambda button: open_website("https://groq.com/privacy-policy/"),
                "default": True,
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True,
            },
        ]

    def supports_vision(self) -> bool:
        return True

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        return self.generate_text_stream(prompt, history, system_prompt)
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args : list = []) -> str:
        import requests
        
        if prompt.startswith("```image") or any(message.get("Message", "").startswith("```image") and message["User"] == "User" for message in history):
            url = self.url + "/vision"
        else:
            url = self.url
        history.append({"User": "User", "Message": prompt})  
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        } 
        data = {
            "model": "llama",
            "messages": convert_history_openai(history, system_prompt, True),
            "stream": True
        }

        try:
            response = requests.post(url + "/chat/completions", headers=headers, json=data, stream=True)
            if response.status_code != 200:
                raise Exception("Rate limit reached or servers down")
            full_message = ""
            prev_message = ""
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "): 
                        if decoded_line == "data: [DONE]":
                            break
                        json_data = json.loads(decoded_line[6:])
                        if "choices" in json_data and len(json_data["choices"]) > 0:
                            delta = json_data["choices"][0]["delta"]
                            if "content" in delta:
                                full_message += delta["content"]
                                args = (full_message.strip(), ) + tuple(extra_args)
                                if len(full_message) - len(prev_message) > 1:
                                    on_update(*args)
                                    prev_message = full_message
            return full_message.strip()
        except Exception as e:
            return self.error_message + " " + str(e)


class G4FHandler(LLMHandler):
    """Common methods for g4f models"""
    key = "g4f"
    version = "0.3.5.8" 
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["g4f"]
    
    def is_installed(self) -> bool:
        if find_module("g4f") is not None:
           from g4f.version import utils       
           if utils.current_version != self.version:
                print(f"Newelle requires g4f=={self.version}, found {utils.current_version}")
                return False
           return True
        return False

    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip") 
        # Remove old versions
        check_output(["bash", "-c", "rm -rf " + os.path.join(pip_path, "*g4f*")])
        install_module("g4f==" + self.version, pip_path)
        print("g4f==" + self.version + " installed")

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True,
            },
        ]

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        return convert_history_openai(history, prompts, False)
    
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        model = self.get_setting("model")
        img = None
        if self.supports_vision():
            img, message = extract_image(prompt)
        else:
            message = prompt
        if img is not None:
            img = get_image_path(img)
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=history,
                image= open(img, "rb") if img is not None else None
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error: {e}"
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        model = self.get_setting("model")
        img = None
        if self.supports_vision():
            img, message = extract_image(prompt)
        else:
            message = prompt
        if img is not None:
            get_image_path(img)
        model = self.get_setting("model")
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=history,
                stream=True,
                image= open(img, "rb") if img is not None else None
            )
            full_message = ""
            prev_message = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    full_message += chunk.choices[0].delta.content
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
            return full_message.strip()
        except Exception as e:
            return f"Error: {e}"

class GPT3AnyHandler(G4FHandler):
    """
    Use any GPT3.5-Turbo providers
    - History is supported by almost all of them
    - System prompts are not well supported, so the prompt is put on top of the message
    """
    key = "GPT3Any"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.client = None 
        if self.is_installed():
            self.init_client()
    
    def init_client(self):
        import g4f 
        from g4f.Provider import RetryProvider
        good_providers = [g4f.Provider.DDG, g4f.Provider.Pizzagpt, g4f.Provider.DarkAI, g4f.Provider.Koala, g4f.Provider.AmigoChat]
        good_nongpt_providers = [g4f.Provider.ReplicateHome,g4f.Provider.RubiksAI, g4f.Provider.TeachAnything, g4f.Provider.Free2GPT, g4f.Provider.DeepInfraChat, g4f.Provider.PerplexityLabs]
        acceptable_providers = [g4f.Provider.Blackbox, g4f.Provider.Upstage, g4f.Provider.Upstage]
        self.client = g4f.client.Client(provider=RetryProvider([RetryProvider(good_providers), RetryProvider(good_nongpt_providers), RetryProvider(acceptable_providers)], shuffle=False))
        self.n = 0
    
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        if self.client is None:
            self.init_client()
        message = prompt
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="",
            messages=history,
        )
        return response.choices[0].message.content

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        if self.client is None:
            self.init_client()
        history = self.convert_history(history, system_prompt)
        message = prompt
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="",
            messages=history,
            stream=True,
        )
        full_message = ""
        prev_message = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                full_message += chunk.choices[0].delta.content
                args = (full_message.strip(), ) + tuple(extra_args)
                if len(full_message) - len(prev_message) > 1:
                    on_update(*args)
                    prev_message = full_message
        return full_message.strip()

    def generate_chat_name(self, request_prompt: str = "") -> str:
        history = ""
        for message in self.history[-4:] if len(self.history) >= 4 else self.history:
            history += message["User"] + ": " + message["Message"] + "\n"
        name = self.generate_text(history + "\n\n" + request_prompt)
        return name

class BingHandler(G4FHandler):
    key = "bing"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        
        self.cookies_path = os.path.join(os.path.dirname(self.path), "models", "har_and_cookies")
        if not os.path.isdir(self.cookies_path):
            os.makedirs(self.cookies_path)
        if self.is_installed(): 
            import g4f
            self.client = g4f.client.Client(provider=g4f.Provider.Bing)        
 
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Model"),
                "description": _("The model to use"),
                "type": "combo",
                "values": self.get_model(),
                "default": "Copilot",
            },
            {
                "key": "cookies",
                "title": _("Enable Cookies"),
                "description": _("Enable cookies to use Bing, add them in the dir in json"),
                "type": "toggle",
                "default": True,
                "folder": self.cookies_path
            }
        ] + super().get_extra_settings()

    def get_model(self):
        if self.is_installed():
            import g4f
            res = tuple()
            for model in g4f.Provider.Bing.models:
                res += ((model, model), )
            return res
        else:
            return (("Copilot", "Copilot"), )

    def load_model(self, model):
        if not self.get_setting("cookies"):
            return True
        from g4f.cookies import set_cookies_dir, read_cookie_files
        set_cookies_dir(self.cookies_path)
        read_cookie_files(self.cookies_path)
        return True

    def supports_vision(self) -> bool:
        return True


class GeminiHandler(LLMHandler):
    key = "gemini"
    
    """
    Official Google Gemini APIs, they support history and system prompts
    """

    default_models = [("gemini-1.5-flash","gemini-1.5-flash"), ("gemini-1.5-flash-8b", "gemini-1.5-flash-8b") , ("gemini-1.0-pro", "gemini-1.0-pro"), ("gemini-1.5-pro","gemini-1.5-pro") ]
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.cache = {}
        if self.get_setting("models", False) is None or len(self.get_setting("models", False)) == 0 or True:
            self.models = self.default_models 
            threading.Thread(target=self.get_models).start()
        else:
            self.models = json.loads(self.get_setting("models", False))

    def get_models(self):
        if self.is_installed():
            try:
                import google.generativeai as genai
                api = self.get_setting("apikey", False)
                if api is None:
                    return
                genai.configure(api_key=api)
                models = genai.list_models()
                result = tuple()
                for model in models:
                    if "generateContent" in model.supported_generation_methods:
                        result += ((model.display_name, model.name,),)
                self.models = result
                self.set_setting("models", json.dumps(result))
                self.settings_update()
            except Exception as e:
                print("Error getting " + self.key + " models: " + str(e))
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["google-generativeai"]

    def supports_vision(self) -> bool:
        return True
    def is_installed(self) -> bool:
        if find_module("google.generativeai") is None:
            return False
        return True

    def get_extra_settings(self) -> list:
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
                "refresh": lambda button: self.get_models(),
                "default": self.models[0][1],
                "values": self.models, 
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
            {
                "key": "safety",
                "title": _("Enable safety settings"),
                "description": _("Enable google safety settings to avoid generating harmful content"),
                "type": "toggle",
                "default": True
            },
            {
                "key": "privacy",
                "title": _("Privacy Policy"),
                "description": _("Open privacy policy website"),
                "type": "button",
                "icon": "internet-symbolic",
                "callback": lambda button: open_website("https://ai.google.dev/gemini-api/terms"),
                "default": True,
            },
        ]

    def __convert_history(self, history: list):
        result = []
        for message in history:
            if message["User"] == "Console":
                result.append({
                    "role": "user",
                    "parts": "Console: " + message["Message"]
                })
            else: 
                img, text = self.get_gemini_image(message["Message"]) 
                result.append({
                    "role": message["User"].lower() if message["User"] == "User" else "model",
                    "parts": message["Message"] if img is None else [img, text]
                })
        return result

    def add_image_to_history(self, history: list, image: object) -> list:
        history.append({
            "role": "user",
            "parts": [image]
        })
        return history
    
    def get_gemini_image(self, message: str) -> tuple[object, str]:
        from google.generativeai import upload_file
        img = None
        image, text = extract_image(message)
        if image is not None:
            if image.startswith("data:image/jpeg;base64,"):
                image = image[len("data:image/jpeg;base64,"):]
                raw_data = base64.b64decode(image)
                with open("/tmp/image.jpg", "wb") as f:
                    f.write(raw_data)
                image_path = "/tmp/image.jpg"
            else:
                image_path = image
            if image in self.cache:
                img = self.cache[image]
            else:
                img = upload_file(image_path)
                self.cache[image] = img
        else:
            text = message
        return img, text

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        import google.generativeai as genai
        
        from google.generativeai.protos import HarmCategory
        from google.generativeai.types import HarmBlockThreshold
        if self.get_setting("safety"):
            safety = None
        else:
            safety = { 
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            }
 
        genai.configure(api_key=self.get_setting("apikey"))
        instructions = "\n"+"\n".join(system_prompt)
        if instructions == "":
            instructions=None
        model = genai.GenerativeModel(self.get_setting("model"), system_instruction=instructions, safety_settings=safety)
        converted_history = self.__convert_history(history)
        try:
            img, txt = self.get_gemini_image(prompt)
            if img is not None:
                converted_history = self.add_image_to_history(converted_history, img)
            chat = model.start_chat(
                history=converted_history
            )
            response = chat.send_message(txt)
            return response.text
        except Exception as e:
            return "Message blocked: " + str(e)

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None , extra_args: list = []) -> str:
        import google.generativeai as genai
        from google.generativeai.protos import HarmCategory
        from google.generativeai.types import HarmBlockThreshold
        
        if self.get_setting("safety"):
            safety = None
        else:
            safety = { 
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            }
 
        genai.configure(api_key=self.get_setting("apikey"))
        instructions = "\n".join(system_prompt)
        if instructions == "":
            instructions=None
        model = genai.GenerativeModel(self.get_setting("model"), system_instruction=instructions, safety_settings=safety)
        converted_history = self.__convert_history(history) 
        try: 
            img, txt = self.get_gemini_image(prompt)
            if img is not None:
                converted_history = self.add_image_to_history(converted_history, img)
            chat = model.start_chat(history=converted_history)
            response = chat.send_message(txt, stream=True)
            full_message = ""
            for chunk in response:
                full_message += chunk.text
                args = (full_message.strip(), ) + tuple(extra_args)
                on_update(*args)
            return full_message.strip()
        except Exception as e:
            return "Message blocked: " + str(e)

class CustomLLMHandler(LLMHandler):
    key = "custom_command"
    
    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self):
        return [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
           
            {
                "key": "command",
                "title": _("Command to execute to get bot output"),
                "description": _("Command to execute to get bot response, {0} will be replaced with a JSON file containing the chat, {1} with the system prompt"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "suggestion",
                "title": _("Command to execute to get bot's suggestions"),
                "description": _("Command to execute to get chat suggestions, {0} will be replaced with a JSON file containing the chat, {1} with the extra prompts, {2} with the numer of suggestions to generate. Must return a JSON array containing the suggestions as strings"),
                "type": "entry",
                "default": ""
            },

        ]

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        command = self.get_setting("command")
        history.append({"User": "User", "Message": prompt})
        command = command.replace("{0}", quote_string(json.dumps(history)))
        command = command.replace("{1}", quote_string(json.dumps(system_prompt)))
        out = check_output(get_spawn_command() + ["bash", "-c", command])
        return out.decode("utf-8")
    
    def get_suggestions(self, request_prompt: str = "", amount: int = 1) -> list[str]:
        command = self.get_setting("suggestion")
        if command == "":
            return []
        self.history.append({"User": "User", "Message": request_prompt})
        command = command.replace("{0}", quote_string(json.dumps(self.history)))
        command = command.replace("{1}", quote_string(json.dumps(self.prompts)))
        command = command.replace("{2}", str(amount))
        out = check_output(get_spawn_command() + ["bash", "-c", command])
        return json.loads(out.decode("utf-8"))  
 
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        command = self.get_setting("command")
        history.append({"User": "User", "Message": prompt})
        command = command.replace("{0}", quote_string(json.dumps(history)))
        command = command.replace("{1}", quote_string(json.dumps(system_prompt)))
        process = Popen(get_spawn_command() + ["bash", "-c", command], stdout=PIPE)        
        full_message = ""
        prev_message = ""
        while True:
            if process.stdout is None:
                break
            chunk = process.stdout.readline()
            if not chunk:
                break
            full_message += chunk.decode("utf-8")
            args = (full_message.strip(), ) + tuple(extra_args)
            if len(full_message) - len(prev_message) > 1:
                on_update(*args)
                prev_message = full_message

        process.wait()
        return full_message.strip()

class OllamaHandler(LLMHandler):
    key = "ollama"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["ollama"]

    def supports_vision(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [ 
            {
                "key": "endpoint",
                "title": _("API Endpoint"),
                "description": _("API base url, change this to use interference APIs"),
                "type": "entry",
                "default": "http://localhost:11434"
            },
            {
                "key": "model",
                "title": _("Ollama Model"),
                "description": _("Name of the Ollama Model"),
                "type": "entry",
                "default": "llama3.1:8b"
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
        ]

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        result = []
        result.append({"role": "system", "content": "\n".join(prompts)})
        for message in history:
            if message["User"] == "Console":
                result.append({
                    "role": "user",
                    "content": "Console: " + message["Message"]
                })
            else:
                image, text = extract_image(message["Message"])
                
                msg = {
                    "role": message["User"].lower() if message["User"] in {"Assistant", "User"} else "system",
                    "content": text
                }
                if message["User"] == "User" and image is not None:
                    if image.startswith("data:image/png;base64,"):
                        image = image[len("data:image/png;base64,"):]
                    msg["images"] = [image]
                result.append(msg)
        return result
    
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        from ollama import Client
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history, system_prompt)

        client = Client(
            host=self.get_setting("endpoint")
        )
        try:
            response = client.chat(
                model=self.get_setting("model"),
                messages=messages,
            )
            return response["message"]["content"]
        except Exception as e:
            return str(e)
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        from ollama import Client
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history, system_prompt)
        client = Client(
            host=self.get_setting("endpoint")
        )
        try:
            response = client.chat(
                model=self.get_setting("model"),
                messages=messages,
                stream=True
            )
            full_message = ""
            prev_message = ""
            for chunk in response:
                full_message += chunk["message"]["content"]
                args = (full_message.strip(), ) + tuple(extra_args)
                if len(full_message) - len(prev_message) > 1:
                    on_update(*args)
                    prev_message = full_message
            return full_message.strip()
        except Exception as e:
            return str(e)


class OpenAIHandler(LLMHandler):
    key = "openai"
    default_models = (("gpt-3.5-turbo", "gpt-3.5-turbo"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        if self.get_setting("models", False) is None:
            self.models = self.default_models 
            threading.Thread(target=self.get_models).start()
        else:
            self.models = json.loads(self.get_setting("models", False))

    def get_models(self):
        if self.is_installed():
            try:
                import openai
                api = self.get_setting("api", False)
                if api is None:
                    return
                client = openai.Client(api_key=api, base_url=self.get_setting("endpoint"))
                models = client.models.list()
                result = tuple()
                for model in models:
                    result += ((model.id, model.id,), )
                self.models = result
                self.set_setting("models", json.dumps(result))
                self.settings_update()
            except Exception as e:
                print("Error getting " + self.key + " models: " + str(e))
            
    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    def supports_vision(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("OpenAI", True, True, True, True, True, "https://openai.com/policies/row-privacy-policy/", None)

    def build_extra_settings(self, provider_name: str, has_api_key: bool, has_stream_settings: bool, endpoint_change: bool, allow_advanced_params: bool, supports_automatic_models: bool, privacy_notice_url : str | None, model_list_url: str | None, default_advanced_params: bool = False, default_automatic_models: bool = False) -> list:
        """Helper to build the list of extra settings for OpenAI Handlers

        Args:
            provider_name: name of the provider, it is stated in model settings 
            has_api_key: if to show the api key setting
            has_stream_settings: if to show the message streaming setting
            endpoint_change: if to allow the endpoint change 
            allow_advanced_params: if to allow advanced parameters like temperature ... 
            supports_automatic_models: if it supports automatic model fetching 
            privacy_notice_url: the url of the privacy policy, None if not stated
            model_list_url: human accessible page that lists the available models

        Returns:
            list containing the extra settings
        """
        api_settings = [ 
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for " + provider_name),
                "type": "entry",
                "default": ""
            },
        ]
        endpoint_settings = [
            {
                "key": "endpoint",
                "title": _("API Endpoint"),
                "description": _("API base url, change this to use interference APIs"),
                "type": "entry",
                "default": "https://api.openai.com/v1/" 
            },

        ]
        custom_model = [
            {
                "key": "custom_model",
                "title": _("Input a custom model"),
                "description": _("Input a custom model name instead taking it from the list"),
                "type": "toggle",
                "default": not default_automatic_models,
                "update_settings": True
            },
        ]
        advanced_param_toggle = [
            {
                "key": "advanced_params",
                "title": _("Advanced Parameters"),
                "description": _("Include parameters like Max Tokens, Top-P, Temperature, etc."),
                "type": "toggle",
                "default": default_advanced_params,
                "update_settings": True
            }
        ]
        models_settings = [ 
            {
                "key": "model",
                "title": _(provider_name + " Model"),
                "description": _("Name of the LLM Model to use"),
                "type": "entry",
                "default": self.models[0][0],
            },
        ]
        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        automatic_models_settings = [
            {
                "key": "model",
                "title": _(provider_name + " Model"),
                "description": _(f"Name of the {provider_name} Model"),
                "type": "combo",
                "refresh": lambda button: self.get_models(),
                "values": self.models,
                "default": self.models[0][0]
            },
        ]

        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        
        advanced_settings = [
            {
                "key": "max-tokens",
                "title": _("Max Tokens"),
                "description": _("Max tokens of the generated text"),
                "website": "https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them",
                "type": "range",
                "min": 3,
                "max": 8000,
                "default": 4000,
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
                "description": _("Positive values penalize new tokens based on whether they appear in the text so far, increasing the model's likelihood to talk about new topics."),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-frequency_penalty",
                "type": "range",
                "min": -2,
                "max": 2,
                "default": 0,
                "round-digits": 1,
            },
        ]
        
        privacy_notice = [{
                "key": "privacy",
                "title": _("Privacy Policy"),
                "description": _("Open privacy policy website"),
                "type": "button",
                "icon": "internet-symbolic",
                "callback": lambda button: open_website(privacy_notice_url),
                "default": True,
            }
        ]
        settings = []
        if has_api_key:
            settings += (api_settings)
        if endpoint_change:
            settings += (endpoint_settings)
        if supports_automatic_models:
            settings += (custom_model)
            custom = self.get_setting("custom_model", False)
            if (custom is None and not default_automatic_models) or custom:
                settings += models_settings
            else:
                settings += automatic_models_settings
        if has_stream_settings:
            settings.append(get_streaming_extra_setting())
        if allow_advanced_params:
            settings += advanced_param_toggle
            advanced = self.get_setting("advanced_params", False)
            if advanced or (advanced is None and default_advanced_params):
                settings += advanced_settings
        if privacy_notice_url is not None:
            settings += privacy_notice
        return settings

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        return convert_history_openai(history, prompts, self.supports_vision())

    def get_advanced_params(self):
        from openai import NOT_GIVEN
        advanced_params = self.get_setting("advanced_params")
        if not advanced_params:
            return NOT_GIVEN, NOT_GIVEN, NOT_GIVEN, NOT_GIVEN, NOT_GIVEN
        top_p = self.get_setting("top-p")
        temperature = self.get_setting("temperature")
        max_tokens = int(self.get_setting("max-tokens"))
        presence_penalty = self.get_setting("presence-penalty")
        frequency_penalty = self.get_setting("frequency-penalty")
        return top_p, temperature, max_tokens, presence_penalty, frequency_penalty 

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        from openai import OpenAI
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history, system_prompt)
        api = self.get_setting("api")
        if api == "":
            api = "nokey"
        
        client = OpenAI(
            api_key=api,
            base_url=self.get_setting("endpoint")
        )
        top_p, temperature, max_tokens, presence_penalty, frequency_penalty = self.get_advanced_params()
        try:
            response = client.chat.completions.create(
                model=self.get_setting("model"),
                messages=messages,
                top_p=top_p,
                max_tokens=max_tokens,
                temperature=temperature,
                presence_penalty=presence_penalty,
                frequency_penalty=frequency_penalty
            )
            return response.choices[0].message.content
        except Exception as e:
            return str(e)
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        from openai import OpenAI
        history.append({"User": "User", "Message": prompt})
        messages = self.convert_history(history, system_prompt)
        print([message["role"] for message in messages])
        api = self.get_setting("api")
        if api == "":
            api = "nokey"
        client = OpenAI(
            api_key=api,
            base_url=self.get_setting("endpoint")
        )
        top_p, temperature, max_tokens, presence_penalty, frequency_penalty = self.get_advanced_params()
        try:
            response = client.chat.completions.create(
                model=self.get_setting("model"),
                messages=messages,
                top_p=top_p,
                max_tokens=max_tokens,
                temperature=temperature,
                presence_penalty=presence_penalty,
                frequency_penalty=frequency_penalty, 
                stream=True
            )
            full_message = ""
            prev_message = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    full_message += chunk.choices[0].delta.content
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
            return full_message.strip()
        except Exception as e:
            return str(e)

class MistralHandler(OpenAIHandler):
    key = "mistral"
    default_models = (("open-mixtral-8x7b", "open-mixtral-8x7b"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.mistral.ai/v1/")
        self.set_setting("advanced_params", False)

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Mistral", True, True, False, False, True, None, "https://docs.mistral.ai/getting-started/models/models_overview/", False, True)

class GroqHandler(OpenAIHandler):
    key = "groq"
    default_models = (("llama-3.3-70B-versatile", "llama-3.3-70B-versatile" ), ) 
    def supports_vision(self) -> bool:
        return "vision" in self.get_setting("model")

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.groq.com/openai/v1/")

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Groq", True, True, False, False, True, "https://groq.com/privacy-policy/", "https://console.groq.com/docs/models", False, True)

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        # Remove system prompt if history contains image prompt
        # since it is not supported by groq
        h = super().convert_history(history, prompts)
        contains_image = False
        for message in h:
            if type(message["content"]) is list:
                if any(content["type"] == "image_url" for content in message["content"]):
                    contains_image = True
                    break
        if contains_image and (prompts is None or len(prompts) > 0):
            h.pop(0)
        return h

class OpenRouterHandler(OpenAIHandler):
    key = "openrouter"
    default_models = (("meta-llama/llama-3.1-70b-instruct:free", "meta-llama/llama-3.1-70b-instruct:free"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://openrouter.ai/api/v1/")

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("OpenRouter", True, True, False, False, True, "https://openrouter.ai/privacy", "https://openrouter.ai/docs/models", False, True)


class GPT4AllHandler(LLMHandler):
    key = "local"

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
    
    def get_extra_settings(self) -> list:
        models = self.get_custom_model_list()
        default = models[0][1] if len(models) > 0 else ""
        return [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True,
            },
            {
                "key": "custom_model",
                "title": _("Custom gguf model file"),
                "description": _("Add a gguf file in the specified folder and then close and reopen the settings to update"),
                "type": "combo",
                "default": default,
                "values": models,
                "folder": self.model_folder,
            }
        ]
    def get_custom_model_list(self): 
        file_list = tuple()
        for root, _, files in os.walk(self.model_folder):
            for file in files: 
                if file.endswith('.gguf'):
                    file_name = file.rstrip('.gguf')
                    relative_path = os.path.relpath(os.path.join(root, file), self.model_folder)
                    file_list += ((file_name, relative_path), )
        return file_list

    def model_available(self, model:str) -> bool:
        """ Returns if a model has already been downloaded
        """
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


