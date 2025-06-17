from typing import Any
from gi.repository import GLib, Gio, Adw
import os
import base64

from .utility.media import get_image_base64, get_image_path

from .extensions import NewelleExtension
from .handlers.llm import LLMHandler
from .handlers.tts import TTSHandler
from .handlers.stt import STTHandler
from .handlers.rag import RAGHandler
from .handlers.memory import MemoryHandler
from .handlers.embeddings import EmbeddingHandler
from .handlers.websearch import WebSearchHandler

from .utility.system import is_flatpak
from .utility.pip import install_module
from .utility.profile_settings import get_settings_dict_by_groups
from .constants import AVAILABLE_INTEGRATIONS, AVAILABLE_WEBSEARCH, DIR_NAME, SCHEMA_ID, PROMPTS, AVAILABLE_STT, AVAILABLE_TTS, AVAILABLE_LLMS, AVAILABLE_RAGS, AVAILABLE_PROMPTS, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, SETTINGS_GROUPS
import threading
import pickle
import json
from .extensions import ExtensionLoader
from .utility import override_prompts
from enum import Enum 
from .handlers import Handler
from .ui_controller import UIController
"""
Manage Newelle Application, create handlers, check integrity, manage settings...
"""

class ReloadType(Enum):
    """
    Enum for reload type

    Attributes: 
        NONE: Nothing to realod  
        LLM: Reload LLM
        TTS: Reload TTS 
        STT: Reload STT 
        PROMPTS: Reload PROMPTS 
        RAG: Reload RAG 
        MEMORIES: Reload MEMORIES 
        EMBEDDINGS: Reload EMBEDDINGS 
EXTENSIONS: Reload EXTENSIONS 
        SECONDARY_LLM: Reload SECONDARY_LLM
        RELOAD_CHAT: Reload RELOAD_CHAT
    """
    NONE = 0
    LLM = 1
    TTS = 2
    STT = 3
    PROMPTS = 4
    RAG = 5
    MEMORIES = 6
    EMBEDDINGS = 7
    EXTENSIONS = 8
    SECONDARY_LLM = 9
    RELOAD_CHAT = 10
    RELOAD_CHAT_LIST = 11
    WEBSEARCH = 12
    OFFERS = 13

class NewelleController:
    """Main controller, manages the application

    Attributes: 
        settings: Gio Settings 
        python_path: Path for python sources 
        newelle_settings: current NewelleSettings object 
        handlers: HandlersManager object 
        config_dir: Config dir of the application 
        data_dir: data dir of the application 
        cache_dir: cache dir of the application 
        pip_path: Path for the runtime pip dependencies  
        models_dir: Path for the models 
        extension_path: Path for the extensions 
        extensions_cache: Path for the extensions cache 
        filename: Chat object filename 
        chat: current chat 
        extensionloader: Extensionloader object 
    """
    def __init__(self, python_path) -> None:
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.python_path = python_path
        self.ui_controller : UIController | None = None

    def ui_init(self):
        """Init necessary variables for the UI and load models and handlers"""
        self.init_paths()
        self.check_path_integrity()
        self.load_integrations()
        self.load_extensions()
        self.newelle_settings = NewelleSettings()
        self.newelle_settings.load_settings(self.settings)
        self.load_chats(self.newelle_settings.chat_id)
        self.handlers = HandlersManager(self.settings, self.extensionloader, self.models_dir, self.integrationsloader)
        self.handlers.select_handlers(self.newelle_settings)
        threading.Thread(target=self.handlers.cache_handlers).start()

    def init_paths(self) -> None:
        """Define paths for the application"""
        self.config_dir = GLib.get_user_config_dir()
        self.data_dir = GLib.get_user_data_dir()
        self.cache_dir = GLib.get_user_cache_dir()
        self.chats_path = os.path.join(os.path.dirname(self.data_dir), "datachats.pkl")
        if not is_flatpak():
            self.config_dir = os.path.join(self.config_dir, DIR_NAME)
            self.data_dir = os.path.join(self.config_dir, DIR_NAME)
            self.cache_dir = os.path.join(self.cache_dir, DIR_NAME)
            self.chats_path = os.path.join(self.data_dir, "chats.pkl")

        self.pip_path = os.path.join(self.config_dir, "pip")
        self.models_dir = os.path.join(self.config_dir, "models")
        self.extension_path = os.path.join(self.config_dir, "extensions")
        self.extensions_cache = os.path.join(self.cache_dir, "extensions_cache")
        self.newelle_dir = os.path.join(self.config_dir, DIR_NAME)

    def set_ui_controller(self, ui_controller):
        """Set add tab function"""
        if ui_controller is not None:
            self.ui_controller = ui_controller
            self.extensionloader.set_ui_controller(ui_controller)
            self.integrationsloader.set_ui_controller(ui_controller)

    def load_chats(self, chat_id):
        """Load chats"""
        self.filename = "chats.pkl"
        if os.path.exists(self.chats_path):
            with open(self.chats_path, 'rb') as f:
                self.chats = pickle.load(f)
        else:
            self.chats = [{"name": _("Chat ") + "1", "chat": []}]
        self.chat = self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
   
    def save_chats(self):
        """Save chats"""
        with open(self.chats_path, 'wb') as f:
            pickle.dump(self.chats, f)

    def check_path_integrity(self):
        """Create missing directories"""
        # Create directories
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        if not os.path.exists(self.extension_path):
            os.makedirs(self.extension_path)
        if not os.path.exists(self.extensions_cache):
            os.makedirs(self.extensions_cache)
        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)
        if not os.path.exists(self.newelle_dir):
            os.makedirs(self.newelle_dir, exist_ok=True)
        # Fix Pip environment
        if os.path.isdir(self.pip_path):
            self.python_path.append(self.pip_path)
        else:
            threading.Thread(target=self.init_pip_path, args=(self.python_path,)).start()

    def init_pip_path(self, path):
        """Install a pip module to init a pip path"""
        install_module("pip-install-test", self.pip_path)
        self.python_path.append(self.pip_path)

    def update_settings(self, apply=True):
        """Update settings"""
        newsettings = NewelleSettings()
        newsettings.load_settings(self.settings)
        reload = self.newelle_settings.compare_settings(newsettings)
        if apply:
            self.newelle_settings = newsettings
            for r in reload:
                self.reload(r)
        return reload

    def reload(self, reload_type: ReloadType):
        """Reload the specified settings

        Args:
            reload_type: type of reload
        """
        if reload_type == ReloadType.EXTENSIONS:
            self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                                   extension_cache=self.extensions_cache, settings=self.settings)
            self.extensionloader.load_extensions()
            self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH)
            self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
            self.newelle_settings.load_prompts()
            self.handlers.select_handlers(self.newelle_settings)
            print("Extensions reload")
        elif reload_type == ReloadType.LLM:
            self.handlers.select_handlers(self.newelle_settings)
            threading.Thread(target=self.handlers.llm.load_model, args=(None,)).start()
        elif reload_type == ReloadType.SECONDARY_LLM and self.newelle_settings.use_secondary_language_model:
            self.handlers.select_handlers(self.newelle_settings)
            threading.Thread(target=self.handlers.secondary_llm.load_model, args=(None,)).start()
        elif reload_type in [ReloadType.TTS, ReloadType.STT, ReloadType.MEMORIES]:
            self.handlers.select_handlers(self.newelle_settings)
        elif reload_type == ReloadType.RAG:
            self.handlers.select_handlers(self.newelle_settings)
            threading.Thread(target=self.handlers.rag.load).start()
        elif reload_type == ReloadType.EMBEDDINGS:
            self.handlers.select_handlers(self.newelle_settings)
            threading.Thread(target=self.handlers.embedding.load_model).start()
        elif reload_type == ReloadType.PROMPTS:
            return
        elif reload_type == ReloadType.WEBSEARCH:
            self.handlers.select_handlers(self.newelle_settings)
            self.newelle_settings.prompts_settings["websearch"] = self.newelle_settings.websearch_on
            self.newelle_settings.save_prompts()
            self.newelle_settings.load_prompts()
            

    def set_extensionsloader(self, extensionloader):
        """Change extension loader

        Args:
            extensionloader (): new extension loader 
        """
        self.extensionloader = extensionloader
        self.handlers.extensionloader = extensionloader

    def set_integrationsloader(self, integrationsloader):
        self.integrationsloader = integrationsloader
        self.handlers.integrationsloader = integrationsloader

    def load_extensions(self):
        """Load extensions"""
        # Load extensions
        self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                               extension_cache=self.extensions_cache, settings=self.settings)
        self.extensionloader.load_extensions()
        self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH)
        self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
        self.set_ui_controller(self.ui_controller)

    def load_integrations(self):
        """Load integrations"""
        self.integrationsloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path, settings=self.settings)
        self.integrationsloader.load_integrations(AVAILABLE_INTEGRATIONS)
        self.set_ui_controller(self.ui_controller)

    def create_profile(self, profile_name, picture=None, settings={}, settings_groups=[]):
        """Create a profile

        Args:
            profile_name (): name of the profile 
            picture (): path to the profile picture 
            settings (): settings to override for that profile 
        """
        self.newelle_settings.profile_settings[profile_name] = {"picture": picture, "settings": settings, "settings_groups": settings_groups}
        self.settings.set_string("profiles", json.dumps(self.newelle_settings.profile_settings))

    def delete_profile(self, profile_name):
        """Delete a profile

        Args:
            profile_name (): name of the profile to delete 
        """
        if profile_name == "Assistant" or profile_name == self.settings.get_string("current-profile"):
            return
        del self.newelle_settings.profile_settings[profile_name]
        self.settings.set_string("profiles", json.dumps(self.newelle_settings.profile_settings))
        self.update_settings()

    def update_current_profile(self):
        """Update the current profile"""
        self.current_profile = self.settings.get_string("current-profile")
        self.profile_settings = self.newelle_settings.profile_settings
        groups = self.profile_settings[self.current_profile].get("settings_groups", [])
        old_settings = get_settings_dict_by_groups(self.settings, groups, SETTINGS_GROUPS, ["current-profile", "profiles"] )
        self.profile_settings = json.loads(self.settings.get_string("profiles"))
        self.profile_settings[self.current_profile]["settings"] = old_settings
        self.settings.set_string("profiles", json.dumps(self.profile_settings))
    
    def export_profile(self, profile_name, remove_passwords=False, export_propic=False):
        """Export a profile

        Args:
            profile_name (): name of the profile to export
        """
        self.update_current_profile()
        profiles = json.loads(self.settings.get_string("profiles"))
        profile = profiles.get(profile_name, None)
        if profile is None:
            return {}
        else:
            if remove_passwords:
                profile["settings"] = self.handlers.remove_passwords(profile["settings"])
            if export_propic and profile["picture"] is not None:
                profile["picture"] = get_image_base64(profile["picture"])
            else:
                profile["picture"] = None
            profile["name"] = profile_name
            return profile

    def import_profile(self, js):
        """Import a profile

        Args:
            json (): json to import
        """
        self.newelle_settings.profile_settings[js["name"]] = js
        if self.newelle_settings.profile_settings[js["name"]]["picture"] is not None:
            image_str = self.newelle_settings.profile_settings[js["name"]]["picture"]
            path = os.path.join(self.config_dir, "profiles")
            raw_data = base64.b64decode(image_str[len("data:image/png;base64,"):])
            img_path = os.path.join(path, js["name"] + ".png")
            with open(img_path, "wb") as f:
                f.write(raw_data)
            self.newelle_settings.profile_settings[js["name"]]["picture"] = img_path

        self.settings.set_string("profiles", json.dumps(self.newelle_settings.profile_settings))


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
            self.profile_settings[self.current_profile] = {"settings": {}, "picture": None, "settings_groups": []}

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
        self.embedding_settings = self.settings.get_string("embedding-settings")
        self.memory_on = self.settings.get_boolean("memory-on")
        self.memory_model = self.settings.get_string("memory-model")
        self.memory_settings = self.settings.get_string("memory-settings")
        self.rag_on = self.settings.get_boolean("rag-on")
        self.rag_on_documents = self.settings.get_boolean("rag-on-documents")
        self.rag_model = self.settings.get_string("rag-model")
        self.rag_settings = self.settings.get_string("rag-settings")
        self.rag_limit = self.settings.get_int("documents-context-limit")
        self.language_model = self.settings.get_string("language-model")
        self.llm_settings = self.settings.get_string("llm-settings")
        self.secondary_language_model = self.settings.get_string("secondary-language-model")
        self.secondary_language_model_settings = self.settings.get_string("llm-secondary-settings")
        self.use_secondary_language_model = self.settings.get_boolean("secondary-llm-on")
        self.custom_prompts = json.loads(self.settings.get_string("custom-prompts"))
        self.prompts_settings = json.loads(self.settings.get_string("prompts-settings")) 
        self.extensions_settings = self.settings.get_string("extensions-settings")
        self.username = self.settings.get_string("user-name")
        self.zoom = self.settings.get_int("zoom")
        self.send_on_enter = self.settings.get_boolean("send-on-enter")
        self.max_run_times = self.settings.get_int("max-run-times")
        self.websearch_on = self.settings.get_boolean("websearch-on")
        self.websearch_model = self.settings.get_string("websearch-model")
        self.websearch_settings = self.settings.get_string("websearch-settings")
        
        self.external_browser = settings.get_boolean("external-browser")
        self.initial_browser_page = settings.get_string("initial-browser-page")
        self.browser_search_string = settings.get_string("browser-search-string")
        self.browser_session_persist = settings.get_boolean("browser-session-persist")
        self.editor_color_scheme = settings.get_string("editor-color-scheme")
        self.load_prompts()
        # Adjust paths
        if os.path.exists(os.path.expanduser(self.main_path)):
            os.chdir(os.path.expanduser(self.main_path))
        else:
            self.main_path = "~"

    def load_prompts(self):
        """Load prompts and do overrides"""
        self.custom_prompts = json.loads(self.settings.get_string("custom-prompts"))
        self.prompts = override_prompts(self.custom_prompts, PROMPTS)
        self.bot_prompts = []
        for prompt in AVAILABLE_PROMPTS:
            is_active = False
            if prompt["setting_name"] in self.prompts_settings:
                is_active = self.prompts_settings[prompt["setting_name"]]
            else:
                is_active = prompt["default"]
            if is_active:
                self.bot_prompts.append(self.prompts[prompt["key"]])

    def compare_settings(self, new_settings) -> list[ReloadType]:
        """Find the difference between two NewelleSettings

        Args:
            new_settings (NewelleSettings): settings to compare   

        Returns:
            list[ReloadType]: list of ReloadType to reload
        """
        reloads = []
        if self.language_model != new_settings.language_model or self.llm_settings != new_settings.llm_settings:
            reloads.append(ReloadType.LLM)
        if self.secondary_language_model != new_settings.secondary_language_model or self.use_secondary_language_model != new_settings.use_secondary_language_model or self.secondary_language_model_settings != new_settings.secondary_language_model_settings:
            reloads.append(ReloadType.SECONDARY_LLM)
        
        if self.tts_program != new_settings.tts_program:
            reloads.append(ReloadType.TTS)

        if self.stt_engine != new_settings.stt_engine:
            reloads.append(ReloadType.STT)

        if self.embedding_model != new_settings.embedding_model or self.embedding_settings != new_settings.embedding_settings:
            reloads.append(ReloadType.EMBEDDINGS)

        if self.memory_on != new_settings.memory_on or self.memory_model != new_settings.memory_model or self.memory_settings != new_settings.memory_settings:
            reloads.append(ReloadType.MEMORIES)

        if self.rag_on != new_settings.rag_on or self.rag_model != new_settings.rag_model or self.rag_settings != new_settings.rag_settings:
            reloads.append(ReloadType.RAG)
        if self.extensions_settings != new_settings.extensions_settings:
            reloads += [ReloadType.EXTENSIONS, ReloadType.LLM, ReloadType.SECONDARY_LLM, ReloadType.EMBEDDINGS, ReloadType.EMBEDDINGS, ReloadType.MEMORIES, ReloadType.RAG, ReloadType.WEBSEARCH]
        if self.username != new_settings.username:
            reloads.append(ReloadType.RELOAD_CHAT)
        if self.reverse_order != new_settings.reverse_order:
            reloads.append(ReloadType.RELOAD_CHAT_LIST)

        if self.websearch_on != new_settings.websearch_on or self.websearch_model != new_settings.websearch_model or self.websearch_settings != new_settings.websearch_settings:
            reloads.append(ReloadType.WEBSEARCH)
        # Check prompts
        if len(self.prompts) != len(new_settings.prompts):
            reloads.append(ReloadType.PROMPTS)
        if self.offers != new_settings.offers:
            reloads.append(ReloadType.OFFERS)

        return reloads

    def save_prompts(self):
        self.settings.set_string("prompts-settings", json.dumps(self.prompts_settings))


class HandlersManager:
    """Manage handlers

    Attributes: 
        settings: Gio.Settings 
        extensionloader: ExtensionLoader 
        directory: Models direcotry 
        handlers: Cached handlers 
        llm: LLM Handler 
        stt: STT Handler 
        tts: TTS Handler
        embedding: Embedding Handler 
        memory: Memory Handler
        rag: RAG Handler 
    """
    def __init__(self, settings: Gio.Settings, extensionloader : ExtensionLoader, models_path, integrations: ExtensionLoader):
        self.settings = settings
        self.extensionloader = extensionloader
        self.directory = models_path
        self.handlers =  {}
        self.handlers_cached = threading.Semaphore()
        self.handlers_cached.acquire()
        self.integrationsloader = integrations

    def fix_handlers_integrity(self, newelle_settings: NewelleSettings):
        """Select available handlers if not available handlers in settings

        Args:
            newelle_settings: Newelle settings
        """
        if newelle_settings.language_model not in AVAILABLE_LLMS:
            newelle_settings.language_model = list(AVAILABLE_LLMS.keys())[0]
        if newelle_settings.secondary_language_model not in AVAILABLE_LLMS:
            newelle_settings.secondary_language_model = list(AVAILABLE_LLMS.keys())[0]
        if newelle_settings.embedding_model not in AVAILABLE_EMBEDDINGS:
            newelle_settings.embedding_model = list(AVAILABLE_EMBEDDINGS.keys())[0]
        if newelle_settings.memory_model not in AVAILABLE_MEMORIES:
            newelle_settings.memory_model = list(AVAILABLE_MEMORIES.keys())[0]
        if newelle_settings.rag_model not in AVAILABLE_RAGS:
            newelle_settings.rag_model = list(AVAILABLE_RAGS.keys())[0]
        if newelle_settings.tts_program not in AVAILABLE_TTS:
            newelle_settings.tts_program = list(AVAILABLE_TTS.keys())[0]
        if newelle_settings.stt_engine not in AVAILABLE_STT:
            newelle_settings.stt_engine = list(AVAILABLE_STT.keys())[0]
        if newelle_settings.websearch_model not in AVAILABLE_WEBSEARCH:
            newelle_settings.websearch_model = list(AVAILABLE_WEBSEARCH.keys())[0]
      
    def set_ui_controller(self, ui_controller):
        self.ui_controller = ui_controller

    def select_handlers(self, newelle_settings: NewelleSettings):
        """Assign the selected handlers

        Args:
            newelle_settings: Newelle settings 
        """
        self.fix_handlers_integrity(newelle_settings)
        # Get LLM 
        self.llm : LLMHandler = self.get_object(AVAILABLE_LLMS, newelle_settings.language_model)
        if newelle_settings.use_secondary_language_model:
            self.secondary_llm : LLMHandler = self.get_object(AVAILABLE_LLMS, newelle_settings.secondary_language_model, True)
        else:
            self.secondary_llm : LLMHandler = self.llm
        self.stt : STTHandler = self.get_object(AVAILABLE_STT, newelle_settings.stt_engine)
        self.tts : TTSHandler = self.get_object(AVAILABLE_TTS, newelle_settings.tts_program)
        self.embedding : EmbeddingHandler= self.get_object(AVAILABLE_EMBEDDINGS, newelle_settings.embedding_model)
        self.memory : MemoryHandler = self.get_object(AVAILABLE_MEMORIES, newelle_settings.memory_model)
        self.memory.set_memory_size(newelle_settings.memory)
        self.rag : RAGHandler = self.get_object(AVAILABLE_RAGS, newelle_settings.rag_model)
        self.websearch : WebSearchHandler = self.get_object(AVAILABLE_WEBSEARCH, newelle_settings.websearch_model)
        # Assign handlers 
        self.integrationsloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.extensionloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.memory.set_handlers(self.secondary_llm, self.embedding)
        
        self.rag.set_handlers(self.llm, self.embedding)
        threading.Thread(target=self.install_missing_handlers).start()

    def set_error_func(self, func):
        def async_set():
            self.handlers_cached.acquire()
            self.handlers_cached.release()
            for handler in self.handlers.values():
                handler.set_error_func(func)
        threading.Thread(target=async_set).start()

    def load_handlers(self):
        """Load handlers"""
        self.llm.load_model(None)
        if self.settings.get_boolean("secondary-llm-on"):
            self.secondary_llm.load_model(None)
        self.embedding.load_model()
        if self.settings.get_boolean("rag-on"):
            self.rag.load()

    def install_missing_handlers(self):
        """Install selected handlers that are not installed. Assumes that select_handlers has been called"""
        if not self.llm.is_installed():
            self.llm.install()
        if not self.stt.is_installed():
            self.stt.install()
        if not self.tts.is_installed():
            self.tts.install()
        if not self.embedding.is_installed():
            self.embedding.install()
        if not self.memory.is_installed():
            self.memory.install()
        if not self.rag.is_installed():
            self.rag.install()

    def cache_handlers(self):
        """Cache handlers"""
        self.handlers = {}
        for key in AVAILABLE_TTS:
            self.handlers[(key, self.convert_constants(AVAILABLE_TTS), False)] = self.get_object(AVAILABLE_TTS, key)
        for key in AVAILABLE_STT:
            self.handlers[(key, self.convert_constants(AVAILABLE_STT), False)] = self.get_object(AVAILABLE_STT, key)
        for key in AVAILABLE_LLMS:
            self.handlers[(key, self.convert_constants(AVAILABLE_LLMS), False)] = self.get_object(AVAILABLE_LLMS, key)
        # Secondary LLMs
        for key in AVAILABLE_LLMS:
            self.handlers[(key, self.convert_constants(AVAILABLE_LLMS), True)] = self.get_object(AVAILABLE_LLMS, key, True)
        for key in AVAILABLE_MEMORIES:
            self.handlers[(key, self.convert_constants(AVAILABLE_MEMORIES), False)] = self.get_object(AVAILABLE_MEMORIES, key)
        for key in AVAILABLE_RAGS:
            self.handlers[(key, self.convert_constants(AVAILABLE_RAGS), False)] = self.get_object(AVAILABLE_RAGS, key)
        for key in AVAILABLE_EMBEDDINGS:
            self.handlers[(key, self.convert_constants(AVAILABLE_EMBEDDINGS), False)] = self.get_object(AVAILABLE_EMBEDDINGS, key)
        for key in AVAILABLE_WEBSEARCH:
            self.handlers[(key, self.convert_constants(AVAILABLE_WEBSEARCH), False)] = self.get_object(AVAILABLE_WEBSEARCH, key)
        self.handlers_cached.release()
    
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
                case "websearch":
                    return AVAILABLE_WEBSEARCH
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
            elif constants == AVAILABLE_WEBSEARCH:
                return "websearch"
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
            model = constants[key]["class"](self.settings, self.directory)
            model.set_secondary_settings(secondary)
        elif constants == AVAILABLE_STT:
            model = constants[key]["class"](self.settings,self.directory)
        elif constants == AVAILABLE_TTS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_MEMORIES:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_EMBEDDINGS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_RAGS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_WEBSEARCH:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == self.extensionloader.extensionsmap:
            model = self.extensionloader.extensionsmap[key]
            if model is None:
                raise Exception("Extension not found")
        else:
            raise Exception("Unknown constants")
        return model

    def get_constants_from_object(self, handler: Handler) -> dict[str, Any]:
        """Get the constants from an hander

        Args:
            handler: the handler 

        Raises:
            Exception: if the handler is not known

        Returns: AVAILABLE_LLMS, AVAILABLE_STT, AVAILABLE_TTS based on the type of the handler 
        """
        if issubclass(type(handler), TTSHandler):
            return AVAILABLE_TTS
        elif issubclass(type(handler), STTHandler):
            return AVAILABLE_STT
        elif issubclass(type(handler), LLMHandler):
            return AVAILABLE_LLMS
        elif issubclass(type(handler), NewelleExtension):
            return self.extensionloader.extensionsmap
        elif issubclass(type(handler), MemoryHandler):
            return AVAILABLE_MEMORIES
        elif issubclass(type(handler), EmbeddingHandler):
            return AVAILABLE_EMBEDDINGS
        elif issubclass(type(handler), RAGHandler):
            return AVAILABLE_RAGS
        elif issubclass(type(handler), WebSearchHandler):
            return AVAILABLE_WEBSEARCH
        else:
            raise Exception("Unknown handler")
    
    def remove_passwords(self, settings_dict):
        for key, handler in self.handlers.items():
            h : Handler = handler
            settings = h.get_extra_settings_list()
            if h.schema_key not in settings_dict:
                continue
            schema_settings = json.loads(settings_dict[h.schema_key])
            if h.key not in schema_settings:
                continue
            for setting in settings:
                key = setting.get("key", "")
                if setting.get("password", False) or key in ["api", "token"]:
                     schema_settings[h.key][setting["key"]] = setting["default"]
            settings_dict[h.schema_key] = json.dumps(schema_settings)
        return settings_dict
