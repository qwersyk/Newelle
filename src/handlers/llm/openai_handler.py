import threading 
import json
from typing import Any, Callable 


from .llm import LLMHandler
from ...utility.system import open_website
from ...utility import convert_history_openai, get_streaming_extra_setting


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
            if not hasattr(response, "choices") or response.choices is None or len(response.choices) == 0 or response.choices[0].message.content is None:
                raise Exception(str(response))
            return response.choices[0].message.content
        except Exception as e:
            raise e
    
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
            raise e

