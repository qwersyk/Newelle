from abc import abstractmethod
from ...utility.pip import find_module
from ..handler import Handler


class STTHandler(Handler):
    """Every STT Handler should extend this class"""
    key = ""
    schema_key = "stt-settings"

    def set_secondary_settings(self, secondary: bool):
        """Set the secondary settings for the STT"""
        if secondary:
            self.schema_key = "stt-secondary-settings"
        else:
            self.schema_key = "stt-settings"

    def is_secondary(self) -> bool:
        """Return if the STT is a secondary one"""
        return self.schema_key == "stt-secondary-settings"

    def is_installed(self) -> bool:
        """If the handler is installed"""
        for module in self.get_extra_requirements():
            if module == "speechrecognition":
                module = "speech_recognition"
            if find_module(module) is None:
                return False
        return True

    @abstractmethod
    def recognize_file(self, path) -> str | None:
        """Recognize a given audio file"""
        pass


