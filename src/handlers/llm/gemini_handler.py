import json 
import threading
import base64
from typing import Callable, Any
import os 
import uuid 
import time 

from .llm import LLMHandler
from ...utility.media import extract_image, extract_video, extract_file
from ...utility.pip import find_module
from ...utility.system import open_website
from ...handlers import ExtraSettings, ErrorSeverity

class GeminiHandler(LLMHandler):
    key = "gemini"
    
    """
    Official Google Gemini APIs, they support history and system prompts
    """

    default_models = [("Gemini 2.0 Flash","gemini-2.0-flash"), ("Gemini 2.0 Flash Lite", "gemini-2.0-flash-lite")]
    
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.cache = {}
        if self.get_setting("models", False) is None or len(self.get_setting("models", False)) == 0:
            self.models = self.default_models 
            self.fix_models_format()
            threading.Thread(target=self.get_models).start()
        else:
            self.models = json.loads(self.get_setting("models", False))
            self.fix_models_format()

    def get_models_list(self):
        return self.models

    def stream_enabled(self) -> bool:
        return True
    
    def fix_models_format(self):
        m = tuple()
        for model in self.models:
            m += ((model[0], model[1]),)
        self.models = m

    def get_supported_files(self) -> list[str]:
        return ["*"]

    def get_models(self, manual=False):
        if self.is_installed():
            try:
                from google import genai
                api = self.get_setting("apikey", False)
                if api is None:
                    return
                client = genai.Client(api_key=api)
                
                models = client.models.list()
                result = tuple()
                for model in models:
                    print(model.supported_actions)
                    print(model)
                    if "embedding" in model.display_name.lower() or "legacy" in model.display_name.lower():
                        continue
                    result += ((model.display_name, model.name,),)
                self.models = result
                self.set_setting("models", json.dumps(result))
                self.settings_update()
            except Exception as e:
                if manual:
                    self.throw("Error getting " + self.key + " models: " + str(e), ErrorSeverity.WARNING)
                print("Error getting " + self.key + " models: " + str(e))
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["google-genai"]

    def supports_vision(self) -> bool:
        return True
    
    def supports_video_vision(self) -> bool:
        return True

    def is_installed(self) -> bool:
        try:
            from google import genai
        except Exception as e:
            return False
        return True

    def get_extra_settings(self) -> list:
        r = [
            ExtraSettings.EntrySetting("apikey", _("API Key (required)"), _("API key got from ai.google.dev"), "", password=True), 
            ExtraSettings.ComboSetting(
                "model",
                _("Model"),
                _("AI Model to use"),
                self.models,
                self.models[0][1],
                refresh=lambda button: self.get_models(),
            ),
            ExtraSettings.ToggleSetting("system_prompt", _("Enable System Prompt"), _("Some models don't support system prompt (or developers instructions), disable it if you get errors about it"), True, update_settings=True),
        ]

        if not (self.get_setting("system_prompt", False) is None or self.get_setting("system_prompt", False)):
            r+= [ExtraSettings.ToggleSetting("force_system_prompt", _("Inject system prompt"), _("Even if the model doesn't support system prompts, put the prompts on top of the user message"), True)]
        r += [
            ExtraSettings.NestedSetting("think", _("Thinking Settings"), _("Settings about thinking models"), [
                ExtraSettings.ToggleSetting("thinking", _("Enable Thinking"), _("Show thinking, disable it if your model does not support it"), True),
                ExtraSettings.ToggleSetting("enable_thinking_budget", _("Enable Thinking Budget"), _("If to enable thinking budget"), False),
                ExtraSettings.ScaleSetting("thinking_budget", _("Thinking Budget"), _("How much time to spend thinking"), 8000, 0, 24576, 0),
            ])
        ]
        r += [
            ExtraSettings.ToggleSetting("img_output", _("Image Output"), _("Enable image output, only supported by gemini-2.0-flash-exp"), False), 
            ExtraSettings.ToggleSetting(
                "streaming",
                _("Message Streaming"),
                _("Gradually stream message output"),
                True
            ),
            ExtraSettings.ToggleSetting(
                "safety",
                _("Enable safety settings"),
                _("Enable google safety settings to avoid generating harmful content"),
                True
            ),
            ExtraSettings.ButtonSetting(
                "privacy",
                _("Privacy Policy"),
                _("Open privacy policy website"),
                lambda button: open_website("https://ai.google.dev/gemini-api/terms"), None, "internet-symbolic"
            ),
            ExtraSettings.ToggleSetting("advanced_params", _("Advanced Parameters"), _("Enable advanced parameters"), False, update_settings=True),
        ]
        if self.get_setting("advanced_params", False):
            r += [
                ExtraSettings.ScaleSetting("temperature", "Temperature", "Creativity allowed in the responses", 1, 0, 2, 2),
                ExtraSettings.ScaleSetting("top_p", "Top P", "Probability of the top tokens to keep", 1, 0, 1, 2),
                ExtraSettings.ScaleSetting("max_tokens", "Max Tokens", "Maximum number of tokens to generate", 8192, 0, 65536, 0),
            ]
        return r
    def __convert_history(self, history: list):
        from google.genai import types
        result = []
        for message in history:
            if message["User"] == "Console":
                result.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text="Console: " + message["Message"])
                        ]
                    )
                )
            else: 
                img, text = self.get_gemini_image(message["Message"]) 
                result.append(
                    types.Content(
                        role="user" if message["User"] == "User" else "model",
                        parts=[types.Part.from_text(text=text)] if img is None else [types.Part.from_text(text=text), types.Part.from_uri(file_uri=img.uri, mime_type=img.mime_type)]
                    )
                )
        return result

    def add_image_to_history(self, history: list, image: object) -> list:
        history.append({
            "role": "user",
            "parts": [image]
        })
        return history
    
    def get_gemini_image(self, message: str) -> tuple[object, str]:
        from google.genai import Client 
        client = Client(api_key=self.get_setting("apikey"))
        img = None
        image, text = extract_image(message)
        if image is None:
            image, text = extract_video(message)
            if image is None:
                image, text = extract_file(message)
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
                img = client.files.upload(file=image_path)
                
                self.cache[image] = img
        else:
            text = message
        self.wait_for_video(img, client)
        return img, text
   
    def wait_for_video(self, video_file, client):
        if video_file is None:
            return
        while video_file.state == "PROCESSING":
            time.sleep(2)
            video_file = client.files.get(name=video_file.name)

        if video_file.state == "FAILED":
          raise ValueError(video_file.state)
    
    @staticmethod
    def save_binary_file(file_name, data):
        f = open(file_name, "wb")
        f.write(data)
        f.close()
   
    def generate_file_name(self, extension):
        image_dir = os.path.join(self.path, "images")
        if not os.path.exists(image_dir):
            os.makedirs(image_dir)
        return os.path.join(image_dir, str(uuid.uuid4()) + extension)

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        return self.generate_text_stream(prompt, history, system_prompt) 
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None , extra_args: list = []) -> str:
        from google import genai
        from google.genai.types import HarmCategory, HarmBlockThreshold, GenerateContentConfig, Part 
        from google.genai import types
        if self.get_setting("safety"):
            safety = None
        else:
            safety = [
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY, threshold=HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
            ] 
        client = genai.Client(api_key=self.get_setting("apikey"))
        instructions = "\n".join(system_prompt)
        append_instructions = None
        if not self.get_setting("system_prompt"): 
            instructions = None
            if self.get_setting("force_system_prompt"):
                append_instructions = "\n".join([p.replace("```", "\\\\\\```") for p in system_prompt])
        if not self.get_setting("advanced_params"):
            generate_content_config = GenerateContentConfig( system_instruction=instructions, 
                                                            safety_settings=safety, response_modalities=["text"] + ["image"] if self.get_setting("img_output") else ["text"])
        else:
            generate_content_config = GenerateContentConfig(system_instruction=instructions if not self.get_setting("img_output") else None, safety_settings=safety, response_modalities=["text"] + ["image"] if self.get_setting("img_output") else ["text"],
                top_p=self.get_setting("top-p"),
                temperature=self.get_setting("temperature"),
                frequency_penalty=self.get_setting("frequency-penalty"),
                max_output_tokens=int(self.get_setting("max_tokens")),
            )
        if self.get_setting("thinking"):
            generate_content_config.thinking_config = types.ThinkingConfig(
                  include_thoughts=True
                )
        if self.get_setting("enable_thinking_budget"):
            generate_content_config.thinking_config = types.ThinkingConfig(
                thinking_budget=int(self.get_setting("thinking_budget")),
                include_thoughts=self.get_setting("thinking")
            )
        history.append({"User": "User", "Message": prompt}) 
        if append_instructions is not None:
            history.insert(0,{"User": "User", "Message": append_instructions})
        converted_history = self.__convert_history(history)
        try: 
            response = client.models.generate_content_stream(
                contents=converted_history,
                config=generate_content_config,
                model=self.get_setting("model"),
            )
            full_message = ""
            thoughts = ""
            thinking = False
            for chunk in response:
                if chunk.candidates[0].content.parts is None:
                    continue
                for part in chunk.candidates[0].content.parts:
                    if part.inline_data:
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
                        file_name = self.generate_file_name(".png") 
                        self.save_binary_file(
                            file_name, part.inline_data.data
                        )
                        full_message += "\n```image\n" + file_name + "\n```\n"
                    elif not part.text:
                        continue
                    elif part.thought:
                        thoughts += part.text
                        if not thinking:
                            full_message += "<think> " + thoughts
                        thinking = True
                        full_message += part.text
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
                    else:
                        if thinking:
                            thinking = False 
                            full_message += "</think>\n"
                        full_message += part.text
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
            return full_message.strip()
        except Exception as e:
            raise Exception("Message blocked: " + str(e))

