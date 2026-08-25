from typing import Any, Callable
from gi.repository import GLib, Gio, Adw
import os
import base64
import time
import re
import copy

from .tools import Tool, ToolRegistry, ToolResult
from .skills import SkillManager
from .modes import ModeManager
from .utility.media import chat_contains_vision, get_image_base64, get_image_path, extract_supported_files
from .utility.message_chunk import get_message_chunks

from .extensions import NewelleExtension
from .handlers.llm import LLMHandler
from .handlers.tts import TTSHandler
from .handlers.stt import STTHandler
from .handlers.rag import RAGHandler
from .handlers.memory import MemoryHandler
from .handlers.embeddings import EmbeddingHandler
from .handlers.websearch import WebSearchHandler
from .handlers.image_generator import ImageGeneratorHandler
from .handlers.interfaces.interface import Interface

from .utility.system import is_flatpak
from .utility.pip import install_module
from .utility.profile_settings import get_settings_dict_by_groups
from .utility.source_attribution import format_source_context
from .constants import AVAILABLE_INTEGRATIONS, AVAILABLE_WEBSEARCH, AVAILABLE_IMAGE_GENERATORS, DIR_NAME, SCHEMA_ID, PROMPTS, AVAILABLE_STT, AVAILABLE_TTS, AVAILABLE_LLMS, AVAILABLE_RAGS, AVAILABLE_PROMPTS, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_INTERFACES, SETTINGS_GROUPS, restore_handlers, restore_prompts
import threading
import pickle
import tempfile
import json
import datetime
import uuid as uuid_lib
from .extensions import ExtensionLoader
from .utility import override_prompts
from .utility.strings import clean_bot_response, clean_prompt, count_tokens, extract_reasoning_content, get_edited_messages
from .utility.context_manager import ContextManager, TrimResult
from .utility.replacehelper import PromptFormatter, replace_variables_dict
from enum import Enum 
from .handlers import Handler
from .handlers.handler import SettingsCache
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
    IMAGE_GENERATOR = 16 
    COMPACT_MODE = 17
    COMPACT_INPUT_BAR = 18

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
    def chat_ids_ordered(self):
        """Return chat IDs in stable chronological order (sorted by ID)."""
        if not hasattr(self, 'chats') or not self.chats:
            return []
        return sorted(self.chats.keys())

    def _get_fallback_chat_id(self):
        """Return first available chat_id when current is invalid."""
        if not self.chats:
            return None
        return min(self.chats.keys())

    @property
    def chat(self):
        """Get the current chat messages list"""
        if hasattr(self, 'chats') and hasattr(self, 'newelle_settings'):
            chat_id = self.newelle_settings.chat_id
            if chat_id in self.chats:
                return self.chats[chat_id]["chat"]
            fallback = self._get_fallback_chat_id()
            if fallback is not None:
                return self.chats[fallback]["chat"]
        return []

    @chat.setter
    def chat(self, value):
        """Set the current chat messages list"""
        if hasattr(self, 'chats') and hasattr(self, 'newelle_settings'):
            chat_id = self.newelle_settings.chat_id
            if chat_id in self.chats:
                self.chats[chat_id]["chat"] = value
            else:
                fallback = self._get_fallback_chat_id()
                if fallback is not None:
                    self.chats[fallback]["chat"] = value
    
    def get_chat_by_id(self, chat_id):
        """Get chat messages list by explicit chat_id.

        Args:
            chat_id: The chat ID to get messages for

        Returns:
            The chat messages list for the specified chat_id, or empty list if invalid
        """
        if hasattr(self, 'chats') and self.chats and chat_id in self.chats:
            return self.chats[chat_id]["chat"]
        return []
    
    def set_chat_by_id(self, chat_id, value):
        """Set chat messages list by explicit chat_id.

        Args:
            chat_id: The chat ID to set messages for
            value: The new chat messages list
        """
        if hasattr(self, 'chats') and self.chats and chat_id in self.chats:
            self.chats[chat_id]["chat"] = value

    def append_chat_message(self, chat_id: int, message: dict) -> bool:
        """Append a message to a specific chat, regardless of selected tab.

        Tool continuations may run in background chats or across a mode switch.
        Those callers must not use ``self.chat``, whose target follows the
        globally selected chat in the current settings.
        """
        if not hasattr(self, "chats") or chat_id not in self.chats:
            return False
        self.chats[chat_id]["chat"].append(message)
        return True

    def get_console_reply(self, chat_id, id_message):
        """Get existing console reply from chat history if available."""
        if not hasattr(self, 'chats') or not self.chats or chat_id not in self.chats:
            return None
        chat = self.chats[chat_id]["chat"]
        idx = min(id_message, len(chat) - 1)
        if idx >= 0 and chat[idx].get("User") == "Console":
            return chat[idx]["Message"]
        return None

    def get_tool_response(self, chat_id, id_message, tool_name, tool_uuid):
        """Get existing tool response from chat history by tool name and UUID."""
        if not hasattr(self, 'chats') or not self.chats or chat_id not in self.chats:
            return None
        chat = self.chats[chat_id]["chat"]
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
        if not hasattr(self, 'chats') or not self.chats or chat_id not in self.chats:
            return str(uuid_lib.uuid4())[:8]
        chat = self.chats[chat_id]["chat"]
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
        self.tools = ToolRegistry()
        # Tool names whose full schema has been fetched via tool_search. They are
        # emitted with full parameters afterwards so lazy-loaded tools can also be
        # invoked through native tool calling (which needs the schema up front).
        self.expanded_tools: set[str] = set()
        self.msgid = 0
        self.chat_documents_index = {}
        self.is_call_request = False
        self.scheduled_tasks = []
        self.scheduled_tasks_lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.scheduler_source_id = None

    def ui_init(self):
        """Init necessary variables for the UI and load models and handlers"""
        self.init_paths()
        self.check_path_integrity()
        skills_dirs = self._build_skills_dirs()
        self.skill_manager = SkillManager(skills_dirs, self.settings)
        self.skill_manager.discover()
        self.load_integrations()
        self.load_extensions()
        # Modes: an overlay on top of the active profile that can override
        # prompts (enable/disable + text replacement) and force-enable/remove
        # tools & skills. It must exist before loading prompts so the first
        # prompt build already reflects the active mode.
        self.mode_manager = ModeManager(self.settings)
        # Merge any modes contributed by already-loaded extensions.
        self.extensionloader.add_modes(self.mode_manager)
        self.skill_manager.set_mode_overrides(self.mode_manager.get_active_mode()["skills"])
        self.newelle_settings = NewelleSettings(self.mode_manager)
        self.newelle_settings.load_settings(self.settings)
        self.load_chats(self.newelle_settings.chat_id)
        self.handlers = HandlersManager(
            self.settings,
            self.extensionloader,
            self.models_dir,
            self.integrationsloader,
            self,
        )
        self.handlers.select_handlers(self.newelle_settings)
        threading.Thread(target=self.handlers.cache_handlers).start()
        self.handlers.add_tools(self.tools)
        self.load_scheduled_tasks()
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
        self.skills_path = os.path.join(self.config_dir, "skills")

    def _build_skills_dirs(self):
        """Build the list of skill directories to search.

        Priority order:
          1. Project/.newelle/skills/  (client-native project location)
          2. Project/.agents/skills/   (cross-client project location)
          3. User~/.newelle/skills/    (client-native user location)
          4. User~/.agents/skills/     (cross-client user location)
        """
        dirs = []

        # Project-level directories (relative to current working directory / main_path)
        main_path = self.settings.get_string("path")
        if main_path:
            project_dir = os.path.expanduser(main_path)
            if os.path.isdir(project_dir):
                dirs.append(os.path.join(project_dir, ".newelle", "skills"))
                dirs.append(os.path.join(project_dir, ".agents", "skills"))

        # User-level directories
        dirs.append(self.skills_path)  # ~/.config/Newelle/skills (client-native)

        user_home = os.path.expanduser("~")
        dirs.append(os.path.join(user_home, ".agents", "skills"))  # ~/.agents/skills (cross-client)

        return dirs

    def set_ui_controller(self, ui_controller):
        """Set add tab function"""
        if ui_controller is not None:
            self.ui_controller = ui_controller
            self.extensionloader.set_ui_controller(ui_controller)
            self.integrationsloader.set_ui_controller(ui_controller)

    def _ensure_chats_dict(self, raw):
        """Convert loaded data to dict format. Handles retrocompatibility with old list format."""
        if isinstance(raw, dict) and "chats" in raw:
            self.chats = raw["chats"]
            self.next_chat_id = raw.get(
                "next_chat_id",
                max(self.chats.keys(), default=0) + 1
            )
            self.folders = raw.get("folders", {})
            self.next_folder_id = raw.get("next_folder_id", 0)
            return
        # Old list format
        self.chats = {i: entry for i, entry in enumerate(raw)}
        self.next_chat_id = len(self.chats)
        self.folders = {}
        self.next_folder_id = 0

    def load_chats(self, chat_id):
        """Load chats"""
        self.filename = "chats.pkl"
        if os.path.exists(self.chats_path):
            with open(self.chats_path, 'rb') as f:
                raw = pickle.load(f)
            self._ensure_chats_dict(raw)
        else:
            self.chats = {0: {"name": _("Chat ") + "1", "chat": []}}
            self.next_chat_id = 1
            self.folders = {}
            self.next_folder_id = 0

        # Validate chat_id: if not in chats, use first available
        if self.chats and hasattr(self, 'newelle_settings'):
            if self.newelle_settings.chat_id not in self.chats:
                self.newelle_settings.chat_id = min(self.chats.keys())

    def save_chats(self):
        """Save chats without exposing a partially written storage file."""
        with self.save_lock:
            storage_dir = os.path.dirname(self.chats_path) or "."
            storage_name = os.path.basename(self.chats_path)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=storage_dir,
                    prefix=f".{storage_name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary_file:
                    temporary_path = temporary_file.name
                    pickle.dump({
                        "chats": self.chats,
                        "next_chat_id": self.next_chat_id,
                        "folders": self.folders,
                        "next_folder_id": self.next_folder_id,
                    }, temporary_file)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())

                os.replace(temporary_path, self.chats_path)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass

    def create_call_chat(self):
        """Create a new call chat that won't be displayed in the chat list"""
        chat_id = self.next_chat_id
        self.next_chat_id += 1
        new_chat = {
            "name": _("Call ") + str(chat_id),
            "chat": [],
            "call": True
        }
        self.chats[chat_id] = new_chat
        self.save_chats()
        return chat_id

    def create_visible_chat(self, name: str | None = None, profile: str | None = None, folder_id: int | None = None):
        """Create a new visible chat entry and refresh history."""
        chat_id = self.next_chat_id
        self.next_chat_id += 1
        if name is None:
            name = _("Chat %d") % chat_id
        new_chat = {
            "name": name,
            "chat": [],
            "id": str(uuid_lib.uuid4()),
            "branched_from": None,
        }
        if profile is not None:
            new_chat["profile"] = profile
        self.chats[chat_id] = new_chat
        self.save_chats()
        if folder_id is not None and folder_id in self.folders:
            self.move_chat_to_folder(chat_id, folder_id)
        elif self.ui_controller is not None:
            GLib.idle_add(self.ui_controller.update_history)
        return chat_id

    def create_folder(self, name: str, color: str, icon: str = "folder-symbolic") -> int:
        """Create a new chat folder and return its ID."""
        folder_id = self.next_folder_id
        self.next_folder_id += 1
        self.folders[folder_id] = {
            "name": name,
            "color": color,
            "icon": icon,
            "chat_ids": [],
            "expanded": True,
        }
        self.save_chats()
        if self.ui_controller is not None:
            GLib.idle_add(self.ui_controller.update_history)
        return folder_id

    def ensure_scheduled_tasks_folder(self) -> int:
        """Ensure the 'Scheduled Tasks' folder exists, creating it if needed."""
        folder_name = _("Scheduled Tasks")
        for folder_id, folder in self.folders.items():
            if folder["name"] == folder_name:
                return folder_id
        return self.create_folder(folder_name, "#3584e4", "alarm-symbolic")

    def rename_folder(self, folder_id: int, name: str):
        """Rename an existing folder."""
        if folder_id in self.folders:
            self.folders[folder_id]["name"] = name
            self.save_chats()

    def update_folder_color(self, folder_id: int, color: str):
        """Change the color of a folder."""
        if folder_id in self.folders:
            self.folders[folder_id]["color"] = color
            self.save_chats()

    def update_folder_icon(self, folder_id: int, icon: str):
        """Change the icon of a folder."""
        if folder_id in self.folders:
            self.folders[folder_id]["icon"] = icon
            self.save_chats()

    def delete_folder(self, folder_id: int):
        """Delete a folder. Chats inside are moved back to top level."""
        if folder_id in self.folders:
            del self.folders[folder_id]
            self.save_chats()
            if self.ui_controller is not None:
                GLib.idle_add(self.ui_controller.update_history)

    def toggle_folder_expanded(self, folder_id: int):
        """Toggle the expanded/collapsed state of a folder."""
        if folder_id in self.folders:
            self.folders[folder_id]["expanded"] = not self.folders[folder_id]["expanded"]
            self.save_chats()

    def move_chat_to_folder(self, chat_id: int, folder_id: int):
        """Move a chat into a folder, removing it from any previous folder."""
        self.remove_chat_from_folder(chat_id, save=False)
        if folder_id in self.folders:
            if chat_id not in self.folders[folder_id]["chat_ids"]:
                self.folders[folder_id]["chat_ids"].append(chat_id)
            self.save_chats()
            if self.ui_controller is not None:
                GLib.idle_add(self.ui_controller.update_history)

    def remove_chat_from_folder(self, chat_id: int, save: bool = True):
        """Remove a chat from whichever folder it belongs to."""
        for folder in self.folders.values():
            if chat_id in folder["chat_ids"]:
                folder["chat_ids"].remove(chat_id)
                if save:
                    self.save_chats()
                    if self.ui_controller is not None:
                        GLib.idle_add(self.ui_controller.update_history)
                return

    def get_folder_for_chat(self, chat_id: int):
        """Return the folder_id containing this chat, or None."""
        for fid, folder in self.folders.items():
            if chat_id in folder["chat_ids"]:
                return fid
        return None

    def _parse_scheduled_datetime(self, value: str, allow_past: bool = True) -> datetime.datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(_("A valid ISO date/time is required."))
        try:
            parsed = datetime.datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(_("Invalid date/time. Use ISO 8601, for example 2026-03-08T14:30.")) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        if not allow_past and parsed <= datetime.datetime.now(parsed.tzinfo):
            raise ValueError(_("Scheduled time must be in the future."))
        return parsed

    def _format_scheduled_chat_name(self, task: dict) -> str:
        summary = task["task"].strip().splitlines()[0][:48]
        if len(task["task"].strip().splitlines()[0]) > 48:
            summary += "..."
        return _("⏰ Scheduled: {0}").format(summary)

    def _normalize_cron_value(self, value: int, field_name: str) -> int:
        if field_name == "weekday" and value == 7:
            return 0
        return value

    def _parse_cron_field(self, raw_field: str, minimum: int, maximum: int, field_name: str) -> tuple[set[int], bool]:
        field = raw_field.strip()
        if not field:
            raise ValueError(_("Cron fields cannot be empty."))
        if field == "*":
            return set(range(minimum, maximum + 1)), True

        values: set[int] = set()
        for part in field.split(","):
            token = part.strip()
            if not token:
                raise ValueError(_("Cron fields cannot contain empty values."))
            if "/" in token:
                base, step_str = token.split("/", 1)
                try:
                    step = int(step_str)
                except ValueError as exc:
                    raise ValueError(_("Cron step values must be integers.")) from exc
                if step <= 0:
                    raise ValueError(_("Cron step values must be greater than zero."))
            else:
                base = token
                step = 1

            if base == "*":
                start = minimum
                end = maximum
            elif "-" in base:
                start_str, end_str = base.split("-", 1)
                try:
                    start = int(start_str)
                    end = int(end_str)
                except ValueError as exc:
                    raise ValueError(_("Cron ranges must contain integers.")) from exc
            else:
                try:
                    start = int(base)
                    end = start
                except ValueError as exc:
                    raise ValueError(_("Cron values must be integers, ranges, steps, or *.")) from exc

            if start > end:
                raise ValueError(_("Cron ranges must be ascending."))
            for candidate in range(start, end + 1, step):
                normalized = self._normalize_cron_value(candidate, field_name)
                if normalized < minimum or normalized > maximum:
                    raise ValueError(_("Cron value {0} is out of range.").format(candidate))
                values.add(normalized)

        if not values:
            raise ValueError(_("Cron field resolved to an empty set."))
        return values, False

    def _parse_cron_expression(self, cron_expression: str) -> dict:
        fields = cron_expression.strip().split()
        if len(fields) != 5:
            raise ValueError(_("Cron expressions must contain exactly 5 fields: minute hour day month weekday."))

        minute, minute_any = self._parse_cron_field(fields[0], 0, 59, "minute")
        hour, hour_any = self._parse_cron_field(fields[1], 0, 23, "hour")
        day, day_any = self._parse_cron_field(fields[2], 1, 31, "day")
        month, month_any = self._parse_cron_field(fields[3], 1, 12, "month")
        weekday, weekday_any = self._parse_cron_field(fields[4], 0, 6, "weekday")

        return {
            "expression": " ".join(fields),
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "weekday": weekday,
            "day_any": day_any,
            "weekday_any": weekday_any,
            "minute_any": minute_any,
            "hour_any": hour_any,
            "month_any": month_any,
        }

    def _cron_weekday(self, dt: datetime.datetime) -> int:
        return (dt.weekday() + 1) % 7

    def _cron_matches(self, parsed_cron: dict, dt: datetime.datetime) -> bool:
        if dt.minute not in parsed_cron["minute"]:
            return False
        if dt.hour not in parsed_cron["hour"]:
            return False
        if dt.month not in parsed_cron["month"]:
            return False

        day_match = dt.day in parsed_cron["day"]
        weekday_match = self._cron_weekday(dt) in parsed_cron["weekday"]
        if parsed_cron["day_any"] and parsed_cron["weekday_any"]:
            day_ok = True
        elif parsed_cron["day_any"]:
            day_ok = weekday_match
        elif parsed_cron["weekday_any"]:
            day_ok = day_match
        else:
            day_ok = day_match or weekday_match
        return day_ok

    def _get_next_cron_run(self, cron_expression: str, after_dt: datetime.datetime | None = None) -> datetime.datetime:
        parsed_cron = self._parse_cron_expression(cron_expression)
        if after_dt is None:
            after_dt = datetime.datetime.now().astimezone()
        elif after_dt.tzinfo is None:
            after_dt = after_dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)

        candidate = after_dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        # One year of minute-level search keeps the implementation dependency-free.
        for _ in range(366 * 24 * 60):
            if self._cron_matches(parsed_cron, candidate):
                return candidate
            candidate += datetime.timedelta(minutes=1)
        raise ValueError(_("Unable to compute the next run for this cron expression within one year."))

    def _normalize_scheduled_task(self, task: dict, now: datetime.datetime | None = None) -> dict:
        if now is None:
            now = datetime.datetime.now().astimezone()
        if not isinstance(task, dict):
            raise ValueError(_("Scheduled task data must be an object."))

        task_text = str(task.get("task", "")).strip()
        if not task_text:
            raise ValueError(_("Scheduled tasks require a task description."))

        has_run_at = bool(task.get("run_at"))
        has_cron = bool(task.get("cron"))
        if has_run_at == has_cron:
            raise ValueError(_("Provide either run_at or cron for a scheduled task."))

        normalized = {
            "id": str(task.get("id") or uuid_lib.uuid4()),
            "task": task_text,
            "schedule_type": "once" if has_run_at else "cron",
            "run_at": None,
            "cron": None,
            "enabled": bool(task.get("enabled", True)),
            "created_at": task.get("created_at") or now.isoformat(),
            "last_run_at": None,
            "next_run_at": None,
            "latest_chat_id": task.get("latest_chat_id"),
            "last_run_status": task.get("last_run_status"),
            "last_error": task.get("last_error"),
            "running": False,
            "folder_id": task.get("folder_id"),
        }

        if task.get("last_run_at"):
            normalized["last_run_at"] = self._parse_scheduled_datetime(task["last_run_at"]).isoformat()

        if normalized["schedule_type"] == "once":
            run_at = self._parse_scheduled_datetime(task["run_at"])
            normalized["run_at"] = run_at.isoformat()
            if normalized["enabled"] and run_at > now:
                normalized["next_run_at"] = run_at.isoformat()
            else:
                normalized["enabled"] = False
        else:
            parsed = self._parse_cron_expression(str(task["cron"]))
            normalized["cron"] = parsed["expression"]
            if normalized["enabled"]:
                normalized["next_run_at"] = self._get_next_cron_run(parsed["expression"], now).isoformat()

        if normalized["latest_chat_id"] is not None:
            try:
                normalized["latest_chat_id"] = int(normalized["latest_chat_id"])
            except (TypeError, ValueError):
                normalized["latest_chat_id"] = None

        return normalized

    def _persist_scheduled_tasks(self):
        with self.scheduled_tasks_lock:
            payload = json.dumps(self.scheduled_tasks)
        self.settings.set_string("scheduled-tasks", payload)

    def load_scheduled_tasks(self):
        raw_value = self.settings.get_string("scheduled-tasks")
        try:
            loaded_tasks = json.loads(raw_value) if raw_value else []
        except json.JSONDecodeError:
            loaded_tasks = []

        now = datetime.datetime.now().astimezone()
        normalized_tasks = []
        for entry in loaded_tasks:
            try:
                normalized_tasks.append(self._normalize_scheduled_task(entry, now))
            except ValueError:
                continue

        with self.scheduled_tasks_lock:
            self.scheduled_tasks = normalized_tasks
        self._persist_scheduled_tasks()

    def get_scheduled_tasks(self) -> list[dict]:
        with self.scheduled_tasks_lock:
            tasks = copy.deepcopy(self.scheduled_tasks)
        tasks.sort(key=lambda task: (task["next_run_at"] is None, task["next_run_at"] or task["created_at"]))
        return tasks

    def start_scheduler(self):
        if self.scheduler_source_id is None:
            self.scheduler_source_id = GLib.timeout_add_seconds(10, self._scheduler_tick)
        self._scheduler_tick()

    def stop_scheduler(self):
        if self.scheduler_source_id is not None:
            GLib.source_remove(self.scheduler_source_id)
            self.scheduler_source_id = None

    def create_scheduled_task(self, task: str, run_at: str | None = None, cron: str | None = None, folder_id: int | None = None) -> dict:
        now = datetime.datetime.now().astimezone()
        if folder_id is None:
            folder_id = self.ensure_scheduled_tasks_folder()
        scheduled_task = self._normalize_scheduled_task(
            {
                "task": task,
                "run_at": run_at,
                "cron": cron,
                "enabled": True,
                "created_at": now.isoformat(),
                "folder_id": folder_id,
            },
            now,
        )
        with self.scheduled_tasks_lock:
            self.scheduled_tasks.append(scheduled_task)
        self._persist_scheduled_tasks()
        return copy.deepcopy(scheduled_task)

    def set_scheduled_task_enabled(self, task_id: str, enabled: bool) -> bool:
        now = datetime.datetime.now().astimezone()
        changed = False
        with self.scheduled_tasks_lock:
            for task in self.scheduled_tasks:
                if task["id"] != task_id:
                    continue
                task["running"] = False
                if enabled:
                    task["enabled"] = True
                    if task["schedule_type"] == "once":
                        run_at = self._parse_scheduled_datetime(task["run_at"])
                        if run_at > now:
                            task["next_run_at"] = run_at.isoformat()
                        else:
                            task["enabled"] = False
                            task["next_run_at"] = None
                    else:
                        task["next_run_at"] = self._get_next_cron_run(task["cron"], now).isoformat()
                else:
                    task["enabled"] = False
                    task["next_run_at"] = None
                changed = True
                break
        if changed:
            self._persist_scheduled_tasks()
        return changed

    def delete_scheduled_task(self, task_id: str) -> bool:
        with self.scheduled_tasks_lock:
            original_len = len(self.scheduled_tasks)
            self.scheduled_tasks = [task for task in self.scheduled_tasks if task["id"] != task_id]
            changed = len(self.scheduled_tasks) != original_len
        if changed:
            self._persist_scheduled_tasks()
        return changed

    def set_scheduled_task_folder(self, task_id: str, folder_id: int) -> bool:
        """Change the folder for a scheduled task."""
        changed = False
        with self.scheduled_tasks_lock:
            for task in self.scheduled_tasks:
                if task["id"] != task_id:
                    continue
                if task.get("folder_id") != folder_id:
                    task["folder_id"] = folder_id
                    changed = True
                break
        if changed:
            self._persist_scheduled_tasks()
        return changed

    def get_scheduled_task_folder_id(self, task_id: str) -> int | None:
        """Get the folder ID for a scheduled task."""
        with self.scheduled_tasks_lock:
            for task in self.scheduled_tasks:
                if task["id"] == task_id:
                    return task.get("folder_id")
        return None

    def _scheduler_tick(self):
        now = datetime.datetime.now().astimezone()
        due_tasks = []
        with self.scheduled_tasks_lock:
            changed = False
            for task in self.scheduled_tasks:
                if not task.get("enabled") or task.get("running") or not task.get("next_run_at"):
                    continue
                next_run = self._parse_scheduled_datetime(task["next_run_at"])
                if next_run > now:
                    continue

                task["running"] = True
                task["last_error"] = None
                due_tasks.append(copy.deepcopy(task))
                if task["schedule_type"] == "once":
                    task["enabled"] = False
                    task["next_run_at"] = None
                else:
                    task["next_run_at"] = self._get_next_cron_run(task["cron"], next_run).isoformat()
                changed = True

            if changed:
                self.settings.set_string("scheduled-tasks", json.dumps(self.scheduled_tasks))

        for task in due_tasks:
            threading.Thread(target=self._run_scheduled_task, args=(task,), daemon=True).start()
        return True

    def _run_scheduled_task(self, task: dict):
        chat_id = None
        status = "completed"
        error_message = None
        folder_id = task.get("folder_id")
        try:
            chat_id = self.create_visible_chat(
                name=self._format_scheduled_chat_name(task),
                profile=self.newelle_settings.current_profile,
                folder_id=folder_id,
            )
            self.run_llm_with_tools(
                message=task["task"],
                chat_id=chat_id,
                max_tool_calls=self.newelle_settings.max_tool_calls,
                save_chat=True,
                force_tools_on_main_thread=True,
            )
            if self.ui_controller is not None:
                GLib.idle_add(self._show_scheduled_task_toast, _("Scheduled task completed"))
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
            if chat_id is not None:
                self.chats[chat_id]["chat"].append(
                    {"User": "Console", "Message": _("Scheduled task error: {0}").format(error_message)}
                )
                self.save_chats()
            if self.ui_controller is not None:
                GLib.idle_add(self._show_scheduled_task_toast, _("Scheduled task failed"))
        finally:
            finished_at = datetime.datetime.now().astimezone().isoformat()
            with self.scheduled_tasks_lock:
                for stored_task in self.scheduled_tasks:
                    if stored_task["id"] != task["id"]:
                        continue
                    stored_task["running"] = False
                    stored_task["last_run_at"] = finished_at
                    stored_task["latest_chat_id"] = chat_id
                    stored_task["last_run_status"] = status
                    stored_task["last_error"] = error_message
                    break
                self.settings.set_string("scheduled-tasks", json.dumps(self.scheduled_tasks))

    def _show_scheduled_task_toast(self, title: str):
        if self.ui_controller is None:
            return False
        self.ui_controller.send_notification(title)
        return False

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
        if not os.path.exists(self.skills_path):
            os.makedirs(self.skills_path, exist_ok=True)
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
        newsettings = NewelleSettings(self.mode_manager)
        newsettings.load_settings(self.settings)
        reload = self.newelle_settings.compare_settings(newsettings)
        if apply:
            self.newelle_settings = newsettings
            for r in reload:
                self.reload(r)
        return reload

    def close_application(self):
        self.stop_scheduler()
        self.handlers.destroy()

    def wait_llm_loading(self):
        GLib.idle_add(self.ui_controller.set_model_loading, True)
        self.handlers.llm.load_model(None)
        GLib.idle_add(self.ui_controller.set_model_loading, False)

    def reload_extensions(self, extension_ids=None) -> set[str]:
        """Reload extensions and refresh only the resources they contribute.

        Args:
            extension_ids: Optional IDs known to have changed. When omitted,
                additions, removals, and enabled-state changes are detected.

        Returns:
            The resource types that were refreshed.
        """
        old_loader = self.extensionloader
        old_ids = set(old_loader.extensionsmap)
        old_enabled = {
            extension.id for extension in old_loader.get_enabled_extensions()
        }

        new_loader = ExtensionLoader(
            self.extension_path,
            pip_path=self.pip_path,
            extension_cache=self.extensions_cache,
            settings=self.settings,
        )
        new_loader.load_extensions()
        if self.ui_controller is not None:
            new_loader.set_ui_controller(self.ui_controller)

        new_ids = set(new_loader.extensionsmap)
        new_enabled = {
            extension.id for extension in new_loader.get_enabled_extensions()
        }
        if extension_ids is None:
            affected_ids = (old_ids ^ new_ids) | (old_enabled ^ new_enabled)
            # ReloadType.EXTENSIONS is also used after replacing an extension
            # file in place. In that case IDs alone cannot identify the change.
            if not affected_ids:
                affected_ids = old_ids | new_ids
        else:
            affected_ids = set(extension_ids)

        refreshes = old_loader.get_contribution_types(
            affected_ids, include_disabled=True
        )
        refreshes.update(
            new_loader.get_contribution_types(
                affected_ids, include_disabled=True
            )
        )
        old_handlers = old_loader.get_handler_contributions(
            affected_ids, include_disabled=True
        )
        new_handlers = new_loader.get_handler_contributions(
            affected_ids, include_disabled=True
        )
        if old_handlers["llm"] or new_handlers["llm"]:
            refreshes.add("llm_handlers")

        self.extensionloader = new_loader
        self.handlers.extensionloader = new_loader
        self.newelle_settings.extensions_settings = self.settings.get_string(
            "extensions-settings"
        )

        active_handlers_changed = False
        if refreshes & {"handlers", "interfaces"}:
            # Rebuilding these descriptor dictionaries is cheap; actual handler
            # instances are invalidated selectively below.
            restore_handlers()
            new_loader.add_handlers(
                AVAILABLE_LLMS,
                AVAILABLE_TTS,
                AVAILABLE_STT,
                AVAILABLE_MEMORIES,
                AVAILABLE_EMBEDDINGS,
                AVAILABLE_RAGS,
                AVAILABLE_WEBSEARCH,
                AVAILABLE_IMAGE_GENERATORS=AVAILABLE_IMAGE_GENERATORS,
                AVAILABLE_INTERFACES=AVAILABLE_INTERFACES,
            )
            duplicated_llms_changed = self.handlers.sync_duplicated_llms(force=True)
            active_handlers_changed = self.handlers.refresh_extension_handlers(
                old_handlers,
                new_handlers,
                self.newelle_settings,
            )
            if duplicated_llms_changed & {
                self.newelle_settings.language_model,
                self.newelle_settings.secondary_language_model,
            }:
                self.handlers.select_handlers(self.newelle_settings)
                active_handlers_changed = True
        else:
            new_loader.set_handlers(
                self.handlers.llm,
                self.handlers.stt,
                self.handlers.tts,
                self.handlers.secondary_llm,
                self.handlers.embedding,
                self.handlers.rag,
                self.handlers.memory,
                self.handlers.websearch,
            )

        if "prompts" in refreshes:
            restore_prompts()
            new_loader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
            self.newelle_settings.load_prompts()

        if "modes" in refreshes and hasattr(self, "mode_manager"):
            new_loader.add_modes(self.mode_manager)

        if "tools" in refreshes or active_handlers_changed:
            self.require_tool_update()
            refreshes.add("tools")

        if self.ui_controller is not None and hasattr(
            self.ui_controller, "refresh_extension_resources"
        ):
            GLib.idle_add(
                self.ui_controller.refresh_extension_resources,
                set(refreshes),
            )

        print(f"Extensions reload ({', '.join(sorted(refreshes)) or 'loader only'})")
        return refreshes

    def reload(self, reload_type: ReloadType):
        """Reload the specified settings

        Args:
            reload_type: type of reload
        """
        if reload_type == ReloadType.EXTENSIONS:
            return self.reload_extensions()
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
        elif reload_type == ReloadType.TOOLS:
            self.skill_manager.discover()
            skills_integration = self.integrationsloader.extensionsmap.get("skills")
            if skills_integration is not None:
                skills_integration.set_skill_manager(self.skill_manager)
            self.require_tool_update()
        elif reload_type == ReloadType.WEBSEARCH:
            self.handlers.select_handlers(self.newelle_settings)
            self.newelle_settings.save_prompts()
            self.newelle_settings.load_prompts()
        elif reload_type == ReloadType.IMAGE_GENERATOR:
            self.handlers.select_handlers(self.newelle_settings)
            self.require_tool_update()


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
    
    def get_commands(self):
        commands = []
        commands.extend(self.integrationsloader.get_commands())
        commands.extend(self.extensionloader.get_commands())
        return commands
    
    def get_command(self, name):
        for command in self.get_commands():
            if command.name == name:
                return command 

        return None

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

            # Apply the active Mode's tool override (enable/remove/no_change)
            if hasattr(self, "mode_manager"):
                is_enabled = self.mode_manager.resolve_tool_enabled(tool.name, is_enabled)

            if is_enabled:
                enabled_tools.append(tool)

        return enabled_tools
        
    def load_extensions(self):
        """Load extensions"""
        # Load extensions
        self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path,
                                               extension_cache=self.extensions_cache, settings=self.settings)
        self.extensionloader.load_extensions()
        self.extensionloader.add_handlers(AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH, AVAILABLE_IMAGE_GENERATORS=AVAILABLE_IMAGE_GENERATORS, AVAILABLE_INTERFACES=AVAILABLE_INTERFACES)
        self.extensionloader.add_prompts(PROMPTS, AVAILABLE_PROMPTS)
        self.extensionloader.add_tools(self.tools)
        self.set_ui_controller(self.ui_controller)

    def load_integrations(self):
        """Load integrations"""
        self.integrationsloader = ExtensionLoader(self.extension_path, pip_path=self.pip_path, settings=self.settings, extension_cache=self.extensions_cache)
        self.integrationsloader.load_integrations(AVAILABLE_INTEGRATIONS)
        skills_integration = self.integrationsloader.extensionsmap.get("skills")
        if skills_integration is not None and hasattr(self, "skill_manager"):
            skills_integration.set_skill_manager(self.skill_manager)
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

    def switch_profile(self, profile_name: str):
        """Switch to a different profile.

        Saves the current profile's settings, restores the target profile's
        saved settings, and reloads any handlers that changed.

        Args:
            profile_name: name of the profile to switch to

        Returns:
            list of ReloadType values that were triggered

        Raises:
            ValueError: if the profile does not exist
        """
        from .utility.profile_settings import restore_settings_from_dict_by_groups
        if profile_name not in self.newelle_settings.profile_settings:
            raise ValueError(f"Profile '{profile_name}' not found")

        current = self.settings.get_string("current-profile")
        if current == profile_name:
            return []

        # Save current profile settings
        self.update_current_profile()

        # Restore target profile settings
        profiles = json.loads(self.settings.get_string("profiles"))
        profile_data = profiles.get(profile_name, {})
        groups = profile_data.get("settings_groups", [])
        saved = profile_data.get("settings", {})

        self.settings.set_string("current-profile", profile_name)
        restore_settings_from_dict_by_groups(self.settings, saved, groups, SETTINGS_GROUPS)

        reload_types = self.update_settings()
        return reload_types

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

    def export_single_chat(self, chat_id):
        """Export a single chat to JSON format

        Args:
            chat_id: The chat ID to export

        Returns:
            dict: Export data in JSON format, or None if chat_id invalid
        """
        if chat_id not in self.chats:
            return None

        chat_data = self.chats[chat_id]
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
        for _cid, chat_data in self.chats.items():
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
            tuple: (success: bool, message: str, imported_count: int, last_chat_id: int | None)
        """
        # Validate required fields
        if "version" not in data or "export_metadata" not in data:
            return False, _("Invalid export format: missing required fields"), 0, None

        export_metadata = data["export_metadata"]
        export_type = export_metadata.get("export_type")

        if export_type == "single_chat":
            return self._import_single_chat(data)
        elif export_type == "multiple_chats":
            return self._import_multiple_chats(data)
        else:
            return False, _("Unknown export type"), 0, None

    def _import_single_chat(self, data):
        """Import a single chat from export data. Returns (success, message, count, last_chat_id)."""
        try:
            chat = data["chat"]
            name = chat.get("name", "Imported Chat")
            messages = chat.get("messages", [])
            profile = chat.get("profile")

            chat_id = self.create_visible_chat(name=name, profile=profile)
            self.chats[chat_id]["chat"] = messages[:]
            self.save_chats()
            return True, _("Imported 1 chat successfully"), 1, chat_id
        except Exception as e:
            return False, _("Error importing chat: {0}").format(str(e)), 0, None

    def _import_multiple_chats(self, data):
        """Import multiple chats from export data. Returns (success, message, count, last_chat_id)."""
        try:
            chats = data["chats"]
            imported_count = 0
            skipped_count = 0
            last_chat_id = None

            # Track existing chat names
            existing_names = {c["name"] for c in self.chats.values()}

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

                    chat_id = self.create_visible_chat(name=name, profile=profile)
                    self.chats[chat_id]["chat"] = messages[:]
                    existing_names.add(name)
                    imported_count += 1
                    last_chat_id = chat_id
                except Exception:
                    skipped_count += 1
                    continue

            if imported_count > 0:
                self.save_chats()

            message = _("Imported {0} chat(s)").format(imported_count)
            if skipped_count > 0:
                message += _(" (skipped {0})").format(skipped_count)

            return True, message, imported_count, last_chat_id
        except Exception as e:
            return False, _("Error importing chats: {0}").format(str(e)), 0, None

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
            return self.newelle_settings.virtualization and is_flatpak()
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
        elif name == "call":
            return self.is_call_request
        elif name == "skills_available":
            if hasattr(self, "skill_manager"):
                return len(self.skill_manager.get_enabled_skills()) > 0
            return False
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
        use_fixed = self.newelle_settings.context_mode == "fixed"
        count = self.newelle_settings.memory if use_fixed else -1
        msgs = chat[:-1] if not include_last_message else chat
        msgs.reverse()
        for msg in msgs:
            if count == 0:
                break
            if msg["User"] == "Console" and msg["Message"] == "None":
                continue
            msg["Reasoning"], msg["Message"] = extract_reasoning_content(msg["Message"])
            if msg["User"] == "File" or msg["User"] == "Folder":
                msg["Message"] = f"```{msg['User'].lower()}\n{msg['Message'].strip()}\n```"
                msg["User"] = "User"
            history.insert(0,msg)
            if count > 0:
                count -= 1
        return history

    def _trim_context(
        self,
        history: list[dict[str, str]],
        prompts: list[str],
        current_message: str,
    ) -> tuple[list[dict[str, str]], TrimResult | None]:
        """Trim history using the ContextManager when context-manager mode is active.

        Returns (trimmed_history, trim_result). trim_result is None when using fixed mode.
        """
        if self.newelle_settings.context_mode != "context-manager":
            return history, None

        prompts_token_count = sum(count_tokens(p) for p in prompts)

        embedding = getattr(self.handlers, "embedding", None)
        if self.newelle_settings.use_secondary_language_model:
            llm = getattr(self.handlers, "secondary_llm", None)
        else:
            llm = getattr(self.handlers, "llm", None)

        cm = ContextManager(
            max_tokens=self.newelle_settings.context_max,
            suggested_tokens=self.newelle_settings.context_suggested,
            embedding_handler=embedding,
            llm_handler=llm,
            summarization_enabled=self.newelle_settings.context_summarization,
        )
        result = cm.trim(history, prompts_token_count, current_message)
        self.last_trim_result = result
        return result.history, result

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
            memory_contexts = self.handlers.memory.get_context(
                chat[-1]["Message"], self.get_history(chat=chat)
            ) or []
            r += [
                format_source_context(context, "Saved memory", source_type="Memory")
                for context in memory_contexts
                if context and context.strip()
            ]
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

    def get_vision_model(self) -> LLMHandler:
        """Return the model configured to handle image and video chats."""
        if (
            self.newelle_settings.use_secondary_language_model
            and self.newelle_settings.use_secondary_language_model_for_vision
        ):
            return self.handlers.secondary_llm
        return self.handlers.llm

    def get_model_for_chat(self, chat: list[dict]) -> LLMHandler:
        """Return the model that should generate the next response for a chat."""
        if chat_contains_vision(chat):
            return self.get_vision_model()
        return self.handlers.llm

    def _build_tool_system_prompt(self, chat_id: int) -> list[str]:
        """Build the live system prompt used by the agent tool loop.

        Unlike a caller-supplied prompt, this prompt is derived from the active
        mode and may need to be rebuilt between tool iterations.
        """
        prompts = []
        formatter = PromptFormatter(replace_variables_dict(), self.get_variable)
        for prompt in self.newelle_settings.bot_prompts:
            prompts.append(formatter.format(prompt))
        prompts += self.get_memory_prompt(chat_id=chat_id)
        return prompts

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

        # Validate chat_id exists
        if not self.chats or effective_chat_id not in self.chats:
            print(f"prepare_generation: Invalid chat_id {effective_chat_id}, chats: {list(self.chats.keys()) if self.chats else []}")
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
        current_message = chat[-1]["Message"] if chat else ""
        # Let extensions preprocess the history
        old_history = copy.deepcopy(history)
        old_user_prompt = current_message
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

        # Extensions may change both the history and prompts. Trim only their
        # effective result so the context manager's selection is what is sent.
        history, _ = self._trim_context(
            new_history,
            prompts,
            chat[-1]["Message"],
        )

        message_label = ""
        try:
            t1 = time.time()
            model = self.get_model_for_chat(chat)
            send_history = history.copy()
            if model.stream_enabled():
                message_label = model.send_message_stream(
                    chat[-1]["Message"],
                    send_history,
                    prompts,
                    update_callback,
                    [stream_number_variable], 
                )
            else:
                message_label = model.send_message(chat[-1]["Message"], send_history, prompts)

            raw_message_label = str(message_label)
            response_metadata = getattr(message_label, "response_metadata", None)
            
            # Post-generation logic
            last_generation_time = time.time() - t1
            
            input_tokens = 0
            for prompt in prompts:
                input_tokens += count_tokens(prompt)
            for message in history:
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
        if message_label != raw_message_label:
            response_metadata = None
        
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
            'time': last_generation_time,
            'trim_result': getattr(self, 'last_trim_result', None),
            'response_metadata': response_metadata,
        })

    def run_llm_with_tools(
        self,
        message: str,
        chat_id: int,
        system_prompt: list[str] = None,
        on_message_callback: Callable[[str], None] = None,
        on_tool_result_callback: Callable[[str, ToolResult], None] = None,
        max_tool_calls: int | None = None,
        save_chat: bool = False,
        force_tools_on_main_thread: bool = False,
        tool_registry: ToolRegistry | None = None,
        skill_manager: SkillManager | None = None,
        extension_processing: bool = True,
    ) -> str:
        """Run LLM with tool support integration.

        Args:
            message: The user message to send
            history: Chat history (uses current chat if None)
            system_prompt: System prompts (uses prepared prompts if None)
            on_message_callback: Callback for streaming message updates
            on_tool_result_callback: Callback for tool results, receives (tool_name, ToolResult)
            max_tool_calls: Maximum number of tool calls to execute. When omitted,
                use the shared max-tool-calls setting.
            chat_id: Chat ID to use (uses current if None)
            save_chat: If True, assistant messages are added to chat history
            force_tools_on_main_thread: If True, execute tool calls on the GTK main thread.
            tool_registry: Optional tool registry to use for this run instead of the controller registry.
            skill_manager: Optional skill manager to bind for this run, used by skill-related tools.
            extension_processing: If True, run extension/integration preprocess_history and
                postprocess_history hooks on the history and final response, mirroring
                generate_response. Set to False to bypass extension processing.

        Returns:
            Final message from the LLM
        """
        if max_tool_calls is None:
            max_tool_calls = getattr(self.newelle_settings, "max_tool_calls", 10)
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")

        active_tool_registry = tool_registry if tool_registry is not None else self.tools
        active_skill_manager = skill_manager if skill_manager is not None else getattr(self, "skill_manager", None)
        skills_integration = None
        original_skill_manager = None
        if hasattr(self, "integrationsloader") and skill_manager is not None:
            skills_integration = self.integrationsloader.extensionsmap.get("skills")
            if skills_integration is not None:
                original_skill_manager = getattr(skills_integration, "skill_manager", None)
                skills_integration.set_skill_manager(active_skill_manager)

        msg_uuid = int(uuid_lib.uuid4())
        self.chats[chat_id]["chat"].append({"User": "User", "Message": message, "UUID": msg_uuid})
        if save_chat:
            self.save_chats()
        history = self.get_history(chat=self.chats[chat_id]["chat"], include_last_message=True)
        system_prompt_was_built = system_prompt is None
        if system_prompt is None:
            _, _, _, _, _, effective_chat_id = self.prepare_generation(chat_id=chat_id)
            system_prompt = self._build_tool_system_prompt(effective_chat_id)
        
        # Avoid history duplication: check the last entry is the current message.
        last_entry_is_current = bool(
            history
            and history[-1].get("User") == "User"
            and history[-1].get("Message") == message
        )
        current_history = history.copy()
        # Let extensions/integrations preprocess the history and prompts before
        # generation, mirroring generate_response. Only runs when a fresh
        # system_prompt was built above; an explicit system_prompt means the
        # caller took full control of the prompts.
        if extension_processing and system_prompt_was_built:
            current_history, system_prompt = self.integrationsloader.preprocess_history(current_history, system_prompt)
            current_history, system_prompt = self.extensionloader.preprocess_history(current_history, system_prompt)

        # Avoid history duplication without removing the message from the
        # canonical working history. Later tool iterations still need the
        # original user request and every preceding tool result.
        current_prompt = message
        current_prompt_index = None
        if last_entry_is_current and current_history:
            current_prompt_index = len(current_history) - 1
            current_prompt = current_history[current_prompt_index].get("Message") or message

        mode_manager = getattr(self, "mode_manager", None)
        active_mode_name = (
            mode_manager.get_active_mode_name()
            if mode_manager is not None
            else None
        )
        model = self.get_model_for_chat(self.chats[chat_id]["chat"])
        cont = True
        iteration = 0
        tool_call_count = 0
        final_synthesis_turn = False
        assistant_msg_uuid = None
        response_text = ""
        text_content = ""
        try:
            while True:
                full_response = ""
                if not cont:
                    break
                cont = False

                # ``switch_mode`` updates the controller while this method is
                # still running. Refresh every mode-derived snapshot before the
                # follow-up model call, but keep ``current_history`` intact so
                # the model receives the switch result and all earlier results.
                current_mode_name = (
                    mode_manager.get_active_mode_name()
                    if mode_manager is not None
                    else None
                )
                if current_mode_name != active_mode_name:
                    active_mode_name = current_mode_name
                    if system_prompt_was_built:
                        system_prompt = self._build_tool_system_prompt(chat_id)
                        if extension_processing:
                            # Extensions can add prompt context based on history.
                            # Give them a deep copy because this pass is only meant
                            # to rebuild prompts, not process the history twice.
                            prompt_history = copy.deepcopy(current_history)
                            prompt_history, system_prompt = self.integrationsloader.preprocess_history(
                                prompt_history, system_prompt
                            )
                            prompt_history, system_prompt = self.extensionloader.preprocess_history(
                                prompt_history, system_prompt
                            )
                    if tool_registry is None:
                        active_tool_registry = self.tools
                    model = self.get_model_for_chat(self.chats[chat_id]["chat"])

                def stream_callback(text: str):
                    nonlocal full_response
                    full_response += text
                    if on_message_callback:
                        on_message_callback(text)
                
                # Each handler appends ``prompt`` to the history it receives.
                # Remove that prompt only from this request-local copy; mutating
                # ``current_history`` here would corrupt subsequent tool turns.
                request_history = current_history.copy()
                if iteration == 0:
                    prompt = current_prompt
                    if current_prompt_index is not None:
                        request_history.pop(current_prompt_index)
                else:
                    prompt = ""
                    if (
                        request_history
                        and request_history[-1].get("ToolContext")
                    ):
                        prompt = request_history.pop()["Message"]
                    else:
                        for i in range(len(request_history) - 1, -1, -1):
                            if request_history[i]["User"] == "Console":
                                prompt = request_history.pop(i)["Message"]
                                break

                request_system_prompt = system_prompt
                if final_synthesis_turn:
                    request_system_prompt = list(system_prompt) + [
                        "The maximum tool call limit has been reached. Do not call any "
                        "more tools; return the best final answer using the results already available."
                    ]

                send_history, _ = self._trim_context(request_history, request_system_prompt, message)

                if model.stream_enabled():
                    response = model.send_message_stream(
                        prompt,
                        send_history,
                        request_system_prompt,
                        stream_callback
                    )
                else:
                    response = model.send_message(
                        prompt,
                        send_history,
                        request_system_prompt
                    )
                    if on_message_callback:
                        on_message_callback(response)

                response_text = str(response)
                response_metadata = getattr(response, "response_metadata", None)
                chunks = get_message_chunks(response_text)
                
                text_content = ""
                tool_calls = []
                
                for chunk in chunks:
                    if chunk.type == "tool_call":
                        tool_calls.append({
                            "name": chunk.tool_name,
                            "args": chunk.tool_args,
                            "id": getattr(chunk, "tool_id", ""),
                        })
                    elif chunk.type in ("text", "markdown"):
                        text_content += "\n" + chunk.text

                # Some streaming handlers throttle their callbacks and can
                # leave the final character or provider chunk unreported.
                # Flush the complete cumulative response once generation is
                # done. Consumers already de-duplicate cumulative updates.
                if model.stream_enabled() and not tool_calls and on_message_callback:
                    on_message_callback(response)
                
                if not tool_calls or final_synthesis_turn:
                    final_content = response_text if not tool_calls else text_content
                    msg_uuid = int(uuid_lib.uuid4())
                    assistant_entry = {
                        "User": "Assistant",
                        "Message": response_text,
                        "UUID": msg_uuid,
                    }
                    if response_metadata is not None:
                        assistant_entry["OpenAIResponse"] = response_metadata
                    current_history.append(assistant_entry)
                    if save_chat:
                        stored_entry = {
                            **assistant_entry,
                            "Profile": self.newelle_settings.current_profile,
                        }
                        self.chats[chat_id]["chat"].append(stored_entry)
                        self.save_chats()
                    # Let extensions/integrations postprocess the final response,
                    # mirroring generate_response.
                    if extension_processing:
                        final_message = final_content
                        chat_list, final_message = self.integrationsloader.postprocess_history(self.chats[chat_id]["chat"], final_message)
                        chat_list, final_message = self.extensionloader.postprocess_history(chat_list, final_message)
                        if save_chat:
                            for entry in chat_list:
                                if entry.get("User") == "Assistant" and entry.get("UUID") == msg_uuid:
                                    if entry.get("Message") != response_text:
                                        entry.pop("OpenAIResponse", None)
                                    break
                            self.set_chat_by_id(chat_id, chat_list)
                            self.save_chats()
                        return final_message
                    return final_content
                remaining_tool_calls = max_tool_calls - tool_call_count
                tool_calls = tool_calls[:remaining_tool_calls]
                assistant_msg_uuid = int(uuid_lib.uuid4())

                assistant_entry = {
                    "User": "Assistant",
                    "Message": response_text,
                    "UUID": assistant_msg_uuid,
                }
                if response_metadata is not None:
                    assistant_entry["OpenAIResponse"] = response_metadata
                current_history.append(assistant_entry)
                if save_chat:
                    self.chats[chat_id]["chat"].append({
                        **assistant_entry,
                        "Profile": self.newelle_settings.current_profile,
                    })

                for tool_call in tool_calls:
                    tool_call_count += 1
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    tool_result_output = None
                    provider_tool_id = tool_call.get("id")
                    tool_uuid = (
                        provider_tool_id.strip()
                        if isinstance(provider_tool_id, str) and provider_tool_id.strip()
                        else str(uuid_lib.uuid4())[:8]
                    )
                    tool_context_messages = []
                    tool_display_text = None

                    # Lazy loading: a tool_search call means the model just fetched
                    # a tool's schema. Expand it in the system prompt so that, on the
                    # next turn, the tool carries its real parameters and can be
                    # invoked through native tool calling too.
                    if (
                        tool_name == "tool_search"
                        and isinstance(tool_args, dict)
                        and tool_args.get("tool_name")
                    ):
                        system_prompt = active_tool_registry.expand_tool_in_prompts(
                            system_prompt, tool_args["tool_name"]
                        )

                    try:
                        tool = active_tool_registry.get_tool(tool_name)
                        if tool is None:
                            raise ValueError(f"Tool '{tool_name}' not found")

                        # Lazy loading guard: if a lazy tool is called before its
                        # schema was fetched, hand back the schema instead of
                        # running it with guessed arguments, and mark it expanded.
                        redirect = active_tool_registry.maybe_redirect_lazy_tool(
                            tool_name, self.newelle_settings.tools_settings_dict, self.expanded_tools
                        )
                        if redirect is not None:
                            # Native tool calling needs the full schema next turn.
                            system_prompt = active_tool_registry.expand_tool_in_prompts(
                                system_prompt, tool_name
                            )
                            if on_tool_result_callback:
                                on_tool_result_callback(tool_name, redirect)
                            tool_result_output = redirect.get_output()
                            if tool_result_output is not None:
                                cont = True
                        else:
                            tool_kwargs = {"msg_uuid": msg_uuid, "tool_uuid": tool_uuid, "chat_id": chat_id, **tool_args}
                            should_run_on_main_thread = (
                                force_tools_on_main_thread or tool.run_on_main_thread
                            )

                            if should_run_on_main_thread:
                                result = self.execute_tool_on_main_thread(
                                    tool_name,
                                    tool_kwargs,
                                    tool_registry=active_tool_registry,
                                )
                            else:
                                result = tool.execute(**tool_kwargs)
                            if isinstance(result, ToolResult):
                                if on_tool_result_callback:
                                    on_tool_result_callback(tool_name, result)
                                tool_result_output = result.get_output()
                                tool_context_messages = result.get_context_messages()
                                tool_display_text = getattr(result, "display_text", None)
                                if tool_result_output is not None or tool_context_messages:
                                    cont = True
                            elif result is not None:
                                tool_result_output = str(result)
                                if on_tool_result_callback:
                                    normalized_result = ToolResult()
                                    normalized_result.set_output(tool_result_output)
                                    on_tool_result_callback(tool_name, normalized_result)
                                cont = True
                    except Exception as e:
                        tool_result_output = f"Error: {str(e)}"
                        tool_display_text = None
                        if on_tool_result_callback:
                            tr = ToolResult()
                            tr.set_output(tool_result_output)
                            on_tool_result_callback(tool_name, tr)

                    console_output = tool_result_output
                    if console_output is None and tool_context_messages:
                        console_output = "Tool returned additional context."
                    tool_result_msg = f"[Tool: {tool_name}, ID: {tool_uuid}]\n{console_output}"
                    if tool_display_text:
                        tool_result_msg = tool_result_msg + "\n" + tool_display_text
                    
                    current_history.append({
                        "User": "Console",
                        "Message": tool_result_msg,
                        "UUID": tool_uuid
                    })
                    for context_message in tool_context_messages:
                        current_history.append({
                            "User": "User",
                            "Message": context_message,
                            "ToolContext": True,
                        })
                    if save_chat:
                        self.chats[chat_id]["chat"].append({
                            "User": "Console",
                            "Message": tool_result_msg,
                        })
                        for context_message in tool_context_messages:
                            self.chats[chat_id]["chat"].append({
                                "User": "User",
                                "Message": context_message,
                                "ToolContext": True,
                            })
                        self.save_chats()

                if tool_call_count >= max_tool_calls:
                    final_synthesis_turn = True
                    cont = True
                iteration += 1
            
            # Let extensions/integrations postprocess the final response,
            # mirroring generate_response.
            if extension_processing:
                final_message = text_content
                chat_list, final_message = self.integrationsloader.postprocess_history(self.chats[chat_id]["chat"], final_message)
                chat_list, final_message = self.extensionloader.postprocess_history(chat_list, final_message)
                if save_chat:
                    for entry in chat_list:
                        if (
                            assistant_msg_uuid is not None
                            and entry.get("User") == "Assistant"
                            and entry.get("UUID") == assistant_msg_uuid
                        ):
                            if entry.get("Message") != response_text:
                                entry.pop("OpenAIResponse", None)
                            break
                    self.set_chat_by_id(chat_id, chat_list)
                    self.save_chats()
                return final_message
            return text_content
        finally:
            if skills_integration is not None:
                skills_integration.set_skill_manager(original_skill_manager)

    def execute_tool_on_main_thread(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_registry: ToolRegistry | None = None,
    ) -> Any:
        """Execute a tool from a worker thread while keeping GTK work on the main loop."""
        active_tool_registry = tool_registry if tool_registry is not None else self.tools
        tool = active_tool_registry.get_tool(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' not found")

        if threading.current_thread() is threading.main_thread():
            return tool.execute(**arguments)

        done = threading.Event()
        result_holder: dict[str, Any] = {}

        def run():
            try:
                result_holder["result"] = tool.execute(**arguments)
            except Exception as exc:
                result_holder["error"] = exc
            finally:
                done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)
        done.wait()
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")


class NewelleSettings:

    def __init__(self, mode_manager=None):
        # The active ModeManager overlays prompt enable/disable and text
        # overrides on top of the profile prompts during ``load_prompts``.
        # It is optional so NewelleSettings remains usable without a mode
        # manager (e.g. in isolated tests).
        self.mode_manager = mode_manager

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
        self.auto_generate_name = settings.get_boolean("auto-generate-name")
        self.chat_id = settings.get_int("chat")
        self.main_path = settings.get_string("path")
        self.auto_run = settings.get_boolean("auto-run")
        self.display_latex = settings.get_boolean("display-latex")
        self.compact_mode = settings.get_boolean("compact-mode")
        self.compact_input_bar = settings.get_boolean("compact-input-bar")
        self.tts_enabled = settings.get_boolean("tts-on")
        self.tts_program = settings.get_string("tts")
        self.tts_voice = settings.get_string("tts-voice")
        self.stt_engine = settings.get_string("stt-engine")
        self.stt_settings = settings.get_string("stt-settings")
        self.secondary_stt_engine = settings.get_string("secondary-stt-engine")
        self.secondary_stt_settings = settings.get_string("stt-secondary-settings")
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
        self.use_secondary_language_model_for_vision = self.settings.get_boolean("secondary-llm-vision")
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
        self.max_tool_calls = settings.get_int("max-tool-calls")
        self.external_browser = settings.get_boolean("external-browser")
        self.initial_browser_page = settings.get_string("initial-browser-page")
        self.browser_search_string = settings.get_string("browser-search-string")
        self.browser_session_persist = settings.get_boolean("browser-session-persist")
        self.editor_color_scheme = settings.get_string("editor-color-scheme")
        self.tools_settings = settings.get_string("tools-settings")
        self.tools_settings_dict = json.loads(self.tools_settings)
        self.skills_settings = settings.get_string("skills-settings")
        self.mcp_servers = self.settings.get_string("mcp-servers")
        self.mcp_servers_dict = json.loads(self.mcp_servers)
        self.file_permissions = self.settings.get_string("file-permissions")
        self.file_permissions_list = json.loads(self.file_permissions)
        self.scheduled_tasks = self.settings.get_string("scheduled-tasks")
        self.wakeword_enabled = settings.get_boolean("wakeword-on")
        self.wakeword_mode = settings.get_string("wakeword-mode")
        self.wakeword_engine = settings.get_string("wakeword-engine")
        self.wakeword_engine_settings = settings.get_string("wakeword-engine-settings")
        self.wakeword = settings.get_string("wakeword")
        self.wakeword_vad_aggressiveness = settings.get_int("wakeword-vad-aggressiveness")
        self.wakeword_pre_buffer_duration = settings.get_double("wakeword-pre-buffer-duration")
        self.wakeword_silence_duration = settings.get_double("wakeword-silence-duration")
        self.wakeword_energy_threshold = settings.get_int("wakeword-energy-threshold")
        self.context_mode = settings.get_string("context-mode")
        self.context_max = settings.get_int("context-max")
        self.context_suggested = settings.get_int("context-suggested")
        self.context_summarization = settings.get_boolean("context-summarization")
        self.font_family = settings.get_string("font-family")
        self.font_size = settings.get_int("font-size")
        self.line_height = settings.get_double("line-height")
        self.monospace_font_family = settings.get_string("monospace-font-family")
        self.monospace_font_size = settings.get_int("monospace-font-size")
        self.monospace_line_height = settings.get_double("monospace-line-height")
        self.image_generator = self.settings.get_string("image-generator")
        self.image_generator_settings = self.settings.get_string("image-generator-settings")
        self.hide_warning = settings.get_boolean("hide-warning")
        self.load_prompts()
        # Adjust paths
        if os.path.exists(os.path.expanduser(self.main_path)):
            os.chdir(os.path.expanduser(self.main_path))
        else:
            self.main_path = "~"

    def load_prompts(self):
        """Load prompts and do overrides"""
        self.custom_prompts = json.loads(self.settings.get_string("custom-prompts"))
        self.user_custom_prompts = json.loads(self.settings.get_string("user-custom-prompts"))
        for prompt_data in self.user_custom_prompts:
            key = prompt_data["key"]
            PROMPTS[key] = prompt_data["text"]
            existing = next((p for p in AVAILABLE_PROMPTS if p["key"] == key), None)
            if existing:
                existing["title"] = prompt_data["title"]
                existing["description"] = prompt_data["description"]
            else:
                AVAILABLE_PROMPTS.append({
                    "key": key,
                    "setting_name": key,
                    "title": prompt_data["title"],
                    "description": prompt_data["description"],
                    "editable": True,
                    "show_in_settings": True,
                    "default": True,
                    "user_custom": True,
                })
        self.prompts = override_prompts(self.custom_prompts, PROMPTS)
        # self.prompts stays profile-level only: mode overrides are applied to
        # bot_prompts below so the Mode Editor keeps comparing against the true
        # profile base text (controller.newelle_settings.prompts).
        mm = self.mode_manager
        self.bot_prompts = []
        ordered_prompts = self._get_ordered_prompts()
        for prompt in ordered_prompts:
            key = prompt["key"]
            is_active = False
            if prompt["setting_name"] in self.prompts_settings:
                is_active = self.prompts_settings[prompt["setting_name"]]
            else:
                is_active = prompt["default"]
            if mm is not None:
                is_active = mm.resolve_prompt_enabled(key, is_active)
            if is_active:
                base_text = self.prompts[key]
                text = mm.resolve_prompt_text(key, base_text) if mm is not None else base_text
                self.bot_prompts.append(text)

    def _get_ordered_prompts(self):
        """Return AVAILABLE_PROMPTS sorted by the user's custom order."""
        try:
            order = json.loads(self.settings.get_string("prompts-order"))
        except (json.JSONDecodeError, Exception):
            order = []
        if not order:
            return list(AVAILABLE_PROMPTS)
        ordered = []
        key_to_prompt = {p["key"]: p for p in AVAILABLE_PROMPTS}
        for key in order:
            if key in key_to_prompt:
                ordered.append(key_to_prompt.pop(key))
        for prompt in AVAILABLE_PROMPTS:
            if prompt["key"] in key_to_prompt:
                ordered.append(prompt)
        return ordered

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
        if self.secondary_language_model != new_settings.secondary_language_model or self.use_secondary_language_model != new_settings.use_secondary_language_model or self.use_secondary_language_model_for_vision != new_settings.use_secondary_language_model_for_vision or self.secondary_language_model_settings != new_settings.secondary_language_model_settings:
            reloads.append(ReloadType.SECONDARY_LLM)
        
        if self.tts_program != new_settings.tts_program:
            reloads.append(ReloadType.TTS)

        if self.stt_engine != new_settings.stt_engine:
            reloads.append(ReloadType.STT)

        if self.automatic_stt != new_settings.automatic_stt:
            if self.wakeword_enabled:
                reloads.append(ReloadType.WAKEWORD)

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
        if self.mcp_servers != new_settings.mcp_servers or self.tools_settings != new_settings.tools_settings or self.skills_settings != new_settings.skills_settings:
            reloads.append(ReloadType.TOOLS)
        # Check wakeword settings
        if (self.wakeword_enabled != new_settings.wakeword_enabled or
            self.wakeword != new_settings.wakeword or
            self.wakeword_mode != new_settings.wakeword_mode or
            self.wakeword_engine != new_settings.wakeword_engine or
            self.wakeword_engine_settings != new_settings.wakeword_engine_settings or
            self.wakeword_vad_aggressiveness != new_settings.wakeword_vad_aggressiveness or
            self.wakeword_pre_buffer_duration != new_settings.wakeword_pre_buffer_duration or
            self.wakeword_silence_duration != new_settings.wakeword_silence_duration or
            self.wakeword_energy_threshold != new_settings.wakeword_energy_threshold or
            self.secondary_stt_engine != new_settings.secondary_stt_engine or
            self.secondary_stt_settings != new_settings.secondary_stt_settings):
            reloads.append(ReloadType.WAKEWORD)
        # Check prompts
        if len(self.prompts) != len(new_settings.prompts):
            reloads.append(ReloadType.PROMPTS)
        if self.offers != new_settings.offers:
            reloads.append(ReloadType.OFFERS)
        if self.image_generator != new_settings.image_generator or self.image_generator_settings != new_settings.image_generator_settings:
            reloads.append(ReloadType.IMAGE_GENERATOR)
        if self.hide_warning != new_settings.hide_warning:
            reloads.append(ReloadType.RELOAD_CHAT)
        if self.compact_mode != new_settings.compact_mode:
            reloads.append(ReloadType.COMPACT_MODE)
        if self.compact_input_bar != new_settings.compact_input_bar:
            reloads.append(ReloadType.COMPACT_INPUT_BAR)

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
        interfaces: List of Interface handlers
    """
    DUPLICATED_LLMS_SETTINGS_KEY = "__duplicated_llm_handlers__"

    def __init__(self, settings: Gio.Settings, extensionloader : ExtensionLoader, models_path, integrations: ExtensionLoader, controller):
        self.settings = settings
        self.extensionloader = extensionloader
        self.directory = models_path
        self.handlers =  {}
        self.handlers_cached = threading.Semaphore()
        self.handlers_cached.acquire()
        self.integrationsloader = integrations
        self.secondary_stt = None
        self.wakeword_handler = None
        self.controller = controller
        self.interfaces = {}
        self._duplicated_llm_definitions = []

    @classmethod
    def _normalize_duplicated_llm_definition(cls, definition: dict) -> dict | None:
        if not isinstance(definition, dict):
            return None
        key = str(definition.get("key", "")).strip()
        source = str(definition.get("source", "")).strip()
        title = str(definition.get("title", "")).strip()
        description = str(definition.get("description", "")).strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key)
            or key == cls.DUPLICATED_LLMS_SETTINGS_KEY
            or not source
            or not title
        ):
            return None
        return {
            "key": key,
            "source": source,
            "title": title,
            "description": description,
        }

    def _read_duplicated_llm_definitions(self) -> list[dict]:
        try:
            llm_settings = SettingsCache.get_instance(self.settings).get_json(
                "llm-settings"
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
        raw_definitions = llm_settings.get(
            self.DUPLICATED_LLMS_SETTINGS_KEY, []
        )
        if not isinstance(raw_definitions, list):
            return []
        definitions = []
        seen = set()
        for raw_definition in raw_definitions:
            definition = self._normalize_duplicated_llm_definition(raw_definition)
            if definition is None or definition["key"] in seen:
                continue
            seen.add(definition["key"])
            definitions.append(definition)
        return definitions

    @staticmethod
    def _build_duplicated_llm_class(base_class: type, key: str) -> type:
        class_name_key = re.sub(r"[^a-zA-Z0-9_]", "_", key)

        def get_duplication_settings(_self):
            return None

        return type(
            f"Duplicated{base_class.__name__}_{class_name_key}",
            (base_class,),
            {
                "key": key,
                "__module__": base_class.__module__,
                "get_duplication_settings": get_duplication_settings,
            },
        )

    def sync_duplicated_llms(self, force: bool = False) -> set[str]:
        """Synchronize persisted user-created LLM providers with the registry."""
        definitions = self._read_duplicated_llm_definitions()
        registered_keys = {
            key
            for key, descriptor in AVAILABLE_LLMS.items()
            if descriptor.get("duplicated", False)
        }
        expected_keys = set()
        for definition in definitions:
            source_descriptor = AVAILABLE_LLMS.get(definition["source"])
            key_descriptor = AVAILABLE_LLMS.get(definition["key"])
            if source_descriptor is None or source_descriptor.get("duplicated", False):
                continue
            if key_descriptor is None or key_descriptor.get("duplicated", False):
                expected_keys.add(definition["key"])
        if (
            not force
            and definitions == self._duplicated_llm_definitions
            and registered_keys == expected_keys
        ):
            return set()

        old_definitions = {
            definition["key"]: definition
            for definition in self._duplicated_llm_definitions
        }
        for key in registered_keys:
            AVAILABLE_LLMS.pop(key, None)

        registered_definitions = []
        for definition in definitions:
            key = definition["key"]
            source = definition["source"]
            if key in AVAILABLE_LLMS or source not in AVAILABLE_LLMS:
                continue
            source_descriptor = AVAILABLE_LLMS[source]
            if source_descriptor.get("duplicated", False):
                continue
            duplicate_class = self._build_duplicated_llm_class(
                source_descriptor["class"], key
            )
            descriptor = {
                "key": key,
                "title": definition["title"],
                "description": definition["description"],
                "class": duplicate_class,
                "duplicated": True,
                "source": source,
            }
            if source_descriptor.get("secondary", False):
                descriptor["secondary"] = True
            AVAILABLE_LLMS[key] = descriptor
            registered_definitions.append(definition)

        changed_keys = registered_keys | {
            definition["key"] for definition in registered_definitions
        }
        if not force:
            changed_keys = {
                key
                for key in changed_keys
                if old_definitions.get(key)
                != next(
                    (
                        definition
                        for definition in registered_definitions
                        if definition["key"] == key
                    ),
                    None,
                )
            }
        disposed = set()
        for cache_key, handler in list(self.handlers.items()):
            if cache_key[0] not in changed_keys:
                continue
            self.handlers.pop(cache_key, None)
            if id(handler) in disposed:
                continue
            disposed.add(id(handler))
            try:
                handler.destroy()
            except Exception as error:
                print(f"Error disposing duplicated LLM handler: {error}")

        # Retain valid-but-currently-unavailable definitions (for example, an
        # extension-provided source that is temporarily disabled) in settings.
        self._duplicated_llm_definitions = definitions
        return changed_keys

    def get_duplicable_llms(self) -> list[tuple[str, dict, list[dict]]]:
        """Return source descriptors and fields exposed for duplication."""
        duplicable = []
        for key, descriptor in AVAILABLE_LLMS.items():
            if descriptor.get("duplicated", False):
                continue
            handler = self.get_object(AVAILABLE_LLMS, key)
            settings = handler.get_duplication_settings()
            if settings is not None:
                duplicable.append((key, descriptor, settings))
        return duplicable

    def duplicate_llm(
        self,
        source: str,
        key: str,
        title: str,
        description: str,
        duplication_values: dict | None = None,
    ) -> dict:
        """Create and persist a user-defined copy of an LLM handler."""
        key = key.strip()
        title = title.strip()
        description = description.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key):
            raise ValueError(
                _("The handler key may only contain letters, numbers, '.', '_' and '-'.")
            )
        if not title:
            raise ValueError(_("The handler name cannot be empty."))
        if key in AVAILABLE_LLMS or key == self.DUPLICATED_LLMS_SETTINGS_KEY:
            raise ValueError(_("An LLM handler with this key already exists."))
        if source not in AVAILABLE_LLMS:
            raise ValueError(_("The source LLM handler is not available."))
        if AVAILABLE_LLMS[source].get("duplicated", False):
            raise ValueError(_("Duplicated LLM handlers cannot be duplicated again."))

        source_handler = self.get_object(AVAILABLE_LLMS, source)
        duplication_settings = source_handler.get_duplication_settings()
        if duplication_settings is None:
            raise ValueError(_("This LLM handler does not support duplication."))

        flat_settings = []
        pending = list(duplication_settings)
        while pending:
            setting = pending.pop(0)
            if setting.get("type") == "nested":
                pending[0:0] = setting.get("extra_settings", [])
            elif "key" in setting:
                flat_settings.append(setting)
        supplied_values = duplication_values or {}
        initial_values = {
            setting["key"]: supplied_values.get(
                setting["key"], setting.get("default")
            )
            for setting in flat_settings
        }
        definition = {
            "key": key,
            "source": source,
            "title": title,
            "description": description,
        }

        cache = SettingsCache.get_instance(self.settings)
        llm_settings = copy.deepcopy(cache.get_json("llm-settings"))
        definitions = self._read_duplicated_llm_definitions()
        definitions.append(definition)
        llm_settings[self.DUPLICATED_LLMS_SETTINGS_KEY] = definitions
        llm_settings[key] = initial_values
        cache.set_json("llm-settings", llm_settings)

        secondary_settings = copy.deepcopy(
            cache.get_json("llm-secondary-settings")
        )
        secondary_settings[key] = copy.deepcopy(initial_values)
        cache.set_json("llm-secondary-settings", secondary_settings)
        self.sync_duplicated_llms()
        return AVAILABLE_LLMS[key]

    def delete_duplicated_llm(self, key: str) -> bool:
        """Delete a duplicated provider and its primary/secondary settings."""
        descriptor = AVAILABLE_LLMS.get(key)
        if descriptor is None or not descriptor.get("duplicated", False):
            return False
        source = descriptor.get("source")
        cache = SettingsCache.get_instance(self.settings)
        llm_settings = copy.deepcopy(cache.get_json("llm-settings"))
        definitions = [
            definition
            for definition in self._read_duplicated_llm_definitions()
            if definition["key"] != key
        ]
        llm_settings[self.DUPLICATED_LLMS_SETTINGS_KEY] = definitions
        llm_settings.pop(key, None)
        cache.set_json("llm-settings", llm_settings)

        secondary_settings = copy.deepcopy(
            cache.get_json("llm-secondary-settings")
        )
        secondary_settings.pop(key, None)
        cache.set_json("llm-secondary-settings", secondary_settings)
        self.sync_duplicated_llms()

        fallback = source if source in AVAILABLE_LLMS else next(iter(AVAILABLE_LLMS))
        for setting_key in ("language-model", "secondary-language-model"):
            if self.settings.get_string(setting_key) == key:
                self.settings.set_string(setting_key, fallback)
        return True

    def destroy(self):
        for handler in self.handlers.values():
            handler.destroy()
        for interface in self.interfaces.values():
            interface.stop()

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
        if newelle_settings.wakeword_engine not in AVAILABLE_STT:
            # Find first wakeword-capable STT
            for key in AVAILABLE_STT:
                if AVAILABLE_STT[key].get("wakeword", False):
                    newelle_settings.wakeword_engine = key
                    break
            else:
                # Fallback to openwakeword if available, or first STT
                if "openwakeword" in AVAILABLE_STT:
                    newelle_settings.wakeword_engine = "openwakeword"
                else:
                    newelle_settings.wakeword_engine = list(AVAILABLE_STT.keys())[0]
        if newelle_settings.websearch_model not in AVAILABLE_WEBSEARCH:
            newelle_settings.websearch_model = list(AVAILABLE_WEBSEARCH.keys())[0]
        if newelle_settings.image_generator not in AVAILABLE_IMAGE_GENERATORS:
            newelle_settings.image_generator = list(AVAILABLE_IMAGE_GENERATORS.keys())[0]
      
    def set_ui_controller(self, ui_controller):
        self.ui_controller = ui_controller

    def select_handlers(self, newelle_settings: NewelleSettings, skip_auto_start_interfaces=False):
        """Assign the selected handlers

        Args:
            newelle_settings: Newelle settings
            skip_auto_start_interfaces: If True, don't auto-start any interfaces (used in headless mode)
        """
        self.sync_duplicated_llms()
        self.fix_handlers_integrity(newelle_settings)
        # Get LLM
        self.llm : LLMHandler = self.get_object(AVAILABLE_LLMS, newelle_settings.language_model)
        if newelle_settings.use_secondary_language_model:
            self.secondary_llm : LLMHandler = self.get_object(AVAILABLE_LLMS, newelle_settings.secondary_language_model, True)
        else:
            self.secondary_llm : LLMHandler = self.llm
        self.stt : STTHandler = self.get_object(AVAILABLE_STT, newelle_settings.stt_engine)
        # Set wakeword handler based on mode
        if newelle_settings.wakeword_mode == "secondary-stt":
            self.secondary_stt : STTHandler = self.get_object(AVAILABLE_STT, newelle_settings.secondary_stt_engine, True)
            self.wakeword_handler : STTHandler = None
        else:  # openwakeword mode
            self.wakeword_handler : STTHandler = self.get_object(AVAILABLE_STT, newelle_settings.wakeword_engine, True)
            self.secondary_stt : STTHandler = None
        self.tts : TTSHandler = self.get_object(AVAILABLE_TTS, newelle_settings.tts_program)
        self.embedding : EmbeddingHandler= self.get_object(AVAILABLE_EMBEDDINGS, newelle_settings.embedding_model)
        self.memory : MemoryHandler = self.get_object(AVAILABLE_MEMORIES, newelle_settings.memory_model)
        self.memory.set_memory_size(newelle_settings.memory)
        self.rag : RAGHandler = self.get_object(AVAILABLE_RAGS, newelle_settings.rag_model)
        self.websearch : WebSearchHandler = self.get_object(AVAILABLE_WEBSEARCH, newelle_settings.websearch_model)
        self.image_generator : ImageGeneratorHandler = self.get_object(AVAILABLE_IMAGE_GENERATORS, newelle_settings.image_generator)
        self.refresh_interfaces(auto_start=not skip_auto_start_interfaces)
        # Assign handlers 
        self.integrationsloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.extensionloader.set_handlers(self.llm, self.stt, self.tts, self.secondary_llm, self.embedding, self.rag, self.memory, self.websearch)
        self.memory.set_handlers(self.secondary_llm, self.embedding, self.rag)

        self.rag.set_handlers(self.llm, self.embedding)
        #self.image_generator.set_ui_controller(self.controller.ui_controller)
        threading.Thread(target=self.install_missing_handlers).start()

    def refresh_interfaces(self, auto_start=True):
        """Synchronize interface instances without restarting unchanged ones."""
        available_keys = set(AVAILABLE_INTERFACES)
        for key in set(self.interfaces) - available_keys:
            interface = self.interfaces.pop(key)
            try:
                interface.stop()
            except Exception as error:
                print(f"Error stopping removed interface '{key}': {error}")
            self.handlers.pop((key, "interface", False), None)

        for key in AVAILABLE_INTERFACES:
            is_new = key not in self.interfaces
            interface = self.get_object(AVAILABLE_INTERFACES, key)
            interface.set_controller(self.controller)
            self.interfaces[key] = interface
            if not auto_start or not is_new:
                continue
            enabled = interface.get_setting("enabled", False, False)
            if not enabled:
                continue
            if Interface.check_external_running(key, self.directory):
                print(
                    f"Interface '{key}' is already running externally, "
                    "skipping auto-start"
                )
            else:
                print("Interface started")
                interface.start()

    def refresh_extension_handlers(
        self,
        old_contributions: dict[str, set[str]],
        new_contributions: dict[str, set[str]],
        newelle_settings: NewelleSettings,
    ) -> bool:
        """Invalidate changed extension handlers and preserve unrelated ones.

        Returns True when one of the selected handlers had to be recreated.
        """
        affected = {
            category: set(old_contributions.get(category, set()))
            | set(new_contributions.get(category, set()))
            for category in ExtensionLoader._HANDLER_CONTRIBUTION_METHODS
        }
        selected = {
            "llm": {
                newelle_settings.language_model,
                newelle_settings.secondary_language_model,
            },
            "tts": {newelle_settings.tts_program},
            "stt": {
                newelle_settings.stt_engine,
                newelle_settings.secondary_stt_engine,
                newelle_settings.wakeword_engine,
            },
            "memory": {newelle_settings.memory_model},
            "embedding": {newelle_settings.embedding_model},
            "rag": {newelle_settings.rag_model},
            "websearch": {newelle_settings.websearch_model},
            "image_generator": {newelle_settings.image_generator},
        }
        active_changed = any(
            affected[category] & keys for category, keys in selected.items()
        )

        invalidated = []
        invalidated_ids = set()
        for cache_key, handler in list(self.handlers.items()):
            key, category, _secondary = cache_key
            if key not in affected.get(category, set()):
                continue
            self.handlers.pop(cache_key, None)
            if id(handler) not in invalidated_ids:
                invalidated.append(handler)
                invalidated_ids.add(id(handler))

        for key in affected["interface"]:
            interface = self.interfaces.pop(key, None)
            if interface is not None and id(interface) not in invalidated_ids:
                invalidated.append(interface)
                invalidated_ids.add(id(interface))

        for handler in invalidated:
            try:
                if isinstance(handler, Interface):
                    handler.stop()
                else:
                    handler.destroy()
            except Exception as error:
                print(f"Error disposing extension handler: {error}")

        if active_changed:
            self.select_handlers(newelle_settings)
        else:
            if affected["interface"]:
                self.refresh_interfaces()
            self.extensionloader.set_handlers(
                self.llm,
                self.stt,
                self.tts,
                self.secondary_llm,
                self.embedding,
                self.rag,
                self.memory,
                self.websearch,
            )
        return active_changed

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
        if self.image_generator is not None:
            for tool in self.image_generator.get_tools():
                tools.register_tool(tool)
        self._add_send_message_tool(tools)

    def _get_message_interfaces(self) -> dict[str, Interface]:
        """Return local, running interfaces that can deliver agent messages.

        An interface running in a different Newelle process cannot safely be
        called from this process, even though its state file makes it visible.
        """
        return {
            key: interface
            for key, interface in self.interfaces.items()
            if interface.is_locally_running() and interface.supports_send_message()
        }

    def _add_send_message_tool(self, tools: ToolRegistry):
        """Register the optional cross-interface messaging tool when usable."""
        interfaces = self._get_message_interfaces()
        if not interfaces:
            return

        interface_schemas = []
        for key, interface in interfaces.items():
            properties = {
                "interface": {
                    "type": "string",
                    "const": key,
                    "description": (
                        f"Send through the running {getattr(interface, 'name', key)} "
                        "interface."
                    ),
                },
                **interface.get_send_message_options_schema(),
            }
            interface_schemas.append({
                "type": "object",
                "properties": properties,
                "required": ["interface", *interface.get_send_message_required_options()],
                "additionalProperties": False,
            })

        def send_message(message, extra_options):
            if not isinstance(message, str) or not message.strip():
                result = ToolResult()
                result.set_output("Error: 'message' must be a non-empty string.")
                return result
            if not isinstance(extra_options, dict):
                result = ToolResult()
                result.set_output("Error: 'extra_options' must be an object.")
                return result

            interface_key = extra_options.get("interface")
            interface = self._get_message_interfaces().get(interface_key)
            if interface is None:
                available = ", ".join(self._get_message_interfaces()) or "none"
                result = ToolResult()
                result.set_output(
                    f"Error: interface '{interface_key}' is not available. "
                    f"Running interfaces: {available}."
                )
                return result
            return interface.send_message(message, extra_options)

        tools.register_tool(Tool(
            name="send_message",
            title="Send Message",
            description=(
                "Send a message to the user through another running interface. "
                "Use the interface-specific destination options in extra_options."
            ),
            func=send_message,
            schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "Message to send. Media code blocks such as ```image, "
                            "```video, and ```file are supported when the selected "
                            "interface supports them."
                        ),
                    },
                    "extra_options": {
                        "description": "Select a running interface and its destination.",
                        "oneOf": interface_schemas,
                    },
                },
                "required": ["message", "extra_options"],
            },
            default_on=False,
            tools_group="Interfaces",
            icon_name="send-to-symbolic",
        ))

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
                    self.embedding, self.rag, self.websearch, self.image_generator]
        for handler in handlers:
            if not handler.is_installed():
                try:
                    constants = self.get_constants_from_object(handler)
                    name = constants.get(handler.key, {}).get("title", handler.key)
                    handler.install_with_progress(
                        _("Install {name}").format(name=name)
                    )
                except Exception as error:
                    print(f"Error installing {handler.key}: {error}")

    def cache_handlers(self):
        """Cache handlers"""
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
        for key in AVAILABLE_INTERFACES:
            self.handlers[(key, self.convert_constants(AVAILABLE_INTERFACES), False)] = self.get_object(AVAILABLE_INTERFACES, key)
        for key in AVAILABLE_IMAGE_GENERATORS:
            self.handlers[(key, self.convert_constants(AVAILABLE_IMAGE_GENERATORS), False)] = self.get_object(AVAILABLE_IMAGE_GENERATORS, key)
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
                case "interface":
                    return AVAILABLE_INTERFACES
                case "image_generator":
                    return AVAILABLE_IMAGE_GENERATORS
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
            elif constants == AVAILABLE_INTERFACES:
                return "interface"
            elif constants == AVAILABLE_IMAGE_GENERATORS:
                return "image_generator"
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
        cache_key = (key, self.convert_constants(constants), secondary)
        if cache_key in self.handlers:
            return self.handlers[cache_key]
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
        elif constants == AVAILABLE_INTERFACES:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_IMAGE_GENERATORS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == self.extensionloader.extensionsmap:
            model = self.extensionloader.extensionsmap[key]
            if model is None:
                raise Exception("Extension not found")
        else:
            raise Exception("Unknown constants")
        self.handlers[cache_key] = model
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
        elif issubclass(type(handler), ImageGeneratorHandler):
            return AVAILABLE_IMAGE_GENERATORS
        elif issubclass(type(handler), Interface):
            return AVAILABLE_INTERFACES
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
