import json
from typing import Any, Callable

from .llm import LLMHandler
from ...utility.system import open_website
from ...utility import convert_history_openai 

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
        elif prompt.startswith("/chatname"):
            prompt = prompt.replace("/chatname", "")
            url = self.url + "/small"
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
            raise Exception(self.error_message + " " + str(e))


    def generate_chat_name(self, request_prompt:str = "") -> str | None:
        """Generate name of the current chat

        Args:
            request_prompt (str, optional): Extra prompt to generate the name. Defaults to None.

        Returns:
            str: name of the chat
        """
        request_prompt = "/chatname" + request_prompt
        return super().generate_chat_name(request_prompt)
