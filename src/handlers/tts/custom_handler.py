from subprocess import check_output
from ...handlers import ExtraSettings
from .tts import TTSHandler
from ...utility.system import get_spawn_command 


class CustomTTSHandler(TTSHandler):
    key = "custom_command"

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("command", _("Command to execute"), _("{0} will be replaced with the file fullpath, {1} with the text"), "")
        ]

    def is_installed(self):
        return True

    def save_audio(self, message, file):
        command = self.get_setting("command")
        if command is not None:
            print(["bash", "-c", command.replace("{0}", file).replace("{1}", message)])
            check_output(get_spawn_command() + ["bash", "-c", command.replace("{0}", file).replace("{1}", message)])
        return 
