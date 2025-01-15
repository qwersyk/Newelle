from subprocess import check_output
from .stt import STTHandler
from ...utility.system import get_spawn_command

class CustomSRHandler(STTHandler):
    
    key = "custom_command"

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "command",
                "title": _("Command to execute"),
                "description": _("{0} will be replaced with the model fullpath"),
                "type": "entry",
                "default": ""
            },
        ]

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def recognize_file(self, path):
        command = self.get_setting("command")
        if command is not None:
            res = check_output(get_spawn_command() + ["bash", "-c", command.replace("{0}", path)]).decode("utf-8")
            return str(res)
        return None

