from dataclasses import dataclass
from typing import Any
from gi.repository import GLib, Gio
import os
from .utility.system import is_flatpak
from .utility.pip import install_module
from .constants import DIR_NAME, SCHEMA_ID, PROMPTS, AVAILABLE_STT, AVAILABLE_TTS, AVAILABLE_LLMS, AVAILABLE_RAGS, AVAILABLE_PROMPTS, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS
import sys
import threading
import pickle
import json
from .extensions import ExtensionLoader
from .utility import override_prompts
from enum import Enum 
from .handlers import Handler

"""
Not yet used in the code.
Manage Newelle Application, create handlers, check integrity, manage settings...
"""

class ReloadType(Enum):
    NONE = 0
    LLM = 1
    TTS = 2
    STT = 3
    PROMPTS = 4
    RAG = 5
    MEMORIES = 6
    EMBEDDINGS = 7
    EXTENSIONS = 8

class NewelleController:
    def __init__(self) -> None:
        self.settings = Gio.Settings.new(SCHEMA_ID)
    
    def ui_init(self):
        self.init_paths()
        self.check_path_integrity()
        self.newelle_settings = NewelleSettings()
        self.newelle_settings.load_settings(self.settings)
        self.load_chats(self.newelle_settings.chat_id)

    def init_paths(self) -> None:
        """Define paths for the application"""
        self.config_dir = GLib.get_user_config_dir()
        self.data_dir = GLib.get_user_data_dir()
        self.cache_dir = GLib.get_user_cache_dir()

        if not is_flatpak():
            self.config_dir = os.path.join(self.config_dir, DIR_NAME)
            self.data_dir = os.path.join(self.config_dir, DIR_NAME)
            self.cache_dir = os.path.join(self.cache_dir, DIR_NAME)


        self.pip_path = os.path.join(self.config_dir, "pip")
        self.models_dir = os.path.join(self.config_dir, "models")
        self.extension_path = os.path.join(self.config_dir, "extensions")
        self.extensions_cache = os.path.join(self.cache_dir, "extensions_cache")


    def load_chats(self, chat_id):
        """Load chats"""
        self.filename = "chats.pkl"
        if os.path.exists(self.data_dir + self.filename):
            with open(self.data_dir + self.filename, 'rb') as f:
                self.chats = pickle.load(f)
        else:
            self.chats = [{"name": _("Chat ") + "1", "chat": []}]
        self.chat = self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
    
    def check_path_integrity(self):
        """Create missing directories"""
        # Create directories
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        if not os.path.exists(self.extension_path):
            os.makedirs(self.extension_path)
        if not os.path.exists(self.extensions_cache):
            os.makedirs(self.extensions_cache)
        # Fix Pip environment
        if os.path.isdir(self.pip_path):
            sys.path.append(self.pip_path)
        else:
            threading.Thread(target=self.init_pip_path, args=(sys.path,)).start()

    def init_pip_path(self, path):
        """Install a pip module to init a pip path"""
        install_module("pip-install-test", self.pip_path)
        path.append(self.pip_path)

    def load_extensions(self):
        """Load extensions"""
        # Load extensions
        self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                               extension_cache=self.extensions_cache, settings=self.settings)
        self.extensionloader.load_extensions()
        self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS)
        self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)

class NewelleSettings:

    def load_settings(self, settings):
        """Basic settings loading

        Args:
            settings (): settings manager object 
        """
        self.settings = settings
        self.profile_settings = json.loads(self.settings.get_string("profiles"))
        self.current_profile = self.settings.get_string("current-profile")
        if len(self.profile_settings) == 0 or self.current_profile not in self.profile_settings:
            self.profile_settings[self.current_profile] = {"settings": {}, "picture": None}

        # Init variables
        self.automatic_stt_status = False
        settings = self.settings
       
        # Get settings variables
        self.offers = settings.get_int("offers")
        self.virtualization = settings.get_boolean("virtualization")
        self.memory = settings.get_int("memory")
        self.hidden_files = settings.get_boolean("hidden-files")
        self.reverse_order = settings.get_boolean("reverse-order")
        self.remove_thinking = settings.get_boolean("remove-thinking")
        self.auto_generate_name = settings.get_boolean("auto-generate-name")
        self.chat_id = settings.get_int("chat")
        self.main_path = settings.get_string("path")
        self.auto_run = settings.get_boolean("auto-run")
        self.display_latex = settings.get_boolean("display-latex")
        self.tts_enabled = settings.get_boolean("tts-on")
        self.tts_program = settings.get_string("tts")
        self.tts_voice = settings.get_string("tts-voice")
        self.stt_engine = settings.get_string("stt-engine")
        self.stt_settings = settings.get_string("stt-settings")
        self.external_terminal = settings.get_string("external-terminal")
        self.automatic_stt = settings.get_boolean("automatic-stt")
        self.stt_silence_detection_threshold = settings.get_double("stt-silence-detection-threshold")
        self.stt_silence_detection_duration = settings.get_int("stt-silence-detection-duration")
        self.embedding_model = self.settings.get_string("embedding-model")
        self.memory_on = self.settings.get_boolean("memory-on")
        self.memory_model = self.settings.get_string("memory-model")
        self.rag_on = self.settings.get_boolean("rag-on")
        self.rag_on_documents = self.settings.get_boolean("rag-on-documents")
        self.rag_model = self.settings.get_string("rag-model")
        self.language_model = self.settings.get_string("language-model")
        self.secondary_language_model = self.settings.get_string("secondary-language-model")
        self.use_secondary_language_model = self.settings.get_boolean("secondary-llm-on")
        self.custom_prompts = json.loads(self.settings.get_string("custom-prompts"))
        #self.prompts = override_prompts(self.custom_prompts, PROMPTS)
        self.prompts_settings = json.loads(self.settings.get_string("prompts-settings"))
        
        # Adjust paths
        if os.path.exists(os.path.expanduser(self.main_path)):
            os.chdir(os.path.expanduser(self.main_path))
        else:
            self.main_path = "~"

    def compare_settings(self, new_settings) -> list[ReloadType]:
        reloads = []
        if self.language_model != new_settings.language_model:
            reloads.append(ReloadType.LLM)
        if self.secondary_language_model != new_settings.secondary_language_model or self.use_secondary_language_model != new_settings.use_secondary_language_model:
            reloads.append(ReloadType.LLM)
        
        if self.tts_program != new_settings.tts_program:
            reloads.append(ReloadType.TTS)

        if self.stt_engine != new_settings.stt_engine:
            reloads.append(ReloadType.STT)

        if self.embedding_model != new_settings.embedding_model:
            reloads.append(ReloadType.EMBEDDINGS)

        if self.memory_on != new_settings.memory_on or self.memory_model != new_settings.memory_model:
            reloads.append(ReloadType.MEMORIES)

        if self.rag_on != new_settings.rag_on or self.rag_on_documents != new_settings.rag_on_documents or self.rag_model != new_settings.rag_model:
            reloads.append(ReloadType.RAG)
        return reloads


class HandlersManager:
    def __init__(self, settings: Gio.Settings, extensionloader : ExtensionLoader, models_path):
        self.settings = settings
        self.extensionloader = extensionloader
        self.directory = models_path

    def cache_handlers(self):
        self.handlers = {}
        for key in AVAILABLE_TTS:
            self.handlers[(key, self.convert_constants(AVAILABLE_TTS))] = self.get_object(AVAILABLE_TTS, key)
        for key in AVAILABLE_STT:
            self.handlers[(key, self.convert_constants(AVAILABLE_STT))] = self.get_object(AVAILABLE_STT, key)
        for key in AVAILABLE_LLMS:
            self.handlers[(key, self.convert_constants(AVAILABLE_LLMS), False)] = self.get_object(AVAILABLE_LLMS, key)
        # Secondary LLMs
        for key in AVAILABLE_LLMS:
            self.handlers[(key, self.convert_constants(AVAILABLE_LLMS), True)] = self.get_object(AVAILABLE_LLMS, key, True)
        for key in AVAILABLE_MEMORIES:
            self.handlers[(key, self.convert_constants(AVAILABLE_MEMORIES), False)] = self.get_object(AVAILABLE_MEMORIES, key)
        for key in AVAILABLE_RAGS:
            self.handlers[(key, self.convert_constants(AVAILABLE_RAGS), False)] = self.get_object(AVAILABLE_RAGS, key)

    def convert_constants(self, constants: str | dict[str, Any]) -> (str | dict):
        """Get an handler instance for the specified handler key

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler

        Raises:
            Exception: if the constant is not valid 

        Returns:
            The created handler           
        """
        if type(constants) is str:
            match constants:
                case "tts":
                    return AVAILABLE_TTS
                case "stt":
                    return AVAILABLE_STT
                case "llm":
                    return AVAILABLE_LLMS
                case "memory":
                    return AVAILABLE_MEMORIES
                case "embedding":
                    return AVAILABLE_EMBEDDINGS
                case "rag":
                    return AVAILABLE_RAGS
                case "extension":
                    return self.extensionloader.extensionsmap
                case _:
                    raise Exception("Unknown constants")
        else:
            if constants == AVAILABLE_LLMS:
                return "llm"
            elif constants == AVAILABLE_STT:
                return "stt"
            elif constants == AVAILABLE_TTS:
                return "tts"
            elif constants == AVAILABLE_MEMORIES:
                return "memory"
            elif constants == AVAILABLE_EMBEDDINGS:
                return "embedding"
            elif constants == AVAILABLE_RAGS:
                return "rag"
            elif constants == self.extensionloader.extensionsmap:
                return "extension"
            else:
                raise Exception("Unknown constants")

    def get_object(self, constants: dict[str, Any], key:str, secondary=False) -> (Handler):
        """Get an handler instance for the specified handler key

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler
            secondary: if to use secondary settings

        Raises:
            Exception: if the constant is not valid 

        Returns:
            The created handler           
        """
        if (key, self.convert_constants(constants), secondary) in self.handlers:
            return self.handlers[(key, self.convert_constants(constants), secondary)]

        if constants == AVAILABLE_LLMS:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "models"))
            model.set_secondary_settings(secondary)
        elif constants == AVAILABLE_STT:
            model = constants[key]["class"](self.settings,os.path.join(self.directory, "models"))
        elif constants == AVAILABLE_TTS:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "models"))
        elif constants == AVAILABLE_MEMORIES:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "models"))
        elif constants == AVAILABLE_EMBEDDINGS:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "models"))
        elif constants == AVAILABLE_RAGS:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "models"))
        elif constants == self.extensionloader.extensionsmap:
            model = self.extensionloader.extensionsmap[key]
            if model is None:
                raise Exception("Extension not found")
        else:
            raise Exception("Unknown constants")
        return model
