from abc import abstractmethod
from ...utility.pip import find_module
from ..handler import Handler


class STTHandler(Handler):
    """Every STT Handler should extend this class"""
    key = ""
    schema_key = "stt-settings"
    
    def is_installed(self) -> bool:
        """If the handler is installed"""
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                return False
        return True

    @abstractmethod
    def recognize_file(self, path) -> str | None:
        """Recognize a given audio file"""
        pass


