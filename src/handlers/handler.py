import os
import json
from ..utility.pip import find_module, install_module
from typing import Any
from enum import Enum


class SettingsCache:
    _instances = {}

    @staticmethod
    def get_instance(settings):
        if settings not in SettingsCache._instances:
            SettingsCache._instances[settings] = SettingsCache(settings)
        return SettingsCache._instances[settings]

    def __init__(self, settings):
        self.settings = settings
        self.cache = {}
        self._updating = False
        if hasattr(self.settings, 'connect'):
            self.settings.connect("changed", self.on_changed)
    
    def on_changed(self, settings, key):
        if self._updating:
            return
        if key in self.cache:
            try:
                self.cache[key] = json.loads(self.settings.get_string(key))
            except Exception as e:
                print(f"Error reloading settings: {e}")

    def get_json(self, key):
        if key not in self.cache:
            self.cache[key] = json.loads(self.settings.get_string(key))
        return self.cache[key]
    
    def set_json(self, key, value):
        self.cache[key] = value
        self._updating = True
        try:
            self.settings.set_string(key, json.dumps(value))
        finally:
            self._updating = False


class ErrorSeverity(Enum):
    """Severity of the error"""
    NONE = 0
    WARNING = 1
    ERROR = 2

class Handler():
    """Handler for a module"""
    key = ""
    schema_key = ""
    on_extra_settings_update = None
    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        self.error_func = None
        self._is_installed_cache = None

    def set_error_func(self, func):
        """Set the error function for the handler. The function must take the error message and ErrorSeverity as arguments"""
        self.error_func = func

    def throw(self, message : str, severity : ErrorSeverity = ErrorSeverity.WARNING):
        """Throw an error message

        Args:
            message (str): The error message
            severity (ErrorSeverity, optional): The severity of the error. Defaults to ErrorSeverity.WARNING.
        """
        if self.error_func:
            self.error_func(message, severity)

    def set_secondary(self, secondary: bool):
        """Set the secondary settings for the LLM"""
        if secondary:
            self.schema_key = "secondary-settings"
        else:
            self.schema_key = "settings"

    def is_secondary(self) -> bool:
        """ Return if the LLM is a secondary one"""
        return self.schema_key == "secondary-settings"

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
                - button: runs a function when the button is pressed
                    - label: label of the button 
                    - icon: icon of the button, if label is not provided
                    - callback: the function to run on press, first argument is the button
                - entry: input text 
                - toggle: bool
                - combo: for multiple choice
                    - values: list of touples of possible values (display_value, actual_value)
                - range: for number input with a slider 
                    - min: minimum value
                    - max: maximum value 
                    - round: how many digits to round
                - nested: an expander row with nested extra settings 
                    - extra_settings: list of extra_settings
                - download: install something showing the downoad process
                    - is_installed: bool, true if the module is installed, false otherwise  
                    - callback: the function to run on press to download/delete. The download must happen in sync 
                    - download_percentage: callable that takes the key and returns the download percentage as float
            Optional parameters:
                - folder: add a button that opens a folder with the specified path
                - website: add a button that opens a website with the specified path
                - update_settings (bool) if reload the settings in the settings page for the specified handler after that setting change
                - refresh (callable) adds a refresh button in the row to reload the settings in the settings page for the specified handler
                - refresh_icon(str): name of the icon for the refresh button
        """
        return []

    def get_extra_settings_list(self) -> list:
        """Get the list of extra settings"""
        res = []
        for setting in self.get_extra_settings():
            if setting["type"] == "nested":
                res += setting["extra_settings"]
            else:
                res.append(setting)
        return res

    @staticmethod
    def get_extra_requirements() -> list:
        """The list of extra pip requirements needed by the handler"""
        return []

    def install(self):
        """Install the handler requirements"""
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        for module in self.get_extra_requirements():
            install_module(module, pip_path)
        self._is_installed_cache = None

    def on_installed(self):
        """Hook called after installation. Override to invalidate custom caches."""
        pass

    def is_installed(self) -> bool:
        """Return if the handler is installed"""
        if self._is_installed_cache is not None:
            return self._is_installed_cache
        for module in self.get_extra_requirements():
            if find_module(module) is None:
                self._is_installed_cache = False
                return False
        self._is_installed_cache = True
        return True

    def get_setting(self, key: str, search_default = True, return_value = None) -> Any:
        """Get a setting from the given key

        Args:
            key (str): key of the setting
            search_default (bool, optional): if the default value should be searched. Defaults to True. 
            return_value (bool, optional): value to return if the settings was not found. Defaults to None. 
        Returns:
            object: value of the setting
        """        
        j = SettingsCache.get_instance(self.settings).get_json(self.schema_key)
        if self.key not in j or key not in j[self.key]:
            if search_default:
                return self.get_default_setting(key)
            else:
                return return_value
        return j[self.key][key]

    def set_setting(self, key : str, value):
        """Set a setting from key and value for this handler

        Args:
            key (str): key of the setting
            value (object): value of the setting
        """        
        cache = SettingsCache.get_instance(self.settings)
        j = cache.get_json(self.schema_key)
        if self.key not in j:
            j[self.key] = {}
        j[self.key][key] = value
        cache.set_json(self.schema_key, j)

    def get_default_setting(self, key) -> object:
        """Get the default setting from a certain key

        Args:
            key (str): key of the setting

        Returns:
            object: setting value
        """
        extra_settings = self.get_extra_settings()
        for s in extra_settings:
            if s["type"] == "nested":
                for setting in s["extra_settings"]:
                    if setting["key"] == key:
                        return setting["default"]
            if s["key"] == key:
                return s["default"]
        return None

    def get_all_settings(self) -> dict:
        j = SettingsCache.get_instance(self.settings).get_json(self.schema_key)
        return j[self.key] if self.key in j else {}

    def set_extra_settings_update(self, callback):
        self.on_extra_settings_update = callback

    def settings_update(self):
        if self.on_extra_settings_update is not None:
            try:
                self.on_extra_settings_update("")
            except Exception as e:
                print(e)

    def destroy(self):
        pass