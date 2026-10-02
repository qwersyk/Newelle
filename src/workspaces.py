"""Workspace persistence and execution boundaries shared by GUI and headless clients."""
import functools
import base64
import gettext
import inspect
import json
import os
import threading
import uuid
import weakref
from contextlib import contextmanager

from .modes import DEFAULT_MODE_NAME
from .utility.profile_settings import restore_settings_from_dict_by_groups
from .constants import SETTINGS_GROUPS

_ = gettext.gettext
DEFAULT_WORKSPACE = "default"


class WorkspaceStorage:
    """One history store shared by the controllers of all application windows."""

    def __init__(self):
        self.chats = {}
        self.folders = {}
        self.workspaces = {}
        self.next_chat_id = 0
        self.next_folder_id = 0
        self.scheduled_tasks = []
        self.lock = threading.RLock()
        self.save_lock = threading.Lock()
        self.scheduled_tasks_lock = threading.Lock()
        self.controllers = weakref.WeakSet()
        self.loaded = False
        self.refresh_pending = False
        self.drafts = {}


def shared_workspace_property(name):
    """Keep existing controller accessors backed by the shared storage."""
    return property(
        lambda self: getattr(self.workspace_storage, name),
        lambda self, value: setattr(self.workspace_storage, name, value),
    )


class WorkspaceBusyError(ValueError):
    """A workspace change conflicts with active work."""


def workspace_request(function):
    """Keep the workspace stable for the entire request, including generator iteration."""
    signature = inspect.signature(function)

    def request(self, args, kwargs):
        bound = signature.bind(self, *args, **kwargs)
        return self.workspace_request(bound.arguments.get("chat_id"))

    if inspect.isgeneratorfunction(function):
        @functools.wraps(function)
        def wrapper(self, *args, **kwargs):
            with request(self, args, kwargs):
                yield from function(self, *args, **kwargs)
    else:
        @functools.wraps(function)
        def wrapper(self, *args, **kwargs):
            with request(self, args, kwargs):
                return function(self, *args, **kwargs)
    return wrapper


def workspace_storage(function):
    """Serialize ID allocation and membership writes, including nested creation."""
    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        with self.workspace_lock:
            return function(self, *args, **kwargs)
    return wrapper


def workspace_change(function):
    """Reserve workspace mutations against concurrent request startup."""
    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        with self.workspace_lock:
            self.begin_workspace_switch()
            try:
                return function(self, *args, **kwargs)
            finally:
                self.workspace_switching = False
    return wrapper


class WorkspaceController:
    @workspace_storage
    def list_workspaces_info(self):
        return {
            "active_workspace_id": self.active_workspace_id,
            "workspaces": [dict(
                id=wid, name=value["name"], profile=value.get("profile"),
                path=value["path"], mode=value["mode"], icon=value.get("icon"),
                picture=base64.b64encode(value["picture"]).decode("ascii") if value.get("picture") else None,
                selected_chat=value.get("selected_chat"),
            ) for wid, value in self.workspaces.items()],
        }

    def remote_workspace_action(self, action, workspace_id=None, **data):
        """Serialize remote mutations on the GTK thread when a window is present."""
        from concurrent.futures import Future, TimeoutError
        from gi.repository import GLib

        ui = self.ui_controller
        if ui is not None and hasattr(ui, "window"):
            if threading.current_thread() is threading.main_thread():
                return ui.workspace_action(action, workspace_id, **data)
            future = Future()

            def invoke():
                if not future.set_running_or_notify_cancel():
                    return False
                try:
                    future.set_result(ui.workspace_action(action, workspace_id, **data))
                except WorkspaceBusyError as error:
                    future.set_exception(RuntimeError(str(error)))
                except Exception as error:
                    future.set_exception(error)
                return False

            GLib.idle_add(invoke)
            try:
                return future.result(timeout=30)
            except TimeoutError:
                if future.cancel():
                    raise RuntimeError(_("The desktop is busy. Try again."))
                return future.result()
        try:
            return self._remote_workspace_action(action, workspace_id, **data)
        except WorkspaceBusyError as error:
            raise RuntimeError(str(error)) from error

    def _remote_workspace_action(self, action, workspace_id=None, **data):
        if workspace_id is not None and workspace_id not in self.workspaces:
            raise KeyError("Workspace not found")
        if action == "path":
            if workspace_id != self.active_workspace_id:
                raise RuntimeError(_("Switch to this workspace before changing its folder."))
            self.set_workspace_directory(data["path"])
        elif action in ("create", "edit"):
            previous = self.workspaces.get(workspace_id, {})
            name = (data.get("name", previous.get("name", "")) or "").strip()
            path = os.path.abspath(os.path.expanduser(data.get("path") or previous.get("path") or self.settings.get_string("path")))
            profile = data.get("profile", previous.get("profile"))
            if not name or not os.path.isdir(path):
                raise ValueError(_("A name and an existing directory are required"))
            if profile and profile not in self.newelle_settings.profile_settings:
                raise ValueError(_("Profile not found"))
            mode = data.get("mode", previous.get("mode", self.mode_manager.get_active_mode_name()))
            if mode not in self.mode_manager.get_modes():
                raise ValueError(_("Mode not found"))
            if action == "create":
                workspace_id = self.create_workspace(name, profile, path)
            else:
                self.edit_workspace(workspace_id, name, profile, path)
            self.workspaces[workspace_id]["mode"] = mode
            self.save_chats()
            if action == "edit" and workspace_id == self.active_workspace_id and not hasattr(self.ui_controller, "window"):
                self.switch_workspace(workspace_id)
        elif action == "switch":
            self.switch_workspace(workspace_id)
        elif action == "delete":
            if workspace_id == DEFAULT_WORKSPACE:
                raise ValueError("The Default workspace cannot be deleted")
            if workspace_id == self.active_workspace_id:
                self.switch_workspace(DEFAULT_WORKSPACE)
            self.delete_workspace(workspace_id)
        elif action == "move":
            chat_id = data["chat_id"]
            if chat_id not in self.workspace_chats():
                raise KeyError("Chat not found in active workspace")
            self.move_chat_to_workspace(chat_id, workspace_id)
        else:
            raise ValueError("Unknown workspace action")
        return workspace_id

    def change_workspace_directory(self, path):
        workspace_id = self.active_workspace_id
        self.remote_workspace_action("path", workspace_id, path=path)
        return self.workspaces[workspace_id]["path"]

    @workspace_change
    def set_workspace_directory(self, path):
        path = os.path.expanduser(path.strip())
        if not path:
            raise ValueError(_("A folder path is required."))
        if not os.path.isabs(path):
            path = os.path.join(os.path.expanduser(self.active_workspace["path"]), path)
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise ValueError(_("Directory not found: {path}").format(path=path))
        if not getattr(self.settings, "workspace_window", False):
            os.chdir(path)
        self.active_workspace["path"] = path
        self.settings.set_string("path", path)
        self.newelle_settings.main_path = path
        self.workspace_path_notice = None
        self.skill_manager.skills_dirs = self._build_skills_dirs()
        self.skill_manager.discover()
        self.skill_manager.activated_skills.clear()
        self.skill_manager.set_mode_overrides(self.mode_manager.get_active_mode().get("skills", {}))
        self.require_tool_update()
        self.save_chats()

    def restore_cd_command(self, path="", msg_uuid=None, chat_id=None):
        # Reopening history must not execute a saved directory change again.
        from .tools import ToolResult
        from gi.repository import Gtk

        result = ToolResult()
        message = ("/cd " + path).strip()
        result.set_output(message)
        result.set_widget(Gtk.Label(label=message, wrap=True, selectable=True, xalign=0))
        return result

    def cd_command(self, path="", msg_uuid=None, chat_id=None):
        from .tools import ToolResult

        result = ToolResult()
        try:
            if chat_id is not None and chat_id not in self.workspace_chats():
                raise ValueError(_("Switch to this chat's workspace before continuing."))
            directory = self.change_workspace_directory(path) if path.strip() else self.active_workspace["path"]
            message = _("Workspace folder: {path}").format(path=directory)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            message = str(error)
        result.set_output(message)
        if threading.current_thread() is threading.main_thread() and hasattr(self.ui_controller, "window"):
            from gi.repository import Gtk
            result.set_widget(Gtk.Label(label=message, wrap=True, selectable=True, xalign=0))
        return result

    def init_workspace_state(self):
        storage = getattr(self, "workspace_storage", None)
        self.workspace_lock = storage.lock if storage else threading.RLock()
        self.workspace_local = threading.local()
        # Requests are tracked separately from the workspace selected in the
        # window.  A request keeps using the workspace of its chat even after
        # the user switches the visible workspace, so a global busy flag would
        # incorrectly prevent independent chats from running together.
        self.workspace_requests = 0
        self.workspace_chat_requests = 0
        self.workspace_requests_by_workspace = {}
        self.active_request_contexts = {}
        self.workspace_switching = False
        if storage is None:
            self.workspaces = {}
        self.active_workspace_id = DEFAULT_WORKSPACE
        self.workspace_path_notice = None

    @property
    def active_workspace(self):
        return self.workspaces[self.active_workspace_id]

    def workspace_chats(self, workspace_id=None):
        workspace_id = workspace_id or self.active_workspace_id
        return {cid: chat for cid, chat in self.chats.items()
                if chat.get("workspace_id", DEFAULT_WORKSPACE) == workspace_id}

    def workspace_folders(self, workspace_id=None):
        workspace_id = workspace_id or self.active_workspace_id
        return {fid: folder for fid, folder in self.folders.items()
                if folder.get("workspace_id", DEFAULT_WORKSPACE) == workspace_id}

    def _workspace_record(self, name, profile=None, path=None, appearance=None):
        appearance = appearance or {}
        return {"name": name, "profile": profile,
                "icon": appearance.get("icon"), "picture": appearance.get("picture"),
                "path": path or self.settings.get_string("path"),
                "mode": self.mode_manager.get_active_mode_name(),
                "open_chats": [], "selected_chat": None}

    @workspace_storage
    def load_workspaces(self, raw):
        storage = getattr(self, "workspace_storage", None)
        if storage is None or not storage.loaded:
            self.workspaces = raw.get("workspaces", {}) if isinstance(raw, dict) else {}
        self.workspaces.setdefault(DEFAULT_WORKSPACE, self._workspace_record(_("Default")))
        self.active_workspace_id = getattr(self, "initial_workspace_id", None) or (raw.get("active_workspace_id", DEFAULT_WORKSPACE) if isinstance(raw, dict) else DEFAULT_WORKSPACE)
        if self.active_workspace_id not in self.workspaces:
            self.active_workspace_id = DEFAULT_WORKSPACE
        for entry in (*self.chats.values(), *self.folders.values()):
            if entry.get("workspace_id") not in self.workspaces:
                entry["workspace_id"] = DEFAULT_WORKSPACE
        for workspace in self.workspaces.values():
            for key, value in self._workspace_record(workspace["name"]).items():
                workspace.setdefault(key, value)
        self.next_chat_id = max(self.next_chat_id, max(self.chats, default=-1) + 1)
        self.next_folder_id = max(self.next_folder_id, max(self.folders, default=-1) + 1)
        if storage is not None:
            storage.loaded = True
        profiles = json.loads(self.settings.get_string("profiles"))
        current_profile = self.newelle_settings.current_profile
        if current_profile not in profiles:
            profiles[current_profile] = self.newelle_settings.profile_settings[current_profile]
            self.settings.set_string("profiles", json.dumps(profiles))
        self.mode_manager.workspace_reference_changed = self.workspace_mode_reference_changed
        self.workspace_switching = True
        try:
            self.apply_workspace_settings()
            self.newelle_settings.load_settings(self.settings)
        finally:
            self.workspace_switching = False
        self.ensure_workspace_chat()
        self.settings.connect("changed::path", self._workspace_setting_changed)
        self.settings.connect("changed::current-mode", self._workspace_setting_changed)
        self.save_chats()

    def ensure_workspace_chat(self):
        chats = {cid: chat for cid, chat in self.workspace_chats().items() if not chat.get("call")}
        workspace = self.active_workspace
        selected = workspace.get("selected_chat")
        if selected not in chats:
            old = self.newelle_settings.chat_id
            selected = old if old in chats else next(iter(chats), None)
        if selected is None:
            selected = self.create_visible_chat()
        workspace["selected_chat"] = selected
        workspace["open_chats"] = [cid for cid in workspace["open_chats"] if cid in chats]
        if selected not in workspace["open_chats"]:
            workspace["open_chats"].append(selected)
        self.newelle_settings.chat_id = selected
        self.settings.set_int("chat", selected)
        return selected

    def _workspace_setting_changed(self, settings, key):
        if self.workspace_switching:
            return
        self.active_workspace["path" if key == "path" else "mode"] = settings.get_string(key)
        self.save_chats()

    def workspace_mode_reference_changed(self, old, new):
        for workspace in self.workspaces.values():
            if workspace["mode"] == old:
                workspace["mode"] = new
        self.save_chats()

    def apply_workspace_settings(self):
        workspace = self.active_workspace
        self.workspace_path_notice = None
        profiles = json.loads(self.settings.get_string("profiles"))
        profile = workspace.get("profile")
        if profile not in profiles:
            workspace["profile"] = None
        elif profile != self.settings.get_string("current-profile"):
            self.update_current_profile()
            saved = profiles[profile]
            restore_settings_from_dict_by_groups(self.settings, saved.get("settings", {}), saved.get("settings_groups", []), SETTINGS_GROUPS)
            self.settings.set_string("current-profile", profile)
        path = os.path.expanduser(workspace["path"])
        if not os.path.isdir(path) or not os.access(path, os.X_OK):
            self.workspace_path_notice = _("Workspace directory is unavailable. Using your home directory.")
            path = os.path.expanduser("~")
        self.settings.set_string("path", path)
        mode = workspace.get("mode", DEFAULT_MODE_NAME)
        if mode not in self.mode_manager.get_modes():
            mode = DEFAULT_MODE_NAME
            workspace["mode"] = mode
        self.mode_manager.set_active_mode(mode)
        if not getattr(self.settings, "workspace_window", False):
            os.chdir(path)
        self.skill_manager.skills_dirs = self._build_skills_dirs()
        self.skill_manager.discover()
        self.skill_manager.activated_skills.clear()
        self.skill_manager.set_mode_overrides(self.mode_manager.get_active_mode().get("skills", {}))

    @contextmanager
    def workspace_request(self, chat_id=None):
        previous_context = getattr(self.workspace_local, "context", None)
        with self.workspace_lock:
            if self.workspace_switching:
                raise RuntimeError(_("Workspace is switching. Please try again."))
            self.workspace_requests += 1
            self.workspace_chat_requests += 1
            workspace_id = None
            if chat_id in self.chats:
                workspace_id = self.chats[chat_id].get("workspace_id", DEFAULT_WORKSPACE)
            self.workspace_requests_by_workspace[workspace_id] = (
                self.workspace_requests_by_workspace.get(workspace_id, 0) + 1
            )
        try:
            # NewelleController supplies a request-local settings/handler
            # snapshot.  Keeping this hook here also leaves headless and
            # lightweight WorkspaceController users fully compatible.
            build_context = getattr(self, "build_workspace_request_context", None)
            if build_context is not None:
                self.workspace_local.context = build_context(chat_id)
                register_context = getattr(self, "register_workspace_request_context", None)
                if register_context is not None and self.workspace_local.context is not None:
                    register_context(chat_id, self.workspace_local.context)
            yield
        finally:
            unregister_context = getattr(self, "unregister_workspace_request_context", None)
            if unregister_context is not None:
                unregister_context(chat_id)
            if previous_context is None:
                self.workspace_local.__dict__.pop("context", None)
            else:
                self.workspace_local.context = previous_context
            with self.workspace_lock:
                self.workspace_requests -= 1
                self.workspace_chat_requests -= 1
                remaining = self.workspace_requests_by_workspace.get(workspace_id, 1) - 1
                if remaining > 0:
                    self.workspace_requests_by_workspace[workspace_id] = remaining
                else:
                    self.workspace_requests_by_workspace.pop(workspace_id, None)

    def begin_workspace_switch(self):
        with self.workspace_lock, self.scheduled_tasks_lock:
            # Requests and tool executions are tied to explicit chat IDs and
            # may continue while the UI activates another workspace.  Only a
            # concurrent workspace transition itself needs serialization.
            if self.workspace_switching:
                raise WorkspaceBusyError(_("Finish or stop active work before switching workspaces."))
            self.workspace_switching = True

    def switch_workspace(self, workspace_id):
        if workspace_id not in self.workspaces:
            raise ValueError(_("Workspace not found."))
        self.begin_workspace_switch()
        previous_id = self.active_workspace_id
        try:
            self.active_workspace_id = workspace_id
            self.apply_workspace_settings()
            self.ensure_workspace_chat()
            reloads = self.update_settings()
            self.require_tool_update()
            self.save_chats()
            return reloads
        except Exception:
            self.active_workspace_id = previous_id
            self.apply_workspace_settings()
            self.ensure_workspace_chat()
            self.update_settings()
            raise
        finally:
            self.workspace_switching = False

    @workspace_storage
    def create_workspace(self, name, profile=None, path=None, appearance=None):
        name = name.strip()
        if not name:
            raise ValueError(_("A workspace name is required."))
        workspace_id = str(uuid.uuid4())
        self.workspaces[workspace_id] = self._workspace_record(name, profile, path, appearance)
        chat_id = self.create_visible_chat(workspace_id=workspace_id)
        self.workspaces[workspace_id].update(open_chats=[chat_id], selected_chat=chat_id)
        self.save_chats()
        return workspace_id

    @workspace_change
    def edit_workspace(self, workspace_id, name, profile, path, appearance=None):
        self.check_workspace_windows(workspace_id)
        if not name.strip():
            raise ValueError(_("A workspace name is required."))
        self.workspaces[workspace_id].update(name=name.strip(), profile=profile, path=path)
        if appearance is not None:
            self.workspaces[workspace_id].update(
                icon=appearance.get("icon"), picture=appearance.get("picture"),
            )
        self.save_chats()

    @workspace_change
    def delete_workspace(self, workspace_id):
        self.check_workspace_windows(workspace_id, deleting=True)
        if workspace_id == DEFAULT_WORKSPACE:
            raise ValueError(_("The Default workspace cannot be deleted."))
        if workspace_id == self.active_workspace_id:
            raise ValueError(_("Switch workspaces before deleting the active workspace."))
        for entry in (*self.chats.values(), *self.folders.values()):
            if entry.get("workspace_id") == workspace_id:
                entry["workspace_id"] = DEFAULT_WORKSPACE
        with self.scheduled_tasks_lock:
            for task in self.scheduled_tasks:
                if task.get("workspace_id") == workspace_id:
                    task["workspace_id"] = DEFAULT_WORKSPACE
        self._persist_scheduled_tasks()
        target = self.workspaces[DEFAULT_WORKSPACE]
        for cid in self.workspaces[workspace_id]["open_chats"]:
            if cid in self.chats and cid not in target["open_chats"]:
                target["open_chats"].append(cid)
        del self.workspaces[workspace_id]
        self.save_chats()

    @workspace_change
    def move_chat_to_workspace(self, chat_id, workspace_id):
        self.check_workspace_windows(workspace_id)
        if workspace_id not in self.workspaces:
            raise ValueError(_("Workspace not found."))
        moving = {chat_id}
        while True:
            parents = {self.chats[cid].get("id") for cid in moving} - {None}
            children = {cid for cid, chat in self.chats.items() if chat.get("branched_from") in parents}
            if children <= moving:
                break
            moving |= children
        self.chats[chat_id]["branched_from"] = None
        for cid in moving:
            self.remove_chat_from_folder(cid, save=False)
            self.chats[cid]["workspace_id"] = workspace_id
        for workspace in self.workspaces.values():
            workspace["open_chats"] = [cid for cid in workspace["open_chats"] if cid not in moving]
            if workspace["selected_chat"] in moving:
                workspace["selected_chat"] = None
        self.ensure_workspace_chat()
        self.save_chats()
        return moving

    def check_workspace_windows(self, workspace_id, deleting=False):
        """Protect other windows' active sessions from membership/settings changes."""
        storage = getattr(self, "workspace_storage", None)
        if storage is None:
            return
        for controller in list(storage.controllers):
            if controller.workspace_requests:
                raise WorkspaceBusyError(_("Finish or stop active work before changing workspaces."))
            window = getattr(controller.ui_controller, "window", None)
            if window is None:
                continue
            if not getattr(window, "ui_built", False):
                raise WorkspaceBusyError(_("Wait for the workspace window to finish loading."))
            if window.workspace_ui_busy() and controller is not self:
                raise WorkspaceBusyError(_("Finish or stop active work before changing workspaces."))
            if deleting and controller is not self and controller.active_workspace_id == workspace_id:
                raise WorkspaceBusyError(_("Close this workspace's window before deleting it."))
