import os, json
from .extra import find_module, install_module
from typing import Any

class Handler():
    """Handler for a module"""
    key = ""
    schema_key = ""

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return False

    def get_extra_settings(self) -> list:
        """
        Extra settings format:
            Required parameters:
            - title: small title for the setting 
            - description: description for the setting
            - default: default value for the setting
            - type: What type of row to create, possible rows:
                - entry: input text 
                - toggle: bool
                - combo: for multiple choice
                    - values: list of touples of possible values (display_value, actual_value)
                - range: for number input with a slider 
                    - min: minimum value
                    - max: maximum value 
                    - round: how many digits to round 
            Optional parameters:
                - folder: add a button that opens a folder with the specified path
                - website: add a button that opens a website with the specified path
                - update_settings (bool) if reload the settings in the settings page for the specified handler after that setting change
        """
        return []

    @staticmethod
    def get_extra_requirements() -> list:
        """The list of extra pip requirements needed by the handler"""
        return []

    def install(self):
        """Install the handler requirements"""
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        for module in self.get_extra_requirements():
            install_module(module, pip_path)

    def is_installed(self) -> bool:
        """Return if the handler is installed"""
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                return False
        return True

    def get_setting(self, key: str) -> Any:
        """Get a setting from the given key

        Args:
            key (str): key of the setting

        Returns:
            object: value of the setting
        """        
        j = json.loads(self.settings.get_string(self.schema_key))
        if self.key not in j or key not in j[self.key]:
            return self.get_default_setting(key)
        return j[self.key][key]

    def set_setting(self, key : str, value):
        """Set a setting from key and value for this handler

        Args:
            key (str): key of the setting
            value (object): value of the setting
        """        
        j = json.loads(self.settings.get_string(self.schema_key))
        if self.key not in j:
            j[self.key] = {}
        j[self.key][key] = value
        self.settings.set_string(self.schema_key, json.dumps(j))

    def get_default_setting(self, key) -> object:
        """Get the default setting from a certain key

        Args:
            key (str): key of the setting

        Returns:
            object: setting value
        """
        extra_settings = self.get_extra_settings()
        for s in extra_settings:
            if s["key"] == key:
                return s["default"]
        return None


