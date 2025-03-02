import json 
import threading
import base64
from typing import Callable, Any

from .llm import LLMHandler
from ...utility.media import extract_image, extract_video, extract_file
from ...utility.pip import find_module
from ...utility.system import open_website
from ...handlers import ExtraSettings

class GeminiHandler(LLMHandler):
    key = "gemini"
    
    """
    Official Google Gemini APIs, they support history and system prompts
    """

    default_models = [("gemini-1.5-flash","gemini-1.5-flash"), ("gemini-1.5-flash-8b", "gemini-1.5-flash-8b") , ("gemini-1.0-pro", "gemini-1.0-pro"), ("gemini-1.5-pro","gemini-1.5-pro") ]
    
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

    def fix_models_format(self):
        m = tuple()
        for model in self.models:
            m += ((model[0], model[1]),)
        self.models = m

    def get_supported_files(self) -> list[str]:
        return ["*"]

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
    
    def supports_video_vision(self) -> bool:
        return True

    def is_installed(self) -> bool:
        if find_module("google.generativeai") is None:
            return False
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("apikey", _("API Key (required)"), _("API key got from ai.google.dev"), ""), 
            ExtraSettings.ComboSetting(
                "model",
                _("Model"),
                _("AI Model to use"),
                self.models,
                self.models[0][1],
                refresh=lambda button: self.get_models(),
            ),
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
            )
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
            raise Exception("Message blocked: " + str(e))

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
            raise Exception("Message blocked: " + str(e))

