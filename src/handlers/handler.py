import os
import json
import copy
import gettext
import threading
from gi.repository import GLib
from ..utility.pip import find_module, install_module
from ..utility.download_manager import DownloadKind, get_download_manager
from typing import Any
from enum import Enum

_ = gettext.gettext


class IsolatedSettings:
    """A read-through, in-memory overlay for a ``Gio.Settings`` instance.

    Provider handlers expect the regular settings API and some of them write
    defaults while they initialise.  A subagent needs those handlers to reuse
    the user's credentials without letting a model override mutate the active
    profile.  This small adapter snapshots overrides and keeps every write
    local while forwarding unknown/read-only API calls to the underlying
    settings object.
    """

    def __init__(self, settings, overrides: dict | None = None):
        self._settings = settings
        self._values = copy.deepcopy(overrides or {})
        self._callbacks = []

    def _get(self, method: str, key: str):
        if key in self._values:
            return copy.deepcopy(self._values[key])
        return getattr(self._settings, method)(key)

    def _set(self, key: str, value) -> bool:
        self._values[key] = copy.deepcopy(value)
        for callback, user_args in tuple(self._callbacks):
            try:
                callback(self, key, *user_args)
            except Exception:
                pass
        return True

    def get_string(self, key):
        return self._get("get_string", key)

    def get_boolean(self, key):
        return self._get("get_boolean", key)

    def get_int(self, key):
        return self._get("get_int", key)

    def get_uint(self, key):
        return self._get("get_uint", key)

    def get_double(self, key):
        return self._get("get_double", key)

    def get_value(self, key):
        return self._get("get_value", key)

    def set_string(self, key, value):
        return self._set(key, value)

    def set_boolean(self, key, value):
        return self._set(key, bool(value))

    def set_int(self, key, value):
        return self._set(key, int(value))

    def set_uint(self, key, value):
        return self._set(key, int(value))

    def set_double(self, key, value):
        return self._set(key, float(value))

    def set_value(self, key, value):
        return self._set(key, value)

    def connect(self, signal: str, callback, *user_args):
        # The overlay is a launch-time snapshot.  Track local changes only;
        # changes to global settings must not alter a running subagent.
        if signal == "changed":
            self._callbacks.append((callback, user_args))
            return len(self._callbacks)
        return 0

    def disconnect(self, handler_id):
        index = int(handler_id) - 1
        if 0 <= index < len(self._callbacks):
            self._callbacks[index] = (lambda *_: None, ())

    def __getattr__(self, name):
        return getattr(self._settings, name)


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


class SettingsSnapshot:
    """Read-only-per-request view of ``Gio.Settings``.

    Workspace switches update the process-wide GSettings object.  A chat
    request must keep seeing the values that were active when it started, so
    background work uses this lightweight snapshot instead of the live
    object.  Setters update only the snapshot; handler code that writes a
    setting therefore cannot change the workspace currently shown in the UI.
    """

    def __init__(self, settings):
        self._base = settings
        self._values = {}
        for key in settings.list_keys():
            try:
                self._values[key] = settings.get_value(key).unpack()
            except Exception:
                continue

    def overlay(self, values):
        for key, value in (values or {}).items():
            if key in self._values:
                self._values[key] = value

    def connect(self, *_args, **_kwargs):
        return 0

    def list_keys(self):
        return list(self._values)

    def _get(self, key, fallback):
        return self._values[key] if key in self._values else fallback()

    def get_string(self, key):
        return str(self._get(key, lambda: self._base.get_string(key)))

    def get_boolean(self, key):
        return bool(self._get(key, lambda: self._base.get_boolean(key)))

    def get_int(self, key):
        return int(self._get(key, lambda: self._base.get_int(key)))

    def get_double(self, key):
        return float(self._get(key, lambda: self._base.get_double(key)))

    def get_strv(self, key):
        value = self._get(key, lambda: self._base.get_strv(key))
        return list(value)

    def get_value(self, key):
        if key not in self._values:
            return self._base.get_value(key)
        current = self._base.get_value(key)
        return GLib.Variant(current.get_type_string(), self._values[key])

    def _set(self, key, value):
        self._values[key] = value

    def set_string(self, key, value):
        self._set(key, value)

    def set_boolean(self, key, value):
        self._set(key, bool(value))

    def set_int(self, key, value):
        self._set(key, int(value))

    def set_double(self, key, value):
        self._set(key, float(value))

    def set_strv(self, key, value):
        self._set(key, list(value))

    def set_value(self, key, value):
        self._set(key, value.unpack() if hasattr(value, "unpack") else value)


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
                - info: read-only title and description
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

    def install_with_progress(self, title: str | None = None):
        """Run the legacy synchronous install method as a tracked operation.

        Subclasses and third-party handlers keep their existing ``install``
        signature. Nested calls to ``install_module`` on this worker thread
        automatically enrich the parent task with pip progress.
        """
        manager = get_download_manager()
        source_id = self.get_install_source_id()
        if manager.has_active(source_id):
            return None
        with manager.operation(
            title or f"Install {self.key}",
            kind=DownloadKind.DEPENDENCY,
            source_id=source_id,
            phase=_("Installing"),
            cancellable=False,
        ) as task:
            result = self.install()
            self._is_installed_cache = None
            if not self.is_installed():
                raise RuntimeError(f"{self.key} did not finish installing")
            task.update(phase=_("Finalizing installation"), reset_progress=True)
            self.on_installed()
            return result

    def get_install_source_id(self) -> str:
        return f"handler:{self.schema_key}:{self.key}"

    def install_in_background(self, title: str | None = None) -> None:
        """Start a tracked legacy installation without blocking the GTK thread."""
        def run_install():
            try:
                self.install_with_progress(title)
            except Exception as error:
                print(f"Error installing {self.key}: {error}")

        threading.Thread(
            target=run_install,
            name=f"install-{self.key}",
            daemon=True,
        ).start()

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
