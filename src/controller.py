from typing import Any
from gi.repository import GLib, Gio, Adw
import os
import base64
import time
import re
import copy

from .tools import ToolRegistry
from .utility.media import get_image_base64, get_image_path, extract_supported_files

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
from .constants import AVAILABLE_INTEGRATIONS, AVAILABLE_WEBSEARCH, DIR_NAME, SCHEMA_ID, PROMPTS, AVAILABLE_STT, AVAILABLE_TTS, AVAILABLE_LLMS, AVAILABLE_RAGS, AVAILABLE_PROMPTS, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, SETTINGS_GROUPS, restore_handlers
import threading
import pickle
import json
import datetime
import uuid as uuid_lib
from .extensions import ExtensionLoader
from .utility import override_prompts
from .utility.strings import clean_bot_response, clean_prompt, count_tokens, remove_thinking_blocks, get_edited_messages
from .utility.replacehelper import PromptFormatter, replace_variables_dict
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
    TOOLS = 14
    WAKEWORD = 15 

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
    @property
    def chat(self):
        """Get the current chat messages list"""
        if hasattr(self, 'chats') and hasattr(self, 'newelle_settings'):
            chat_id = self.newelle_settings.chat_id
            return self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
        return []

    @chat.setter
    def chat(self, value):
        """Set the current chat messages list"""
        if hasattr(self, 'chats') and hasattr(self, 'newelle_settings'):
            chat_id = self.newelle_settings.chat_id
            index = min(chat_id, len(self.chats) - 1)
            self.chats[index]["chat"] = value
    
    def get_chat_by_id(self, chat_id):
        """Get chat messages list by explicit chat_id.

        Args:
            chat_id: The chat ID to get messages for

        Returns:
            The chat messages list for the specified chat_id, or empty list if invalid
        """
        if hasattr(self, 'chats') and self.chats and 0 <= chat_id < len(self.chats):
            return self.chats[chat_id]["chat"]
        return []
    
    def set_chat_by_id(self, chat_id, value):
        """Set chat messages list by explicit chat_id.

        Args:
            chat_id: The chat ID to set messages for
            value: The new chat messages list
        """
        if hasattr(self, 'chats') and self.chats and 0 <= chat_id < len(self.chats):
            self.chats[chat_id]["chat"] = value

    def get_console_reply(self, chat_id, id_message):
        """Get existing console reply from chat history if available."""
        if not hasattr(self, 'chats') or not self.chats:
            return None
        chat = self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
        idx = min(id_message, len(chat) - 1)
        if idx >= 0 and chat[idx].get("User") == "Console":
            return chat[idx]["Message"]
        return None

    def get_tool_response(self, chat_id, id_message, tool_name, tool_uuid):
        """Get existing tool response from chat history by tool name and UUID."""
        if not hasattr(self, 'chats') or not self.chats:
            return None
        chat = self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
        for i in range(id_message, len(chat)):
            entry = chat[i]
            if entry.get("User") == "Console":
                msg = entry.get("Message", "")
                if msg.startswith(f"[Tool: {tool_name}, ID: {tool_uuid}]"):
                    lines = msg.split("\n", 1)
                    return lines[1] if len(lines) > 1 else ""
                if not msg.startswith("[Tool:"):
                    return msg
        return None

    def get_tool_call_uuid(self, chat_id, id_message, tool_name, tool_call_index):
        """Get tool call UUID from chat history during restore."""
        if not hasattr(self, 'chats') or not self.chats:
            return str(uuid_lib.uuid4())[:8]
        chat = self.chats[min(chat_id, len(self.chats) - 1)]["chat"]
        count = 0
        for i in range(id_message, len(chat)):
            entry = chat[i]
            if entry.get("User") == "Console":
                msg = entry.get("Message", "")
                match = re.match(r'\[Tool: ([^,]+), ID: ([^\]]+)\]', msg)
                if match:
                    parsed_name, parsed_uuid = match.groups()
                    if parsed_name == tool_name:
                        if count == tool_call_index:
                            return parsed_uuid
                        count += 1
                elif not msg.startswith("[Tool:"):
                    return str(uuid_lib.uuid4())[:8]
        return str(uuid_lib.uuid4())[:8]

    def __init__(self, python_path) -> None:
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.python_path = python_path
        self.ui_controller : UIController | None = None
        self.installing_handlers = {}
        self.tools = ToolRegistry()
        self.msgid = 0
        self.chat_documents_index = {}

    def ui_init(self):
        """Init necessary variables for the UI and load models and handlers"""
        self.init_paths()
        self.check_path_integrity()
        self.load_integrations()
        self.load_extensions()
        self.newelle_settings = NewelleSettings()
        self.newelle_settings.load_settings(self.settings)
        self.load_chats(self.newelle_settings.chat_id)
        self.handlers = HandlersManager(self.settings, self.extensionloader, self.models_dir, self.integrationsloader, self.installing_handlers)
        self.handlers.select_handlers(self.newelle_settings)
        threading.Thread(target=self.handlers.cache_handlers).start()
        self.handlers.add_tools(self.tools)
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

    def close_application(self):
        self.handlers.destroy()

    def wait_llm_loading(self):
        GLib.idle_add(self.ui_controller.set_model_loading, True)
        self.handlers.llm.load_model(None)
        GLib.idle_add(self.ui_controller.set_model_loading, False)

    def reload(self, reload_type: ReloadType):
        """Reload the specified settings

        Args:
            reload_type: type of reload
        """
        if reload_type == ReloadType.EXTENSIONS:
            self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                                   extension_cache=self.extensions_cache, settings=self.settings)
            self.extensionloader.load_extensions()
            restore_handlers()
            self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH)
            self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
            self.newelle_settings.load_prompts()
            self.extensionloader.add_tools(self.tools)
            self.handlers.extensionloader = self.extensionloader
            self.handlers.select_handlers(self.newelle_settings)
            self.extensionloader.set_ui_controller(self.ui_controller)
            print("Extensions reload")
        elif reload_type == ReloadType.LLM:
            self.handlers.llm.destroy()
            self.handlers.select_handlers(self.newelle_settings)
            GLib.timeout_add(50, threading.Thread(target=self.wait_llm_loading).start)
        elif reload_type == ReloadType.SECONDARY_LLM:
            self.handlers.select_handlers(self.newelle_settings)
            if self.newelle_settings.use_secondary_language_model:
                GLib.timeout_add(100,threading.Thread(target=self.handlers.secondary_llm.load_model, args=(None,)).start)
        elif reload_type in [ReloadType.TTS, ReloadType.STT, ReloadType.MEMORIES]:
            if ReloadType.MEMORIES:
                self.require_tool_update()
            self.handlers.select_handlers(self.newelle_settings)
        elif reload_type == ReloadType.RAG:
            self.handlers.select_handlers(self.newelle_settings)
            if self.newelle_settings.rag_on:
                GLib.timeout_add(400, threading.Thread(target=self.handlers.rag.load).start)
            self.require_tool_update()
        elif reload_type == ReloadType.EMBEDDINGS:
            self.handlers.select_handlers(self.newelle_settings)
            GLib.timeout_add(300, threading.Thread(target=self.handlers.embedding.load_model).start)
        elif reload_type == ReloadType.PROMPTS:
            return
        elif reload_type == ReloadType.WEBSEARCH:
            self.handlers.select_handlers(self.newelle_settings)
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

    def get_mcp_integration(self):
        if self.integrationsloader is not None:
            for integration in self.integrationsloader.get_extensions():
                if integration.id == "mcp":
                    return integration
        return None
    
    def update_mcp_tools(self):
        mcp_integration = self.get_mcp_integration()
        if mcp_integration is not None:
            mcp_integration.update_tools()
            self.tools.update_tools(mcp_integration.get_tools())

    def require_tool_update(self):
        self.tools = ToolRegistry()
        self.extensionloader.add_tools(self.tools)
        self.integrationsloader.add_tools(self.tools)
        # Add tools from memory and rag handlers
        self.handlers.add_tools(self.tools)
    
    def get_enabled_tools(self) -> list:
        """Get the list of enabled tools
        
        Returns:
            list[Tool]: List of enabled tools
        """
        enabled_tools = []
        tools_settings = self.newelle_settings.tools_settings_dict
        
        for tool in self.tools.get_all_tools():
            # Check if tool is explicitly enabled/disabled in settings
            is_enabled = tool.default_on
            if tool.name in tools_settings and "enabled" in tools_settings[tool.name]:
                is_enabled = tools_settings[tool.name]["enabled"]
            
            # Special case: search tool is disabled if websearch is off
            if tool.name == "search" and not self.newelle_settings.websearch_on:
                is_enabled = False
            
            if is_enabled:
                enabled_tools.append(tool)
        
        return enabled_tools
        
    def load_extensions(self):
        """Load extensions"""
        # Load extensions
        self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                               extension_cache=self.extensions_cache, settings=self.settings)
        self.extensionloader.load_extensions()
        self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH)
        self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
        self.extensionloader.add_tools(self.tools)
        self.set_ui_controller(self.ui_controller)

    def load_integrations(self):
        """Load integrations"""
        self.integrationsloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path, settings=self.settings, extension_cache=self.extensions_cache)
        self.integrationsloader.load_integrations(AVAILABLE_INTEGRATIONS)
        self.integrationsloader.add_tools(self.tools)
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

    def export_single_chat(self, chat_index):
        """Export a single chat to JSON format

        Args:
            chat_index: Index of the chat to export

        Returns:
            dict: Export data in JSON format
        """
        if chat_index < 0 or chat_index >= len(self.chats):
            return None

        chat_data = self.chats[chat_index]
        export_data = {
            "version": "1.0",
            "export_metadata": {
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "exported_by": "Newelle",
                "exported_from_version": "1.0.0",
                "format_version": "1.0",
                "export_type": "single_chat",
                "export_id": str(uuid_lib.uuid4())
            },
            "chat": {
                "name": chat_data["name"],
                "profile": chat_data.get("profile", None),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_modified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "message_count": len(chat_data["chat"]),
                "messages": chat_data["chat"]
            }
        }
        return export_data

    def export_all_chats(self):
        """Export all chats to JSON format

        Returns:
            dict: Export data in JSON format
        """
        chats_list = []
        for chat_data in self.chats:
            chat_entry = {
                "name": chat_data["name"],
                "profile": chat_data.get("profile", None),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_modified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "message_count": len(chat_data["chat"]),
                "messages": chat_data["chat"]
            }
            chats_list.append(chat_entry)

        export_data = {
            "version": "1.0",
            "export_metadata": {
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "exported_by": "Newelle",
                "exported_from_version": "1.0.0",
                "format_version": "1.0",
                "export_type": "multiple_chats",
                "chat_count": len(chats_list),
                "export_id": str(uuid_lib.uuid4())
            },
            "chats": chats_list
        }
        return export_data

    def import_chat(self, data):
        """Import chat(s) from JSON format

        Args:
            data: Dictionary containing chat export data

        Returns:
            tuple: (success: bool, message: str, imported_count: int)
        """
        # Validate required fields
        if "version" not in data or "export_metadata" not in data:
            return False, _("Invalid export format: missing required fields"), 0

        export_metadata = data["export_metadata"]
        export_type = export_metadata.get("export_type")

        if export_type == "single_chat":
            return self._import_single_chat(data)
        elif export_type == "multiple_chats":
            return self._import_multiple_chats(data)
        else:
            return False, _("Unknown export type"), 0

    def _import_single_chat(self, data):
        """Import a single chat from export data"""
        try:
            chat = data["chat"]
            name = chat.get("name", "Imported Chat")
            messages = chat.get("messages", [])
            profile = chat.get("profile")

            # Ensure name is unique
            counter = 1
            original_name = name
            while any(c["name"] == name for c in self.chats):
                name = f"{original_name} ({counter})"
                counter += 1

            # Create new chat
            new_chat = {
                "name": name,
                "chat": messages[:]
            }

            # Set profile if provided
            if profile is not None:
                new_chat["profile"] = profile

            self.chats.append(new_chat)
            self.save_chats()
            return True, _("Imported 1 chat successfully"), 1
        except Exception as e:
            return False, _("Error importing chat: {0}").format(str(e)), 0

    def _import_multiple_chats(self, data):
        """Import multiple chats from export data"""
        try:
            chats = data["chats"]
            imported_count = 0
            skipped_count = 0

            # Track existing chat names
            existing_names = {c["name"] for c in self.chats}

            for chat in chats:
                try:
                    name = chat.get("name", "Imported Chat")
                    messages = chat.get("messages", [])
                    profile = chat.get("profile")

                    # Ensure name is unique
                    if name in existing_names:
                        counter = 1
                        original_name = name
                        while name in existing_names:
                            name = f"{original_name} ({counter})"
                            counter += 1

                    # Create new chat
                    new_chat = {
                        "name": name,
                        "chat": messages[:]
                    }

                    # Set profile if provided
                    if profile is not None:
                        new_chat["profile"] = profile

                    self.chats.append(new_chat)
                    existing_names.add(name)
                    imported_count += 1
                except Exception:
                    skipped_count += 1
                    continue

            if imported_count > 0:
                self.save_chats()

            message = _("Imported {0} chat(s)").format(imported_count)
            if skipped_count > 0:
                message += _(" (skipped {0})").format(skipped_count)

            return True, message, imported_count
        except Exception as e:
            return False, _("Error importing chats: {0}").format(str(e)), 0

    def get_variable(self, name:str):
        tools = self.tools.get_all_tools()
        for tool in tools:
            if tool.name == name:
                if tool in self.get_enabled_tools():
                    return True
                else:
                    return False
        if name == "tts_on":
            return self.newelle_settings.tts_enabled
        elif name == "virtualization_on":
            return self.newelle_settings.virtualization
        elif name == "auto_run":
            return self.newelle_settings.auto_run
        elif name == "websearch_on":
            return self.newelle_settings.websearch_on
        elif name == "rag_on":
            return self.newelle_settings.rag_on_documents
        elif name == "local_folder":
            return self.newelle_settings.rag_on
        elif name == "automatic_stt":
            return self.newelle_settings.automatic_stt
        elif name == "profile_name":
            return self.newelle_settings.current_profile
        elif name == "external_browser":
            return self.newelle_settings.external_browser
        elif name == "history":
            return "\n".join([f"{msg['User']}: {msg['Message']}" for msg in self.get_history()])
        elif name == "message":
            return self.chat[-1]["Message"]
        else:
            rep = replace_variables_dict()
            var = "{" + name.upper() + "}"
            if var in rep:
                return rep[var]
            else:
                return None

    def get_history(
        self, chat=None, include_last_message=False, copy_chat=True
    ) -> list[dict[str, str]]:
        """Format the history excluding none messages and picking the right context size

        Args:
            chat (): chat history, if None current is taken

        Returns:
           chat history
        """
        if chat is None:
            chat = self.chat
        if copy_chat:
            chat = copy.deepcopy(chat)
        history = []
        count = self.newelle_settings.memory
        msgs = chat[:-1] if not include_last_message else chat
        msgs.reverse()
        for msg in msgs:
            if count == 0:
                break
            if msg["User"] == "Console" and msg["Message"] == "None":
                continue
            if self.newelle_settings.remove_thinking:
                msg["Message"] = remove_thinking_blocks(msg["Message"])
            if msg["User"] == "File" or msg["User"] == "Folder":
                msg["Message"] = f"```{msg['User'].lower()}\n{msg['Message'].strip()}\n```"
                msg["User"] = "User"
            history.insert(0,msg)
            count -= 1
        return history

    def get_memory_prompt(self, chat=None, chat_id=None):
        """Get memory and RAG context prompts.
        
        Args:
            chat: Optional chat messages list. If None, uses current chat.
            chat_id: Optional chat ID for document indexing. If None, uses current chat_id.
        """
        if chat is None:
            chat = self.chat
        if chat_id is None:
            chat_id = self.newelle_settings.chat_id
            
        r = []
        if self.newelle_settings.memory_on:
            r += self.handlers.memory.get_context(
                chat[-1]["Message"], self.get_history(chat=chat)
            )
        if self.newelle_settings.rag_on:
            r += self.handlers.rag.get_context(
                chat[-1]["Message"], self.get_history(chat=chat)
            )
        if (
            self.newelle_settings.rag_on_documents
            and self.handlers.rag is not None
        ):
            documents = extract_supported_files(
                self.get_history(chat=chat, include_last_message=True),
                self.handlers.rag.get_supported_files_reading(),
                self.handlers.llm.get_supported_files()
            )
            if len(documents) > 0:
                existing_index = self.chat_documents_index.get(chat_id, None)
                
                if self.ui_controller:
                     GLib.idle_add(self.ui_controller.add_reading_widget, documents)

                if existing_index is None:
                    existing_index = self.handlers.rag.build_index(documents)
                    self.chat_documents_index[chat_id] = existing_index
                else:
                    existing_index.update_index(documents)
                
                if existing_index.get_index_size() > self.newelle_settings.rag_limit: 
                    r += existing_index.query(
                        clean_prompt(chat[-1]["Message"])
                    )
                else:
                    r += existing_index.get_all_contexts()
                
                if self.ui_controller:
                    GLib.idle_add(self.ui_controller.remove_reading_widget)

        return r

    def update_memory(self, bot_response, chat=None):
        """Update memory with bot response.
        
        Args:
            bot_response: The bot's response message
            chat: Optional chat messages list. If None, uses current chat.
        """
        if chat is None:
            chat = self.chat
        if self.newelle_settings.memory_on:
            threading.Thread(
                target=self.handlers.memory.register_response,
                args=(bot_response, chat),
            ).start()

    def prepare_generation(self, chat_id=None):
        """Prepare contexts and prompts for generation.

        Args:
            chat_id: Optional chat ID to use. If None, uses current chat_id from settings.

        Returns:
            Tuple of (prompts, history, old_history, old_user_prompt, chat, effective_chat_id)
            Returns (None, None, None, None, None, None) if chat_id is invalid
        """
        # Use explicit chat_id or fall back to current
        effective_chat_id = chat_id if chat_id is not None else self.newelle_settings.chat_id

        # Validate chat_id is within bounds
        if not self.chats or effective_chat_id < 0 or effective_chat_id >= len(self.chats):
            print(f"prepare_generation: Invalid chat_id {effective_chat_id}, chats length: {len(self.chats) if self.chats else 0}")
            return None, None, None, None, None, None

        chat = self.get_chat_by_id(effective_chat_id)

        # Save profile for generation
        self.chats[effective_chat_id]["profile"] = self.newelle_settings.current_profile

        # Append extensions prompts
        prompts = []
        formatter = PromptFormatter(replace_variables_dict(), self.get_variable)
        for prompt in self.newelle_settings.bot_prompts:
            prompts.append(formatter.format(prompt))

        # Append memory
        prompts += self.get_memory_prompt(chat=chat, chat_id=effective_chat_id)

        # Set the history for the model
        history = self.get_history(chat=chat)
        # Let extensions preprocess the history
        old_history = copy.deepcopy(history)
        old_user_prompt = chat[-1]["Message"] if chat else ""
        processed_chat, prompts = self.integrationsloader.preprocess_history(chat, prompts)
        chat, prompts = self.extensionloader.preprocess_history(processed_chat, prompts)

        # Update the chat in storage if it was modified
        self.set_chat_by_id(effective_chat_id, chat)

        return prompts, history, old_history, old_user_prompt, chat, effective_chat_id


    def generate_response(self, stream_number_variable, update_callback, chat_id=None):
        """
        Generator for the response.
        Yields (status, data) tuples.
        status can be: 'stream', 'error', 'done', 'edited_messages'

        Args:
            stream_number_variable: Variable to track stream number for cancellation
            update_callback: Callback for streaming updates
            chat_id: Optional chat ID to use. If None, uses current chat_id from settings.
        """
        prompts, history, old_history, old_user_prompt, chat, effective_chat_id = self.prepare_generation(chat_id=chat_id)

        # Handle invalid chat_id
        if prompts is None:
            yield ('error', 'Invalid chat ID: the chat may have been deleted')
            return
        
        # Check for edited messages
        new_history = self.get_history(chat=chat)
        edited_messages = get_edited_messages(new_history, old_history)
        
        if edited_messages is None:
             if len(new_history) < len(old_history):
                 yield ('reload_chat', None)
        else:
             for message in edited_messages:
                 yield ('reload_message', message)
                 
        if len(chat) == 0:
            yield ('done', None)
            return

        if chat[-1]["Message"] != old_user_prompt:
             yield ('reload_message', len(chat) - 1)

        
        message_label = ""
        try:
            t1 = time.time()
            if self.handlers.llm.stream_enabled():
                message_label = self.handlers.llm.send_message_stream(
                    chat[-1]["Message"],
                    new_history,
                    prompts,
                    update_callback,
                    [stream_number_variable], 
                )
            else:
                 message_label = self.handlers.llm.send_message(chat[-1]["Message"], prompts, new_history)
            
            # Post-generation logic
            last_generation_time = time.time() - t1
            
            input_tokens = 0
            for prompt in prompts:
                input_tokens += count_tokens(prompt)
            for message in new_history:
                input_tokens += count_tokens(message.get("User", "")) + count_tokens(message.get("Message", ""))
            input_tokens += count_tokens(chat[-1]["Message"])
            
            output_tokens = count_tokens(message_label)
            
            message_label = clean_bot_response(message_label)

        except Exception as e:
            yield ('error', str(e))
            return

        # Post-processing
        old_history = copy.deepcopy(chat)
        chat, message_label = self.integrationsloader.postprocess_history(chat, message_label)
        chat, message_label = self.extensionloader.postprocess_history(chat, message_label)
        
        # Update the chat in storage if it was modified
        self.set_chat_by_id(effective_chat_id, chat)
        
        # Check for edited messages again
        edited_messages = get_edited_messages(chat, old_history)
        if edited_messages is None:
             if len(chat) < len(old_history):
                 yield ('reload_chat', None)
        else:
             for message in edited_messages:
                 yield ('reload_message', message)

        # Update memory
        self.update_memory(message_label, chat=chat)
        
        # Return final message and tokens
        yield ('finished', {
            'message': message_label,
            'prompts': prompts,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'time': last_generation_time
        })


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
        self.remember_profile = settings.get_boolean("remember-profile")
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
        self.secondary_stt_engine = settings.get_string("secondary-stt-engine")
        self.secondary_stt_settings = settings.get_string("stt-secondary-settings")
        self.secondary_stt_on = settings.get_boolean("secondary-stt-on")
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
        self.parallel_tool_execution = settings.get_boolean("parallel-tool-execution")
        self.external_browser = settings.get_boolean("external-browser")
        self.initial_browser_page = settings.get_string("initial-browser-page")
        self.browser_search_string = settings.get_string("browser-search-string")
        self.browser_session_persist = settings.get_boolean("browser-session-persist")
        self.editor_color_scheme = settings.get_string("editor-color-scheme")
        self.tools_settings = settings.get_string("tools-settings")
        self.tools_settings_dict = json.loads(self.tools_settings)
        self.mcp_servers = self.settings.get_string("mcp-servers")
        self.mcp_servers_dict = json.loads(self.mcp_servers)
        self.wakeword_enabled = settings.get_boolean("wakeword-on")
        self.wakeword = settings.get_string("wakeword")
        self.wakeword_vad_aggressiveness = settings.get_int("wakeword-vad-aggressiveness")
        self.wakeword_pre_buffer_duration = settings.get_double("wakeword-pre-buffer-duration")
        self.wakeword_silence_duration = settings.get_double("wakeword-silence-duration")
        self.wakeword_energy_threshold = settings.get_int("wakeword-energy-threshold")
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
        if self.secondary_stt_engine != new_settings.secondary_stt_engine or self.secondary_stt_on != new_settings.secondary_stt_on or self.secondary_stt_settings != new_settings.secondary_stt_settings:
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
        if self.mcp_servers != new_settings.mcp_servers or self.tools_settings != new_settings.tools_settings:
            reloads.append(ReloadType.TOOLS)
        # Check wakeword settings
        if (self.wakeword_enabled != new_settings.wakeword_enabled or
            self.wakeword != new_settings.wakeword or
            self.wakeword_vad_aggressiveness != new_settings.wakeword_vad_aggressiveness or
            self.wakeword_pre_buffer_duration != new_settings.wakeword_pre_buffer_duration or
            self.wakeword_silence_duration != new_settings.wakeword_silence_duration or
            self.wakeword_energy_threshold != new_settings.wakeword_energy_threshold):
            reloads.append(ReloadType.WAKEWORD)
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
    def __init__(self, settings: Gio.Settings, extensionloader : ExtensionLoader, models_path, integrations: ExtensionLoader, installing_handlers: dict):
        self.settings = settings
        self.extensionloader = extensionloader
        self.directory = models_path
        self.handlers =  {}
        self.handlers_cached = threading.Semaphore()
        self.handlers_cached.acquire()
        self.integrationsloader = integrations
        self.installing_handlers = installing_handlers
        self.secondary_stt = None

    def destroy(self):
        for handler in self.handlers.values():
            handler.destroy()

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
        if newelle_settings.secondary_stt_engine not in AVAILABLE_STT:
            # Find first secondary-capable STT
            for key in AVAILABLE_STT:
                if "secondary" in AVAILABLE_STT[key] and AVAILABLE_STT[key]["secondary"]:
                    newelle_settings.secondary_stt_engine = key
                    break
            else:
                # Fallback to first STT if none are marked as secondary
                newelle_settings.secondary_stt_engine = list(AVAILABLE_STT.keys())[0]
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
        if newelle_settings.secondary_stt_on:
            self.secondary_stt : STTHandler = self.get_object(AVAILABLE_STT, newelle_settings.secondary_stt_engine, True)
        else:
            self.secondary_stt : STTHandler = None
        self.tts : TTSHandler = self.get_object(AVAILABLE_TTS, newelle_settings.tts_program)
        self.embedding : EmbeddingHandler= self.get_object(AVAILABLE_EMBEDDINGS, newelle_settings.embedding_model)
        self.memory : MemoryHandler = self.get_object(AVAILABLE_MEMORIES, newelle_settings.memory_model)
        self.memory.set_memory_size(newelle_settings.memory)
        self.rag : RAGHandler = self.get_object(AVAILABLE_RAGS, newelle_settings.rag_model)
        self.websearch : WebSearchHandler = self.get_object(AVAILABLE_WEBSEARCH, newelle_settings.websearch_model)
        # Assign handlers 
        self.integrationsloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.extensionloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.memory.set_handlers(self.secondary_llm, self.embedding, self.rag)

        self.rag.set_handlers(self.llm, self.embedding)
        threading.Thread(target=self.install_missing_handlers).start()

    def set_error_func(self, func):
        def async_set():
            self.handlers_cached.acquire()
            self.handlers_cached.release()
            for handler in self.handlers.values():
                handler.set_error_func(func)
        threading.Thread(target=async_set).start()

    def add_tools(self, tools: ToolRegistry):
        if self.memory is not None:
            for tool in self.memory.get_tools():
                tools.register_tool(tool)
        for tool in self.rag.get_tools():
            tools.register_tool(tool)

    def load_handlers(self):
        """Load handlers"""
        threading.Thread(target=self.llm.load_model, args=(None,)).start()
        if self.settings.get_boolean("secondary-llm-on"):
            self.secondary_llm.load_model(None)
        self.embedding.load_model()
        if self.settings.get_boolean("rag-on"):
            self.rag.load()
        

    def install_missing_handlers(self):
        """Install selected handlers that are not installed. Assumes that select_handlers has been called""" 
        handlers = [self.llm, self.stt, self.tts, self.memory, 
                    self.embedding, self.rag, self.websearch]
        for handler in handlers:
            if not handler.is_installed():
                self.set_installing(handler, True)
                handler.install()
                self.set_installing(handler, False)

    def set_installing(self, handler: Handler, status: bool):
        """Set installing status"""
        self.installing_handlers[(handler.key, handler.schema_key)] = status

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
        # Secondary STTs
        for key in AVAILABLE_STT:
            self.handlers[(key, self.convert_constants(AVAILABLE_STT), True)] = self.get_object(AVAILABLE_STT, key, True)
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
            model.set_secondary_settings(secondary)
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
