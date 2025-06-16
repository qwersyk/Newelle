import threading 
import json
from typing import Any, Callable 


from .llm import LLMHandler
from ...utility.system import open_website
from ...utility import convert_history_openai, get_streaming_extra_setting
from ...handlers import ExtraSettings, ErrorSeverity

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

    def get_models_list(self):
        return self.models

    def get_models(self, manual=False):
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
                if manual:
                    self.throw("Error getting " + self.key + " models: " + str(e), ErrorSeverity.WARNING)
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
            ExtraSettings.EntrySetting("api", _("API Key"), _("API Key for " + provider_name), "", password=True),
        ]
        endpoint_settings = [
            ExtraSettings.EntrySetting("endpoint", _("API Endpoint"), _("API base url, change this to use interference APIs"), "https://api.openai.com/v1/"),
        ]
        custom_model = [
            ExtraSettings.ToggleSetting("custom_model", _("Use Custom Model"), _("Use a custom model"), False, update_settings=True)
        ]
        advanced_param_toggle = [
            ExtraSettings.ToggleSetting("advanced_params", _("Advanced Parameters"), _("Include parameters like Max Tokens, Top-P, Temperature, etc."), default_advanced_params, update_settings=True)
        ]
        models_settings = [ 
            ExtraSettings.EntrySetting("model", _("Model"), _("Name of the LLM Model to use"), self.models[0][0]),
        ]
        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        automatic_models_settings = [
            ExtraSettings.ComboSetting(
                    "model",
                    _(provider_name + " Model"),
                    _(f"Name of the {provider_name} Model"),
                    self.models,
                    self.models[0][0],
                    refresh=lambda button: self.get_models(),
                )
        ]

        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        
        advanced_settings = [
            ExtraSettings.ScaleSetting("max-tokens", _("max Tokens"), _("Max tokens of the generated text"), 4000, 3, 8000, 0),
            ExtraSettings.ScaleSetting("top-p", _("Top-P"), _("An alternative to sampling with temperature, called nucleus sampling"), 1, 0, 1, 2),
            ExtraSettings.ScaleSetting("temperature", _("Temperature"), _("What sampling temperature to use. Higher values will make the output more random"), 1, 0, 2, 1),
            ExtraSettings.ScaleSetting("frequency-penalty", _("Frequency Penalty"), _("Number between -2.0 and 2.0. Positive values decrease the model's likelihood to repeat the same line verbatim"), 0, -2, 2, 0),
            ExtraSettings.ScaleSetting("presence-penalty", _("Presence Penalty"), _("Number between -2.0 and 2.0. Positive values decrease the model's likelihood to talk about new topics"), 0, -2, 2, 0),
        ]
        
        privacy_notice = [
            ExtraSettings.ButtonSetting(
                    "privacy", _("Privacy Policy"), _("Open privacy policy website"),
                    lambda button: open_website(privacy_notice_url), None, "internet-symbolic"
                )
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
                stream=True,
                extra_headers=self.get_extra_headers(),
                extra_body=self.get_extra_body(),
            )
            full_message = ""
            prev_message = ""
            is_reasoning = False
            for chunk in response:
                if len(chunk.choices) == 0:
                    continue
                if chunk.choices[0].delta.content:
                    if is_reasoning:
                        full_message += "</think>\n"
                        is_reasoning = False
                    full_message += chunk.choices[0].delta.content
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
                elif hasattr(chunk.choices[0].delta, "reasoning") and chunk.choices[0].delta.reasoning is not None:
                    if not is_reasoning:
                        full_message += "<think>"
                    is_reasoning = True
                    full_message += chunk.choices[0].delta.reasoning
                    if len(full_message) - len(prev_message) > 1:
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
                        prev_message = full_message
            return full_message.strip()
        except Exception as e:
            raise e

    def get_extra_body(self):
        return {}

    def get_extra_headers(self):
        return {}
