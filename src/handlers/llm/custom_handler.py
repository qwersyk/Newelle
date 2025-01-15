import json
from subprocess import PIPE, Popen, check_output 
from typing import Any, Callable

from .llm import LLMHandler
from ...utility.strings import quote_string
from ...utility.system import get_spawn_command

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

