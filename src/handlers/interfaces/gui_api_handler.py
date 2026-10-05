import io
import json
import os
import queue
import re
import tempfile
import threading
import time
import uuid

from ..extra_settings import ExtraSettings
from .interface import Interface


class GUIAPIInterface(Interface):
    key = "gui-api"
    name = "Newelle GUI API"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._server = None
        self._error = None

    @staticmethod
    def get_extra_requirements() -> list:
        return ["fastapi", "uvicorn"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting(
                key="api_key",
                title=_("API Key"),
                description=_("API key required to authenticate requests (leave empty to disable authentication)"),
                default="",
                password=True,
            ),
            ExtraSettings.EntrySetting(
                key="host",
                title=_("Host"),
                description=_("Host address to bind the API server to"),
                default="127.0.0.1",
            ),
            ExtraSettings.SpinSetting(
                key="port",
                title=_("Port"),
                description=_("Port to bind the API server to"),
                default=8081,
                min=1,
                max=65535,
                step=1,
            ),
        ]

    def _get_port(self):
        return self.get_setting("port", search_default=True, return_value=8081)

    def _get_host(self):
        return self.get_setting("host", search_default=True, return_value="127.0.0.1")

    # ------------------------------------------------------------------ #
    #                        FastAPI app factory                          #
    # ------------------------------------------------------------------ #
    def _create_app(self):
        from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request, Body
        from fastapi.responses import JSONResponse, StreamingResponse, Response
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel, Field
        from typing import Any, Optional
        from starlette.middleware.base import BaseHTTPMiddleware

        controller = self.controller
        api_key = self.get_setting("api_key", search_default=True, return_value="")

        # Pending tool interactions: {interaction_id: {options, event, result}}
        pending_interactions: dict = {}

        class APIKeyMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if api_key:
                    auth_header = request.headers.get("Authorization", "")
                    bearer = f"Bearer {api_key}"
                    api_key_param = request.query_params.get("api_key", "")
                    if auth_header != bearer and api_key_param != api_key:
                        return JSONResponse(
                            status_code=401,
                            content={"error": "Invalid or missing API key"},
                        )
                chat_match = re.match(r"^/api/chats/(\d+)(?:/|$)", request.url.path)
                if chat_match and int(chat_match.group(1)) not in controller.chats:
                    return JSONResponse(status_code=404, content={"detail": "Chat not found"})
                if chat_match and int(chat_match.group(1)) not in controller.workspace_chats():
                    return JSONResponse(status_code=409, content={"detail": "Switch to the chat's workspace first"})
                return await call_next(request)

        app = FastAPI(title="Newelle GUI API", version="1.0.0")
        app.add_middleware(APIKeyMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # ---- Pydantic models ---- #
        class CreateChatRequest(BaseModel):
            name: Optional[str] = None
            profile: Optional[str] = None
            folder_id: Optional[int] = None
            workspace_id: Optional[str] = None

        class WorkspaceRequest(BaseModel):
            name: Optional[str] = None
            profile: Optional[str] = None
            path: Optional[str] = None
            mode: Optional[str] = None

        class MoveWorkspaceRequest(BaseModel):
            workspace_id: str

        class WorkspacePathRequest(BaseModel):
            path: str

        class RenameChatRequest(BaseModel):
            name: str

        class SendMessageRequest(BaseModel):
            message: str
            chat_id: int
            stream: bool = False

        class RunLLMRequest(BaseModel):
            message: str
            chat_id: int
            system_prompt: Optional[list[str]] = None
            max_tool_calls: Optional[int] = None
            save_chat: bool = False

        class SetPromptActiveRequest(BaseModel):
            prompt_key: str
            active: bool

        class SetCustomPromptRequest(BaseModel):
            prompt_key: str
            text: str

        class DeleteCustomPromptRequest(BaseModel):
            prompt_key: str

        class SetToolEnabledRequest(BaseModel):
            tool_name: str
            enabled: bool

        class SetInterfaceEnabledRequest(BaseModel):
            interface_key: str
            enabled: bool

        class CreateProfileRequest(BaseModel):
            profile_name: str
            picture: Optional[str] = None
            settings: Optional[dict] = Field(default_factory=dict)
            settings_groups: Optional[list] = Field(default_factory=list)

        class DeleteProfileRequest(BaseModel):
            profile_name: str

        class ImportProfileRequest(BaseModel):
            profile_data: dict

        class CreateFolderRequest(BaseModel):
            name: str
            color: str
            icon: str = "folder-symbolic"

        class RenameFolderRequest(BaseModel):
            name: str

        class UpdateFolderColorRequest(BaseModel):
            color: str

        class UpdateFolderIconRequest(BaseModel):
            icon: str

        class MoveChatToFolderRequest(BaseModel):
            chat_id: int
            folder_id: int

        class RemoveChatFromFolderRequest(BaseModel):
            chat_id: int

        class CreateScheduledTaskRequest(BaseModel):
            task: str
            run_at: Optional[str] = None
            cron: Optional[str] = None
            folder_id: Optional[int] = None

        class SetScheduledTaskEnabledRequest(BaseModel):
            task_id: str
            enabled: bool

        class DeleteScheduledTaskRequest(BaseModel):
            task_id: str

        class SetScheduledTaskFolderRequest(BaseModel):
            task_id: str
            folder_id: int

        class PatchSettingsRequest(BaseModel):
            settings: dict

        class SwitchProfileRequest(BaseModel):
            profile: str

        class CreateBranchRequest(BaseModel):
            message_id: int
            source_chat_id: Optional[int] = None

        class ToolInteractRequest(BaseModel):
            interaction_id: str
            option_index: int

        class ReloadRequest(BaseModel):
            reload_type: str

        class SetActiveModeRequest(BaseModel):
            mode: str

        class ModeMutationRequest(BaseModel):
            # Keep these loose so malformed editor payloads receive the API's
            # deliberate 400 responses instead of framework-level 422 errors.
            name: Optional[Any] = None
            description: Optional[Any] = None
            icon: Optional[Any] = None
            tools: Optional[Any] = None
            skills: Optional[Any] = None
            prompts: Optional[Any] = None

        class SetExtensionEnabledRequest(BaseModel):
            extension_id: str
            enabled: bool

        class ExtensionSettingsRequest(BaseModel):
            settings: dict = Field(default_factory=dict)

        class DeleteExtensionRequest(BaseModel):
            extension_id: str

        class SetSkillEnabledRequest(BaseModel):
            name: str
            enabled: bool

        class SetSttProviderRequest(BaseModel):
            provider: str

        class SetSttSettingsRequest(BaseModel):
            provider: Optional[str] = None
            settings: dict = Field(default_factory=dict)

        class ProviderSettingsRequest(BaseModel):
            provider: Optional[str] = None
            enabled: Optional[bool] = None
            settings: dict = Field(default_factory=dict)
            details: dict = Field(default_factory=dict)

        class ProviderActionRequest(BaseModel):
            provider: Optional[str] = None
            key: str
            action: str = "primary"

        class InterfaceSettingsRequest(BaseModel):
            settings: dict = Field(default_factory=dict)

        class InterfaceActionRequest(BaseModel):
            key: str
            action: str = "primary"

        class PermissionSettingsRequest(BaseModel):
            auto_run: Optional[Any] = None
            max_run_times: Optional[Any] = None
            default_action: Optional[Any] = None
            file_rules: Optional[Any] = None
            command_rules: Optional[Any] = None
            path_rules: Optional[Any] = None

        # ============================================================ #
        #                         BOOTSTRAP                             #
        # ============================================================ #
        @app.post("/api/bootstrap/reload")
        def api_reload(req: ReloadRequest):
            from ...constants import ReloadType
            try:
                rt = ReloadType[req.reload_type]
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Unknown reload type: {req.reload_type}")
            controller.reload(rt)
            return {"status": "ok"}

        @app.post("/api/bootstrap/close")
        def api_close():
            controller.close_application()
            return {"status": "ok"}

        @app.get("/api/bootstrap/llm-loading")
        def api_llm_loading():
            controller.wait_llm_loading()
            return {"status": "loaded"}

        # ============================================================ #
        #                           CHATS                               #
        # ============================================================ #
        def workspace_action(action, workspace_id=None, **data):
            try:
                wid = controller.remote_workspace_action(action, workspace_id, **data)
                return {"workspace_id": wid, **controller.list_workspaces_info()}
            except KeyError as error:
                raise HTTPException(status_code=404, detail=str(error))
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error))

        @app.get("/api/workspaces")
        def api_workspaces():
            return controller.list_workspaces_info()

        @app.post("/api/workspaces")
        def api_create_workspace(req: WorkspaceRequest):
            return workspace_action("create", **req.model_dump(exclude_unset=True))

        @app.patch("/api/workspaces/{workspace_id}")
        def api_edit_workspace(workspace_id: str, req: WorkspaceRequest):
            return workspace_action("edit", workspace_id, **req.model_dump(exclude_unset=True))

        @app.post("/api/workspaces/{workspace_id}/switch")
        def api_switch_workspace(workspace_id: str):
            return workspace_action("switch", workspace_id)

        @app.post("/api/workspaces/{workspace_id}/path")
        def api_workspace_path(workspace_id: str, req: WorkspacePathRequest):
            return workspace_action("path", workspace_id, path=req.path)

        @app.delete("/api/workspaces/{workspace_id}")
        def api_delete_workspace(workspace_id: str):
            return workspace_action("delete", workspace_id)

        @app.post("/api/chats/{chat_id}/workspace")
        def api_move_workspace(chat_id: int, req: MoveWorkspaceRequest):
            return workspace_action("move", req.workspace_id, chat_id=chat_id)

        @app.get("/api/chats")
        def api_list_chats():
            """List all chats with metadata (list_chats_info equivalent)."""
            ids = controller.chat_ids_ordered()
            result = []
            for cid in ids:
                chat_data = controller.chats.get(cid)
                if chat_data is None:
                    continue
                entry = {
                    "id": cid,
                    "name": chat_data.get("name", ""),
                    "message_count": len(chat_data.get("chat", [])),
                    "folder_id": controller.get_folder_for_chat(cid),
                    "profile": chat_data.get("profile"),
                    "call": chat_data.get("call", False),
                    "workspace_id": chat_data.get("workspace_id"),
                }
                result.append(entry)
            return result

        @app.get("/api/chats/{chat_id}/history")
        def api_get_chat_history(chat_id: int):
            """Get full message timeline for one chat."""
            if chat_id not in controller.workspace_chats():
                raise HTTPException(status_code=404, detail="Chat not found")
            return controller.get_chat_by_id(chat_id)

        @app.post("/api/chats")
        def api_create_chat(req: CreateChatRequest):
            if req.workspace_id and req.workspace_id not in controller.workspaces:
                raise HTTPException(status_code=404, detail="Workspace not found")
            chat_id = controller.create_visible_chat(
                name=req.name, profile=req.profile, folder_id=req.folder_id, workspace_id=req.workspace_id
            )
            return {"chat_id": chat_id}

        @app.put("/api/chats/{chat_id}/rename")
        def api_rename_chat(chat_id: int, req: RenameChatRequest):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            controller.rename_chat(chat_id, req.name)
            return {"status": "ok"}

        @app.delete("/api/chats/{chat_id}")
        def api_delete_chat(chat_id: int):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            controller.remove_chat_from_folder(chat_id)
            del controller.chats[chat_id]
            controller.save_chats()
            return {"status": "ok"}

        @app.get("/api/chats/ids")
        def api_chat_ids_ordered():
            return controller.chat_ids_ordered()

        @app.get("/api/chats/{chat_id}")
        def api_get_chat_by_id(chat_id: int):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            return {
                "id": chat_id,
                "data": controller.chats[chat_id],
            }

        @app.put("/api/chats/{chat_id}")
        def api_set_chat_by_id(chat_id: int, messages: list = Body(...)):
            if chat_id not in controller.workspace_chats():
                raise HTTPException(status_code=404, detail="Chat not found")
            controller.set_chat_by_id(chat_id, messages)
            controller.save_chats()
            return {"status": "ok"}

        @app.post("/api/chats/call")
        def api_create_call_chat():
            chat_id = controller.create_call_chat()
            return {"chat_id": chat_id}

        @app.post("/api/chats/{chat_id}/branch")
        def api_create_branch(chat_id: int, req: CreateBranchRequest):
            """Branch chat at a message point (delegates to window if available)."""
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            source = req.source_chat_id if req.source_chat_id is not None else chat_id
            window.create_branch(req.message_id, source)
            return {"status": "ok"}

        @app.post("/api/chats/{chat_id}/copy")
        def api_copy_chat(chat_id: int):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            source = controller.chats[chat_id]
            new_id = controller.create_visible_chat(name=source["name"] + " (copy)", workspace_id=source.get("workspace_id"))
            controller.chats[new_id]["chat"] = [m.copy() for m in source.get("chat", [])]
            controller.save_chats()
            return {"chat_id": new_id}

        @app.post("/api/chats/{chat_id}/choose")
        def api_choose_chat(chat_id: int):
            if chat_id not in controller.workspace_chats():
                raise HTTPException(status_code=409, detail="Switch to the chat's workspace first")
            controller.newelle_settings.chat_id = chat_id
            controller.settings.set_int("chat", chat_id)
            controller.active_workspace["selected_chat"] = chat_id
            controller.save_chats()
            return {"status": "ok"}

        # ============================================================ #
        #                         MESSAGES                              #
        # ============================================================ #
        @app.post("/api/messages/run-llm")
        async def api_run_llm_with_tools(req: RunLLMRequest):
            if req.chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            try:
                result = controller.run_llm_with_tools(
                    message=req.message,
                    chat_id=req.chat_id,
                    system_prompt=req.system_prompt,
                    max_tool_calls=req.max_tool_calls,
                    save_chat=req.save_chat,
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            return {"message": result}

        @app.get("/api/chats/{chat_id}/messages/{message_id}/console-reply")
        def api_get_console_reply(chat_id: int, message_id: int):
            result = controller.get_console_reply(chat_id, message_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Console reply not found")
            return {"reply": result}

        @app.get("/api/chats/{chat_id}/messages/{message_id}/tool-response")
        def api_get_tool_response(chat_id: int, message_id: int, tool_name: str, tool_uuid: str):
            result = controller.get_tool_response(chat_id, message_id, tool_name, tool_uuid)
            if result is None:
                raise HTTPException(status_code=404, detail="Tool response not found")
            return {"response": result}

        @app.get("/api/chats/{chat_id}/messages/{message_id}/tool-call-uuid")
        def api_get_tool_call_uuid(chat_id: int, message_id: int, tool_name: str, tool_call_index: int = 0):
            result = controller.get_tool_call_uuid(chat_id, message_id, tool_name, tool_call_index)
            return {"uuid": result}

        @app.post("/api/messages/send")
        def api_send_message(manual: bool = True):
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.send_message(manual=manual)
            return {"status": "ok"}

        @app.post("/api/messages/continue")
        def api_continue_message():
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.continue_message(None)
            return {"status": "ok"}

        @app.post("/api/messages/regenerate")
        def api_regenerate_message():
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.regenerate_message()
            return {"status": "ok"}

        @app.post("/api/messages/stop")
        def api_stop_chat():
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.stop_chat()
            return {"status": "ok"}

        @app.post("/api/messages/{message_id}/reload")
        def api_reload_message(message_id: int):
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.reload_message(message_id)
            return {"status": "ok"}

        @app.post("/api/messages/add-prompt")
        def api_add_prompt(prompt: Optional[str] = None):
            window = _get_window(controller)
            if window is None:
                raise HTTPException(status_code=503, detail="Window not available")
            window.add_prompt(prompt)
            return {"status": "ok"}

        # ============================================================ #
        #                          PROMPTS                              #
        # ============================================================ #
        @app.get("/api/prompts")
        def api_list_prompts():
            """List prompts shown in settings (one row per key; no duplicates)."""
            from ...constants import PROMPTS, AVAILABLE_PROMPTS
            ns = getattr(controller, 'newelle_settings', None)
            ps = (ns.prompts_settings if ns else None) or {}
            merged = getattr(ns, 'prompts', None) if ns else None
            result = []
            for prompt in AVAILABLE_PROMPTS:
                if not prompt.get("show_in_settings", True):
                    continue
                key = prompt.get("key")
                setting_name = prompt.get("setting_name")
                if setting_name in ps:
                    is_active = ps[setting_name]
                else:
                    is_active = prompt.get("default", False)
                if isinstance(merged, dict) and key in merged:
                    text = merged[key]
                else:
                    raw = PROMPTS.get(key, "")
                    text = raw if isinstance(raw, str) else ""
                result.append({
                    "key": key,
                    "name": str(prompt.get("title", key)),
                    "description": str(prompt.get("description", "")),
                    "active": bool(is_active),
                    "editable": bool(prompt.get("editable", False)),
                    "text": text,
                })
            return result

        @app.post("/api/prompts/set-active")
        def api_set_prompt_active(req: SetPromptActiveRequest):
            if not hasattr(controller, 'newelle_settings'):
                raise HTTPException(status_code=503, detail="Settings not loaded")
            from ...constants import AVAILABLE_PROMPTS
            ns = controller.newelle_settings
            setting_name = None
            for p in AVAILABLE_PROMPTS:
                if p.get("key") == req.prompt_key:
                    setting_name = p.get("setting_name")
                    break
            if not setting_name:
                raise HTTPException(status_code=400, detail="Unknown prompt key")
            if not isinstance(ns.prompts_settings, dict):
                ns.prompts_settings = {}
            ns.prompts_settings[setting_name] = req.active
            ns.load_prompts()
            ns.save_prompts()
            return {"status": "ok"}

        @app.post("/api/prompts/set-custom")
        def api_set_custom_prompt(req: SetCustomPromptRequest):
            if not hasattr(controller, 'newelle_settings'):
                raise HTTPException(status_code=503, detail="Settings not loaded")
            ns = controller.newelle_settings
            if not isinstance(ns.custom_prompts, dict):
                ns.custom_prompts = {}
            ns.custom_prompts[req.prompt_key] = req.text
            ns.settings.set_string("custom-prompts", json.dumps(ns.custom_prompts))
            ns.load_prompts()
            ns.save_prompts()
            return {"status": "ok"}

        @app.post("/api/prompts/delete-custom")
        def api_delete_custom_prompt(req: DeleteCustomPromptRequest):
            if not hasattr(controller, 'newelle_settings'):
                raise HTTPException(status_code=503, detail="Settings not loaded")
            ns = controller.newelle_settings
            if isinstance(ns.custom_prompts, dict):
                ns.custom_prompts.pop(req.prompt_key, None)
            ns.settings.set_string("custom-prompts", json.dumps(ns.custom_prompts))
            ns.load_prompts()
            ns.save_prompts()
            return {"status": "ok"}

        # ============================================================ #
        #                     TOOLS / COMMANDS                          #
        # ============================================================ #
        @app.get("/api/tools")
        def api_list_tools():
            """List all tools with enabled status."""
            all_tools = controller.tools.get_all_tools()
            tools_settings = {}
            if hasattr(controller, 'newelle_settings'):
                tools_settings = controller.newelle_settings.tools_settings_dict
            result = []
            for tool in all_tools:
                is_enabled = tool.default_on
                if tool.name in tools_settings and "enabled" in tools_settings[tool.name]:
                    is_enabled = tools_settings[tool.name]["enabled"]
                if tool.name == "search" and hasattr(controller, 'newelle_settings') and not controller.newelle_settings.websearch_on:
                    is_enabled = False
                # The tools panel manages the Normal (profile) configuration;
                # the active Mode may still override the value at runtime.
                mode_override = None
                mode_manager = getattr(controller, "mode_manager", None)
                if mode_manager is not None:
                    override = mode_manager.get_tool_override(tool.name)
                    if override in ("enable", "remove"):
                        mode_override = override
                result.append({
                    "name": tool.name,
                    "description": getattr(tool, 'description', ''),
                    "enabled": is_enabled,
                    "mode_override": mode_override,
                    "default_on": tool.default_on,
                    "tools_group": getattr(tool, 'tools_group', None),
                    "icon_name": getattr(tool, 'icon_name', None),
                    "title": getattr(tool, 'title', None) or tool.name,
                })
            return result

        @app.post("/api/tools/set-enabled")
        def api_set_tool_enabled(req: SetToolEnabledRequest):
            if not hasattr(controller, 'newelle_settings'):
                raise HTTPException(status_code=503, detail="Settings not loaded")
            ts = controller.newelle_settings.tools_settings_dict
            if req.tool_name not in ts:
                ts[req.tool_name] = {}
            ts[req.tool_name]["enabled"] = req.enabled
            controller.newelle_settings.tools_settings_dict = ts
            controller.settings.set_string("tools-settings", json.dumps(ts))
            return {"status": "ok"}

        @app.post("/api/tools/require-update")
        def api_require_tool_update():
            controller.require_tool_update()
            return {"status": "ok"}

        @app.get("/api/tools/enabled")
        def api_get_enabled_tools():
            tools = controller.get_enabled_tools()
            return [{"name": t.name, "description": getattr(t, 'description', '')} for t in tools]

        @app.get("/api/commands")
        def api_list_commands():
            commands = controller.get_commands()
            result = []
            for cmd in commands:
                result.append({
                    "name": cmd.name,
                    "description": getattr(cmd, 'description', ''),
                })
            return result

        @app.get("/api/commands/{name}")
        def api_get_command(name: str):
            cmd = controller.get_command(name)
            if cmd is None:
                raise HTTPException(status_code=404, detail="Command not found")
            return {"name": cmd.name, "description": getattr(cmd, 'description', '')}

        @app.post("/api/tools/mcp-update")
        def api_update_mcp_tools():
            controller.update_mcp_tools()
            return {"status": "ok"}

        @app.post("/api/tools/interact")
        def api_tool_interact(req: ToolInteractRequest):
            """Respond to a pending tool interaction (e.g. accept/skip a command)."""
            entry = pending_interactions.get(req.interaction_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="Interaction not found or expired")
            if req.option_index < 0 or req.option_index >= len(entry["options"]):
                raise HTTPException(status_code=400, detail="Invalid option index")
            entry["options"][req.option_index].callback()
            entry["event"].set()
            return {"status": "ok"}

        @app.get("/api/tools/mcp")
        def api_get_mcp_integration():
            mcp = controller.get_mcp_integration()
            if mcp is None:
                return {"integration": None}
            return {"integration": {"id": mcp.id, "name": getattr(mcp, 'name', mcp.id)}}

        # ============================================================ #
        #                        INTERFACES                             #
        # ============================================================ #
        @app.get("/api/interfaces")
        def api_list_interfaces():
            from ...constants import AVAILABLE_INTERFACES
            result = []
            for key, info in AVAILABLE_INTERFACES.items():
                iface = controller.handlers.interfaces.get(key) if hasattr(controller.handlers, 'interfaces') else None
                enabled = iface.get_setting("enabled", False, False) if iface else False
                result.append({
                    "key": key,
                    "name": info.get("title", key),
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                    "enabled": bool(enabled),
                    "running": iface.is_running() if iface else False,
                    "error": getattr(iface, '_error', None) if iface else None,
                    "has_settings": bool(iface and iface.get_extra_settings()),
                })
            return result

        @app.post("/api/interfaces/set-enabled")
        def api_set_interface_enabled(req: SetInterfaceEnabledRequest):
            iface = (
                controller.handlers.interfaces.get(req.interface_key)
                if hasattr(controller.handlers, "interfaces")
                else None
            )
            if iface is None:
                raise HTTPException(status_code=404, detail="Interface not found")
            iface.set_setting("enabled", req.enabled)
            return {"status": "ok"}

        @app.get("/api/interfaces/{interface_key}/settings")
        def api_get_interface_settings(interface_key: str):
            iface = (
                controller.handlers.interfaces.get(interface_key)
                if hasattr(controller.handlers, "interfaces")
                else None
            )
            if iface is None:
                raise HTTPException(status_code=404, detail="Interface not found")
            return {
                "interface": interface_key,
                "settings": _serialize_extra_settings(
                    iface.get_extra_settings(), lambda key: iface.get_setting(key)
                ),
            }

        @app.put("/api/interfaces/{interface_key}/settings")
        def api_set_interface_settings(
            interface_key: str, req: InterfaceSettingsRequest
        ):
            iface = (
                controller.handlers.interfaces.get(interface_key)
                if hasattr(controller.handlers, "interfaces")
                else None
            )
            if iface is None:
                raise HTTPException(status_code=404, detail="Interface not found")
            allowed = {
                setting["key"]
                for setting in _flatten_extra_settings(iface.get_extra_settings())
                if setting.get("type") not in ("button", "download")
            }
            unknown = set(req.settings) - allowed
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown interface setting: {sorted(unknown)[0]}",
                )
            for key, value in req.settings.items():
                iface.set_setting(key, value)
            return {"status": "ok"}

        @app.post("/api/interfaces/{interface_key}/settings/action")
        def api_run_interface_setting_action(
            interface_key: str, req: InterfaceActionRequest
        ):
            iface = (
                controller.handlers.interfaces.get(interface_key)
                if hasattr(controller.handlers, "interfaces")
                else None
            )
            if iface is None:
                raise HTTPException(status_code=404, detail="Interface not found")
            setting = _find_extra_setting(iface.get_extra_settings(), req.key)
            if setting is None:
                raise HTTPException(status_code=404, detail="Setting action not found")
            callback = setting.get("refresh") if req.action == "refresh" else setting.get("callback")
            if not callable(callback):
                raise HTTPException(status_code=400, detail="Setting has no such action")
            threading.Thread(target=callback, args=(None,), daemon=True).start()
            return {"status": "ok"}

        @app.get("/api/interfaces/{interface_key}/running")
        def api_is_interface_running(interface_key: str):
            if hasattr(controller.handlers, 'interfaces'):
                iface = controller.handlers.interfaces.get(interface_key)
                if iface:
                    return {"running": iface.is_running()}
            return {"running": False}

        @app.post("/api/interfaces/{interface_key}/start")
        def api_start_interface(interface_key: str):
            if hasattr(controller.handlers, 'interfaces'):
                iface = controller.handlers.interfaces.get(interface_key)
                if iface:
                    iface.start()
                    return {"status": "ok"}
            raise HTTPException(status_code=404, detail="Interface not found")

        @app.post("/api/interfaces/{interface_key}/stop")
        def api_stop_interface(interface_key: str):
            if hasattr(controller.handlers, 'interfaces'):
                iface = controller.handlers.interfaces.get(interface_key)
                if iface:
                    iface.stop()
                    return {"status": "ok"}
            raise HTTPException(status_code=404, detail="Interface not found")

        @app.get("/api/interfaces/{interface_key}/error")
        def api_get_interface_error(interface_key: str):
            if hasattr(controller.handlers, 'interfaces'):
                iface = controller.handlers.interfaces.get(interface_key)
                if iface:
                    return {"error": getattr(iface, '_error', None)}
            return {"error": None}

        @app.get("/api/interfaces/enabled-map")
        def api_get_interfaces_enabled_map():
            if not hasattr(controller.handlers, "interfaces"):
                return {}
            return {
                key: bool(iface.get_setting("enabled", False, False))
                for key, iface in controller.handlers.interfaces.items()
            }

        # ============================================================ #
        #                         PROFILES                              #
        # ============================================================ #
        @app.get("/api/profiles")
        def api_list_profiles():
            profiles = controller.newelle_settings.profile_settings
            current = controller.settings.get_string("current-profile")
            result = []
            for name, data in profiles.items():
                result.append({
                    "name": name,
                    "picture": data.get("picture"),
                    "settings_groups": data.get("settings_groups", []),
                    "current": name == current,
                })
            return result

        @app.get("/api/profiles/current")
        def api_get_current_profile():
            current = controller.settings.get_string("current-profile")
            return {"profile": current}

        @app.post("/api/profiles")
        def api_create_profile(req: CreateProfileRequest):
            controller.create_profile(
                req.profile_name, req.picture, req.settings, req.settings_groups
            )
            return {"status": "ok"}

        @app.delete("/api/profiles/{profile_name}")
        def api_delete_profile(profile_name: str):
            controller.delete_profile(profile_name)
            return {"status": "ok"}

        @app.post("/api/profiles/update-current")
        def api_update_current_profile():
            controller.update_current_profile()
            return {"status": "ok"}

        @app.get("/api/profiles/{profile_name}/export")
        def api_export_profile(profile_name: str, remove_passwords: bool = False, export_propic: bool = False):
            result = controller.export_profile(profile_name, remove_passwords, export_propic)
            return result

        @app.post("/api/profiles/import")
        def api_import_profile(req: ImportProfileRequest):
            controller.import_profile(req.profile_data)
            return {"status": "ok"}

        @app.post("/api/profiles/switch")
        def api_switch_profile(req: SwitchProfileRequest):
            if req.profile not in controller.newelle_settings.profile_settings:
                raise HTTPException(status_code=404, detail=f"Profile '{req.profile}' not found")
            window = _get_window(controller)
            if window is not None:
                window.switch_profile(req.profile)
            else:
                controller.switch_profile(req.profile)
            return {"status": "ok"}

        # ============================================================ #
        #                           MODES                               #
        # ============================================================ #
        def _get_mode_manager():
            mm = getattr(controller, "mode_manager", None)
            if mm is None:
                raise HTTPException(status_code=503, detail="Modes not available")
            return mm

        def _refresh_active_mode():
            """Reapply all active overlays after a mode mutation or switch."""
            mm = _get_mode_manager()
            active = mm.get_active_mode()
            skill_manager = getattr(controller, "skill_manager", None)
            if skill_manager is not None:
                skill_manager.set_mode_overrides(active.get("skills", {}))
            controller.update_settings()
            _refresh_mode_buttons()

        def _refresh_mode_buttons():
            """Keep any open desktop mode switchers in sync with API edits."""
            window = _get_window(controller)
            if window is not None and hasattr(window, "refresh_mode_buttons"):
                window.refresh_mode_buttons()

        def _serialize_mode(name, mode, mm):
            from ...modes import BUILT_IN_MODE_NAMES
            return {
                "name": name,
                "description": mode.get("description", ""),
                "icon": mode.get("icon", ""),
                "tools": mode.get("tools", {}),
                "skills": mode.get("skills", {}),
                "prompts": mode.get("prompts", {}),
                "current": name == mm.get_active_mode_name(),
                "builtin": name in BUILT_IN_MODE_NAMES,
            }

        def _validate_mode_mutation(req, require_name=False):
            """Validate editor input and return normalized optional fields."""
            from ...modes import VALID_STATES

            if require_name and req.name is None:
                raise HTTPException(status_code=400, detail="Mode name is required")
            if req.name is not None:
                if not isinstance(req.name, str) or not req.name.strip():
                    raise HTTPException(
                        status_code=400, detail="Mode name cannot be blank"
                    )

            for field_name in ("tools", "skills"):
                state_map = getattr(req, field_name)
                if state_map is None:
                    continue
                if not isinstance(state_map, dict):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{field_name.title()} overrides must be an object",
                    )
                for key, state in state_map.items():
                    if not isinstance(key, str) or state not in VALID_STATES:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Invalid {field_name[:-1]} override for "
                                f"'{key}': expected no_change, enable, or remove"
                            ),
                        )

            if req.prompts is not None:
                if not isinstance(req.prompts, dict):
                    raise HTTPException(
                        status_code=400,
                        detail="Prompt overrides must be an object",
                    )
                for key, config in req.prompts.items():
                    if not isinstance(key, str) or not isinstance(config, dict):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid prompt override for '{key}'",
                        )
                    state = config.get("state", "no_change")
                    if state not in VALID_STATES:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Invalid prompt override for '{key}': expected "
                                "no_change, enable, or remove"
                            ),
                        )
                    if "override" in config and not isinstance(
                        config["override"], str
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Prompt text override for '{key}' must be a string",
                        )

            if req.description is not None and not isinstance(req.description, str):
                raise HTTPException(
                    status_code=400, detail="Mode description must be a string"
                )
            if req.icon is not None and not isinstance(req.icon, str):
                raise HTTPException(
                    status_code=400, detail="Mode icon must be a string"
                )

        def _raise_mode_mutation_error(error):
            from ...modes import (
                InvalidModeNameError,
                ModeAlreadyExistsError,
                ModeNotFoundError,
                ProtectedModeError,
            )
            if isinstance(error, ModeAlreadyExistsError):
                raise HTTPException(status_code=409, detail=str(error))
            if isinstance(error, ModeNotFoundError):
                raise HTTPException(status_code=404, detail=str(error))
            if isinstance(error, (InvalidModeNameError, ProtectedModeError)):
                raise HTTPException(status_code=400, detail=str(error))
            raise error

        @app.get("/api/modes")
        def api_list_modes():
            """List all modes with the active one flagged."""
            mm = _get_mode_manager()
            active = mm.get_active_mode_name()
            from ...modes import BUILT_IN_MODE_NAMES
            result = []
            for name, mode in mm.get_modes().items():
                result.append({
                    "name": name,
                    "description": mode.get("description", ""),
                    "icon": mode.get("icon", ""),
                    "current": name == active,
                    "builtin": name in BUILT_IN_MODE_NAMES,
                })
            return result

        @app.get("/api/modes/current")
        def api_get_current_mode():
            mm = _get_mode_manager()
            return {"mode": mm.get_active_mode_name()}

        @app.get("/api/modes/editor-options")
        def api_get_mode_editor_options():
            """Return profile prompts and every currently available override target."""
            from ...constants import AVAILABLE_PROMPTS, PROMPTS
            from ...modes import MODE_ICON_CHOICES, VALID_STATES

            ns = getattr(controller, "newelle_settings", None)
            profile_prompts = getattr(ns, "prompts", {}) if ns is not None else {}
            prompts = []
            for prompt in AVAILABLE_PROMPTS:
                key = prompt.get("key")
                fallback = PROMPTS.get(key, "")
                profile_text = (
                    profile_prompts.get(key, fallback)
                    if isinstance(profile_prompts, dict)
                    else fallback
                )
                if not isinstance(profile_text, str):
                    profile_text = fallback if isinstance(fallback, str) else ""
                prompts.append({
                    "key": key,
                    "name": str(prompt.get("title", key)),
                    "description": str(prompt.get("description", "")),
                    "profile_text": profile_text,
                })

            grouped_tools = {}
            for tool in controller.tools.get_all_tools():
                group_name = getattr(tool, "tools_group", None) or ""
                grouped_tools.setdefault(group_name, []).append({
                    "name": tool.name,
                    "title": getattr(tool, "title", None) or tool.name,
                    "description": getattr(tool, "description", ""),
                    "icon": getattr(tool, "icon_name", None),
                })
            tools = [
                {
                    "name": group_name,
                    "title": group_name or "Other",
                    "tools": group_tools,
                }
                for group_name, group_tools in grouped_tools.items()
            ]

            skill_manager = getattr(controller, "skill_manager", None)
            skills = [] if skill_manager is None else [
                {
                    "name": skill.name,
                    "description": skill.description,
                }
                for skill in skill_manager.skills.values()
            ]
            return {
                "prompts": prompts,
                "tools": tools,
                "skills": skills,
                "icons": list(MODE_ICON_CHOICES),
                "states": list(VALID_STATES),
            }

        @app.get("/api/modes/{name}")
        def api_get_mode(name: str):
            mm = _get_mode_manager()
            mode = mm.get_mode(name)
            if mode is None:
                raise HTTPException(
                    status_code=404, detail=f"Mode '{name}' not found"
                )
            return _serialize_mode(name, mode, mm)

        @app.post("/api/modes")
        def api_create_mode(req: ModeMutationRequest):
            from ...modes import DEFAULT_MODE_ICON, ModeError

            _validate_mode_mutation(req, require_name=True)
            mm = _get_mode_manager()
            try:
                name = mm.create_mode(
                    req.name,
                    description=req.description or "",
                    icon=req.icon or DEFAULT_MODE_ICON,
                    tools=req.tools or {},
                    skills=req.skills or {},
                    prompts=req.prompts or {},
                )
            except ModeError as error:
                _raise_mode_mutation_error(error)
            _refresh_mode_buttons()
            return _serialize_mode(name, mm.get_mode(name), mm)

        @app.put("/api/modes/{name}")
        def api_update_mode(name: str, req: ModeMutationRequest):
            from ...modes import ModeError

            _validate_mode_mutation(req)
            mm = _get_mode_manager()
            was_active = mm.get_active_mode_name() == name
            try:
                updated_name = mm.update_mode(
                    name,
                    new_name=req.name,
                    description=req.description,
                    icon=req.icon,
                    tools=req.tools,
                    skills=req.skills,
                    prompts=req.prompts,
                )
            except ModeError as error:
                _raise_mode_mutation_error(error)
            if was_active:
                _refresh_active_mode()
            else:
                _refresh_mode_buttons()
            return _serialize_mode(
                updated_name, mm.get_mode(updated_name), mm
            )

        @app.delete("/api/modes/{name}")
        def api_delete_mode(name: str):
            from ...modes import BUILT_IN_MODE_NAMES

            mm = _get_mode_manager()
            if mm.get_mode(name) is None:
                raise HTTPException(
                    status_code=404, detail=f"Mode '{name}' not found"
                )
            if name in BUILT_IN_MODE_NAMES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Built-in mode '{name}' cannot be deleted",
                )
            was_active = mm.get_active_mode_name() == name
            mm.delete_mode(name)
            if was_active:
                _refresh_active_mode()
            else:
                _refresh_mode_buttons()
            return {"status": "ok", "mode": mm.get_active_mode_name()}

        @app.post("/api/modes/set-active")
        def api_set_active_mode(req: SetActiveModeRequest):
            mm = _get_mode_manager()
            try:
                mm.set_active_mode(req.mode)
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Mode '{req.mode}' not found")
            # Propagate skill overrides and rebuild prompts/tools so the next
            # generation reflects the newly active mode. Mirrors the desktop
            # ModeButton._on_mode_activated and ChatInterface._cmd_mode flows.
            _refresh_active_mode()
            return {"status": "ok"}

        # ============================================================ #
        #                        EXTENSIONS                             #
        # ============================================================ #
        def _flatten_extra_settings(extra_settings, section=None):
            """Flatten nested handler settings while retaining section labels."""
            flattened = []
            for setting in extra_settings or []:
                if not isinstance(setting, dict):
                    continue
                if setting.get("type") == "nested":
                    nested_section = setting.get("title") or section
                    flattened.extend(
                        _flatten_extra_settings(
                            setting.get("extra_settings", []), nested_section
                        )
                    )
                    continue
                entry = dict(setting)
                if section:
                    entry["section"] = section
                flattened.append(entry)
            return flattened

        def _find_extra_setting(extra_settings, key):
            for setting in extra_settings or []:
                if not isinstance(setting, dict):
                    continue
                if setting.get("key") == key:
                    return setting
                if setting.get("type") == "nested":
                    found = _find_extra_setting(
                        setting.get("extra_settings", []), key
                    )
                    if found is not None:
                        return found
            return None

        def _serialize_extra_settings(extra_settings, get_value):
            """Map a handler's extra_settings list to the JSON shape the WebUI renders.

            ``extra_settings`` is a list of dicts (Newelle's canonical setting format).
            ``get_value`` is a callable taking the setting key and returning the
            current value. Mirrors api_get_tts_settings.
            """
            result = []
            for s in _flatten_extra_settings(extra_settings):
                entry = {
                    "key": s.get("key", ""),
                    "title": s.get("title", ""),
                    "description": s.get("description", ""),
                    "type": s.get("type", "entry"),
                }
                for field in (
                    "default", "values", "password", "min", "max", "step",
                    "round-digits", "website", "folder", "section", "label",
                    "icon", "is_installed",
                ):
                    if field in s:
                        entry[field] = s[field]
                value = get_value(entry["key"])
                entry["value"] = entry.get("default") if value is None else value
                if entry["type"] in ("button", "download"):
                    entry["action"] = True
                    entry["has_refresh"] = callable(s.get("refresh"))
                if entry["type"] == "download" and callable(
                    s.get("download_percentage")
                ):
                    try:
                        entry["progress"] = s["download_percentage"](None)
                    except Exception:
                        entry["progress"] = 0
                result.append(entry)
            return result

        def _provider_category(category):
            from ...constants import (
                AVAILABLE_EMBEDDINGS,
                AVAILABLE_IMAGE_GENERATORS,
                AVAILABLE_LLMS,
                AVAILABLE_MEMORIES,
                AVAILABLE_RAGS,
                AVAILABLE_WEBSEARCH,
            )

            categories = {
                "embedding": {
                    "providers": AVAILABLE_EMBEDDINGS,
                    "provider_key": "embedding-model",
                    "settings_key": "embedding-settings",
                },
                "memory": {
                    "providers": AVAILABLE_MEMORIES,
                    "provider_key": "memory-model",
                    "settings_key": "memory-settings",
                    "enabled_key": "memory-on",
                },
                "rag": {
                    "providers": AVAILABLE_RAGS,
                    "provider_key": "rag-model",
                    "settings_key": "rag-settings",
                    "enabled_key": "rag-on",
                },
                "image-generation": {
                    "providers": AVAILABLE_IMAGE_GENERATORS,
                    "provider_key": "image-generator",
                    "settings_key": "image-generator-settings",
                },
                "websearch": {
                    "providers": AVAILABLE_WEBSEARCH,
                    "provider_key": "websearch-model",
                    "settings_key": "websearch-settings",
                    "enabled_key": "websearch-on",
                },
                "secondary-llm": {
                    "providers": AVAILABLE_LLMS,
                    "provider_key": "secondary-language-model",
                    "settings_key": "llm-secondary-settings",
                    "enabled_key": "secondary-llm-on",
                    "secondary": True,
                },
            }
            config = categories.get(category)
            if config is None:
                raise HTTPException(
                    status_code=404, detail="Unknown provider settings category"
                )
            return config

        def _provider_handler(config, provider):
            providers = config["providers"]
            if provider not in providers:
                raise HTTPException(status_code=404, detail="Provider not found")
            handler = controller.handlers.get_object(
                providers, provider, config.get("secondary", False)
            )
            if config["provider_key"] == "rag-model":
                handler.set_handlers(
                    controller.handlers.llm, controller.handlers.embedding
                )
            return handler

        def _provider_payload(category, provider=None):
            config = _provider_category(category)
            selected = provider or controller.settings.get_string(
                config["provider_key"]
            )
            if selected not in config["providers"]:
                selected = next(iter(config["providers"]), "")
            handler = _provider_handler(config, selected)
            extra = list(handler.get_extra_settings() or [])
            if category == "rag" and hasattr(handler, "get_index_row"):
                extra.append(handler.get_index_row())

            providers = [
                {
                    "key": key,
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                }
                for key, info in config["providers"].items()
            ]
            payload = {
                "category": category,
                "provider": selected,
                "providers": providers,
                "enabled": (
                    controller.settings.get_boolean(config["enabled_key"])
                    if config.get("enabled_key")
                    else None
                ),
                "settings": _serialize_extra_settings(
                    extra, lambda key: handler.get_setting(key)
                ),
                "models": [],
                "details": {},
            }
            if category == "secondary-llm" and hasattr(
                handler, "get_models_list"
            ):
                try:
                    payload["models"] = [
                        {
                            "id": model[0],
                            "name": model[1] if len(model) > 1 else model[0],
                        }
                        for model in handler.get_models_list()
                    ]
                except Exception:
                    payload["models"] = []
                payload["details"] = {
                    "use_for_vision": controller.settings.get_boolean(
                        "secondary-llm-vision"
                    )
                }
            elif category == "rag":
                payload["details"] = {
                    "use_for_unsupported": controller.settings.get_boolean(
                        "rag-on-documents"
                    ),
                    "documents_context_limit": controller.settings.get_int(
                        "documents-context-limit"
                    ),
                    "custom_document_folders": controller.settings.get_strv(
                        "custom-document-folders"
                    ),
                    "documents_path": getattr(handler, "documents_path", ""),
                }
            return payload

        @app.get("/api/provider-settings/{category}")
        def api_get_provider_settings(category: str, provider: Optional[str] = None):
            return _provider_payload(category, provider)

        @app.put("/api/provider-settings/{category}")
        def api_set_provider_settings(category: str, req: ProviderSettingsRequest):
            config = _provider_category(category)
            selected = req.provider or controller.settings.get_string(
                config["provider_key"]
            )
            handler = _provider_handler(config, selected)
            if req.provider is not None:
                controller.settings.set_string(config["provider_key"], selected)
            if req.enabled is not None:
                enabled_key = config.get("enabled_key")
                if enabled_key is None:
                    raise HTTPException(
                        status_code=400, detail="Category cannot be enabled or disabled"
                    )
                controller.settings.set_boolean(enabled_key, req.enabled)

            extra = list(handler.get_extra_settings() or [])
            editable = {
                setting["key"]
                for setting in _flatten_extra_settings(extra)
                if setting.get("type") not in ("button", "download")
            }
            unknown = set(req.settings) - editable
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider setting: {sorted(unknown)[0]}",
                )
            for key, value in req.settings.items():
                handler.set_setting(key, value)

            if category == "secondary-llm" and "use_for_vision" in req.details:
                value = req.details["use_for_vision"]
                if not isinstance(value, bool):
                    raise HTTPException(
                        status_code=400, detail="use_for_vision must be boolean"
                    )
                controller.settings.set_boolean("secondary-llm-vision", value)
            elif category == "rag":
                if "use_for_unsupported" in req.details:
                    value = req.details["use_for_unsupported"]
                    if not isinstance(value, bool):
                        raise HTTPException(
                            status_code=400,
                            detail="use_for_unsupported must be boolean",
                        )
                    controller.settings.set_boolean("rag-on-documents", value)
                if "documents_context_limit" in req.details:
                    value = req.details["documents_context_limit"]
                    if not isinstance(value, int) or not 0 <= value <= 50000:
                        raise HTTPException(
                            status_code=400,
                            detail="documents_context_limit must be between 0 and 50000",
                        )
                    controller.settings.set_int("documents-context-limit", value)
                if "custom_document_folders" in req.details:
                    folders = req.details["custom_document_folders"]
                    if not isinstance(folders, list) or not all(
                        isinstance(folder, str) and folder.strip()
                        for folder in folders
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="custom_document_folders must contain non-empty paths",
                        )
                    controller.settings.set_strv(
                        "custom-document-folders",
                        list(dict.fromkeys(folder.strip() for folder in folders)),
                    )

            controller.update_settings()
            return _provider_payload(category, selected)

        @app.post("/api/provider-settings/{category}/action")
        def api_run_provider_setting_action(
            category: str, req: ProviderActionRequest
        ):
            config = _provider_category(category)
            selected = req.provider or controller.settings.get_string(
                config["provider_key"]
            )
            handler = _provider_handler(config, selected)
            extra = list(handler.get_extra_settings() or [])
            if category == "rag" and hasattr(handler, "get_index_row"):
                extra.append(handler.get_index_row())
            setting = _find_extra_setting(extra, req.key)
            if setting is None:
                raise HTTPException(status_code=404, detail="Setting action not found")
            callback = (
                setting.get("refresh")
                if req.action == "refresh"
                else setting.get("callback")
            )
            if not callable(callback):
                raise HTTPException(status_code=400, detail="Setting has no such action")
            threading.Thread(target=callback, args=(None,), daemon=True).start()
            return {"status": "ok"}

        def _get_extension_loader():
            loader = getattr(controller, "extensionloader", None)
            if loader is None:
                raise HTTPException(status_code=503, detail="Extensions not available")
            return loader

        @app.get("/api/extensions")
        def api_list_extensions():
            loader = _get_extension_loader()
            result = []
            for ext in loader.get_extensions():
                try:
                    has_settings = bool(ext.get_extra_settings())
                except Exception:
                    has_settings = False
                try:
                    installed = bool(ext.is_installed())
                except Exception:
                    installed = True
                result.append({
                    "id": ext.id,
                    "name": getattr(ext, "name", ext.id),
                    "description": getattr(ext, "description", ""),
                    "enabled": ext not in loader.disabled_extensions,
                    "installed": installed,
                    "has_settings": has_settings,
                })
            return result

        @app.get("/api/extensions/{extension_id}/settings")
        def api_get_extension_settings(extension_id: str):
            loader = _get_extension_loader()
            ext = loader.get_extension_by_id(extension_id)
            if ext is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            try:
                extra = ext.get_extra_settings()
            except Exception:
                extra = []
            settings = _serialize_extra_settings(extra, lambda k: ext.get_setting(k))
            return {"extension_id": extension_id, "settings": settings}

        @app.post("/api/extensions/{extension_id}/settings")
        def api_set_extension_settings(extension_id: str, req: ExtensionSettingsRequest):
            loader = _get_extension_loader()
            ext = loader.get_extension_by_id(extension_id)
            if ext is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            for key, value in req.settings.items():
                ext.set_setting(key, value)
            controller.update_settings()
            return {"status": "ok"}

        @app.post("/api/extensions/{extension_id}/set-enabled")
        def api_set_extension_enabled(extension_id: str, req: SetExtensionEnabledRequest):
            loader = _get_extension_loader()
            if loader.get_extension_by_id(extension_id) is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            if req.enabled:
                loader.enable(extension_id)
            else:
                loader.disable(extension_id)
            controller.reload_extensions({extension_id})
            return {"status": "ok"}

        @app.post("/api/extensions/add")
        async def api_add_extension(file: UploadFile = File(...)):
            loader = _get_extension_loader()
            filename = file.filename or "extension.py"
            if not filename.endswith(".py"):
                raise HTTPException(status_code=400, detail="Extension file must be a .py file")
            tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
            try:
                content = await file.read()
                tmp.write(content)
                tmp.flush()
                tmp.close()
                loader.add_extension(tmp.name, filename)
                # Reload so the new extension's handlers/prompts/tools are live.
                controller.reload_extensions()
                new_loader = controller.extensionloader
                new_id = None
                base = os.path.basename(filename)
                for ext_id, fname in new_loader.filemap.items():
                    if fname == base:
                        new_id = ext_id
                        break
                if new_id is not None:
                    added = new_loader.get_extension_by_id(new_id)
                    if added is not None and hasattr(added, "install"):
                        threading.Thread(target=added.install, daemon=True).start()
                return {"status": "ok", "id": new_id}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass

        @app.delete("/api/extensions/{extension_id}")
        def api_delete_extension(extension_id: str):
            loader = _get_extension_loader()
            if loader.get_extension_by_id(extension_id) is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            loader.remove_extension(extension_id)
            controller.reload_extensions({extension_id})
            return {"status": "ok"}

        # ============================================================ #
        #                           SKILLS                              #
        # ============================================================ #
        def _get_skill_manager():
            sm = getattr(controller, "skill_manager", None)
            if sm is None:
                raise HTTPException(status_code=503, detail="Skills not available")
            return sm

        @app.get("/api/skills")
        def api_list_skills():
            sm = _get_skill_manager()
            try:
                sm.discover()
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            result = []
            for skill in sm.skills.values():
                # The skills panel manages the Normal (profile) configuration;
                # the active Mode may still override the value at runtime.
                override = sm.mode_skill_overrides.get(skill.name)
                if override not in ("enable", "remove"):
                    override = None
                result.append({
                    "name": skill.name,
                    "description": skill.description,
                    "enabled": sm.is_skill_enabled(skill.name, apply_overrides=False),
                    "mode_override": override,
                    "location": skill.location,
                    "removable": os.path.abspath(skill.base_dir).startswith(
                        os.path.abspath(sm.skills_dirs[0])
                    ) if sm.skills_dirs else False,
                })
            return result

        @app.post("/api/skills/set-enabled")
        def api_set_skill_enabled(req: SetSkillEnabledRequest):
            sm = _get_skill_manager()
            sm.set_skill_enabled(req.name, req.enabled)
            return {"status": "ok"}

        @app.post("/api/skills/add")
        async def api_add_skill(file: UploadFile = File(...)):
            import zipfile
            import shutil
            sm = _get_skill_manager()
            if not sm.skills_dirs:
                raise HTTPException(status_code=503, detail="No skills directory configured")

            filename = file.filename or "skill.zip"
            if not filename.lower().endswith(".zip"):
                raise HTTPException(status_code=400, detail="Skill must be a .zip file")

            tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            extract_dir = tempfile.mkdtemp(prefix="newelle_skill_")
            try:
                content = await file.read()
                tmp_zip.write(content)
                tmp_zip.flush()
                tmp_zip.close()

                try:
                    with zipfile.ZipFile(tmp_zip.name) as zf:
                        for member in zf.namelist():
                            # Reject absolute paths and parent traversal to prevent
                            # path-traversal attacks during extraction.
                            norm = os.path.normpath(member)
                            if norm.startswith("..") or os.path.isabs(norm):
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Unsafe path in archive: {member}",
                                )
                        zf.extractall(extract_dir)
                except zipfile.BadZipFile:
                    raise HTTPException(status_code=400, detail="Invalid or corrupt zip file")

                # Locate the directory containing SKILL.md (root, or first subdirectory).
                source_dir = None
                if os.path.isfile(os.path.join(extract_dir, "SKILL.md")):
                    source_dir = extract_dir
                else:
                    for entry in os.listdir(extract_dir):
                        candidate = os.path.join(extract_dir, entry)
                        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "SKILL.md")):
                            source_dir = candidate
                            break
                if source_dir is None:
                    raise HTTPException(
                        status_code=400,
                        detail="No SKILL.md found in the zip (root or single subdirectory)",
                    )

                skill = sm.add_skill_from_path(source_dir)
                if skill is None:
                    raise HTTPException(status_code=400, detail="Failed to parse SKILL.md")
                return {
                    "status": "ok",
                    "skill": {
                        "name": skill.name,
                        "description": skill.description,
                        "enabled": sm.is_skill_enabled(skill.name),
                        "location": skill.location,
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                try:
                    os.unlink(tmp_zip.name)
                except Exception:
                    pass
                try:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                except Exception:
                    pass

        @app.delete("/api/skills/{skill_name}")
        def api_delete_skill(skill_name: str):
            sm = _get_skill_manager()
            ok = sm.remove_skill(skill_name)
            if not ok:
                raise HTTPException(status_code=404, detail="Skill not found")
            return {"status": "ok"}

        # ============================================================ #
        #                       STT SETTINGS                            #
        # ============================================================ #
        @app.get("/api/stt/providers")
        def api_list_stt_providers():
            from ...constants import AVAILABLE_STT
            result = []
            for key, info in AVAILABLE_STT.items():
                result.append({
                    "key": key,
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                    "primary": info.get("primary", True),
                    "secondary": info.get("secondary", False),
                    "wakeword": info.get("wakeword", False),
                    "website": info.get("website"),
                })
            return result

        @app.get("/api/stt/status")
        def api_stt_status():
            ns = controller.newelle_settings
            secondary_on = bool(getattr(ns, "use_secondary_stt", False))
            secondary_provider = getattr(ns, "secondary_stt_engine", "") if secondary_on else ""
            wakeword_engine = getattr(ns, "wakeword_engine", "")
            return {
                "provider": getattr(ns, "stt_engine", ""),
                "secondary_on": secondary_on,
                "secondary_provider": secondary_provider,
                "wakeword_engine": wakeword_engine,
            }

        @app.post("/api/stt/set-provider")
        def api_set_stt_provider(req: SetSttProviderRequest):
            from ...constants import AVAILABLE_STT
            if req.provider not in AVAILABLE_STT:
                raise HTTPException(status_code=400, detail="Unknown provider")
            if not AVAILABLE_STT[req.provider].get("primary", True):
                raise HTTPException(status_code=400, detail="Provider cannot be used as primary STT")
            controller.settings.set_string("stt-engine", req.provider)
            controller.update_settings()
            return {"status": "ok"}

        @app.get("/api/stt/settings")
        def api_get_stt_settings(provider: Optional[str] = None):
            target = provider or getattr(controller.newelle_settings, "stt_engine", "")
            from ...constants import AVAILABLE_STT
            if target not in AVAILABLE_STT:
                raise HTTPException(status_code=404, detail="Provider not found")
            handler_class = AVAILABLE_STT[target]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            extra = handler.get_extra_settings_list() if hasattr(handler, "get_extra_settings_list") else handler.get_extra_settings()
            settings = _serialize_extra_settings(extra, lambda k: handler.get_setting(k))
            return {"provider": target, "settings": settings}

        @app.post("/api/stt/settings")
        def api_set_stt_settings(req: SetSttSettingsRequest):
            provider = req.provider or getattr(controller.newelle_settings, "stt_engine", "")
            from ...constants import AVAILABLE_STT
            if provider not in AVAILABLE_STT:
                raise HTTPException(status_code=400, detail="Unknown provider")
            handler_class = AVAILABLE_STT[provider]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            for key, value in req.settings.items():
                handler.set_setting(key, value)
            controller.update_settings()
            return {"status": "ok"}

        # ============================================================ #
        #                         FOLDERS                               #
        # ============================================================ #
        @app.post("/api/folders")
        def api_create_folder(req: CreateFolderRequest):
            folder_id = controller.create_folder(req.name, req.color, req.icon)
            return {"folder_id": folder_id}

        @app.put("/api/folders/{folder_id}/rename")
        def api_rename_folder(folder_id: int, req: RenameFolderRequest):
            controller.rename_folder(folder_id, req.name)
            return {"status": "ok"}

        @app.put("/api/folders/{folder_id}/color")
        def api_update_folder_color(folder_id: int, req: UpdateFolderColorRequest):
            controller.update_folder_color(folder_id, req.color)
            return {"status": "ok"}

        @app.put("/api/folders/{folder_id}/icon")
        def api_update_folder_icon(folder_id: int, req: UpdateFolderIconRequest):
            controller.update_folder_icon(folder_id, req.icon)
            return {"status": "ok"}

        @app.delete("/api/folders/{folder_id}")
        def api_delete_folder(folder_id: int):
            controller.delete_folder(folder_id)
            return {"status": "ok"}

        @app.post("/api/folders/{folder_id}/toggle-expanded")
        def api_toggle_folder_expanded(folder_id: int):
            controller.toggle_folder_expanded(folder_id)
            return {"status": "ok"}

        @app.post("/api/folders/move-chat")
        def api_move_chat_to_folder(req: MoveChatToFolderRequest):
            controller.move_chat_to_folder(req.chat_id, req.folder_id)
            return {"status": "ok"}

        @app.post("/api/folders/remove-chat")
        def api_remove_chat_from_folder(req: RemoveChatFromFolderRequest):
            controller.remove_chat_from_folder(req.chat_id)
            return {"status": "ok"}

        @app.get("/api/folders/chat/{chat_id}")
        def api_get_folder_for_chat(chat_id: int):
            folder_id = controller.get_folder_for_chat(chat_id)
            return {"folder_id": folder_id}

        @app.get("/api/folders")
        def api_list_folders():
            result = []
            for fid, folder in controller.workspace_folders().items():
                result.append({
                    "id": fid,
                    "name": folder["name"],
                    "color": folder["color"],
                    "icon": folder["icon"],
                    "chat_ids": folder["chat_ids"],
                    "expanded": folder["expanded"],
                })
            return result

        # ============================================================ #
        #                     SCHEDULED TASKS                           #
        # ============================================================ #
        @app.get("/api/scheduled-tasks")
        def api_get_scheduled_tasks():
            return controller.get_scheduled_tasks()

        @app.post("/api/scheduled-tasks")
        def api_create_scheduled_task(req: CreateScheduledTaskRequest):
            task = controller.create_scheduled_task(
                task=req.task, run_at=req.run_at, cron=req.cron, folder_id=req.folder_id
            )
            return task

        @app.post("/api/scheduled-tasks/set-enabled")
        def api_set_scheduled_task_enabled(req: SetScheduledTaskEnabledRequest):
            changed = controller.set_scheduled_task_enabled(req.task_id, req.enabled)
            if not changed:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"status": "ok"}

        @app.delete("/api/scheduled-tasks/{task_id}")
        def api_delete_scheduled_task(task_id: str):
            changed = controller.delete_scheduled_task(task_id)
            if not changed:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"status": "ok"}

        @app.post("/api/scheduled-tasks/set-folder")
        def api_set_scheduled_task_folder(req: SetScheduledTaskFolderRequest):
            changed = controller.set_scheduled_task_folder(req.task_id, req.folder_id)
            if not changed:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"status": "ok"}

        @app.get("/api/scheduled-tasks/{task_id}/folder")
        def api_get_scheduled_task_folder(task_id: str):
            folder_id = controller.get_scheduled_task_folder_id(task_id)
            return {"folder_id": folder_id}

        @app.post("/api/scheduler/start")
        def api_start_scheduler():
            controller.start_scheduler()
            return {"status": "ok"}

        @app.post("/api/scheduler/stop")
        def api_stop_scheduler():
            controller.stop_scheduler()
            return {"status": "ok"}

        # ============================================================ #
        #                          LLM                                 #
        # ============================================================ #
        @app.get("/api/llm/providers")
        def api_list_llm_providers():
            from ...constants import AVAILABLE_LLMS
            result = []
            for key, info in AVAILABLE_LLMS.items():
                result.append({
                    "key": key,
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                    "secondary": info.get("secondary", False),
                    "duplicated": info.get("duplicated", False),
                    "source": info.get("source", ""),
                })
            return result

        @app.get("/api/llm/status")
        def api_llm_status():
            llm = controller.handlers.llm
            provider = controller.newelle_settings.language_model
            model = llm.get_selected_model() if hasattr(llm, "get_selected_model") else ""
            secondary_on = controller.newelle_settings.use_secondary_language_model
            secondary_provider = controller.newelle_settings.secondary_language_model
            secondary_model = ""
            if secondary_on and hasattr(controller.handlers, "secondary_llm") and controller.handlers.secondary_llm:
                secondary_model = controller.handlers.secondary_llm.get_selected_model() if hasattr(controller.handlers.secondary_llm, "get_selected_model") else ""
            return {
                "provider": provider,
                "model": model,
                "secondary_on": secondary_on,
                "secondary_provider": secondary_provider,
                "secondary_model": secondary_model,
            }

        @app.get("/api/llm/models")
        def api_list_llm_models(provider: Optional[str] = None):
            target = provider or controller.newelle_settings.language_model
            from ...constants import AVAILABLE_LLMS
            if target not in AVAILABLE_LLMS:
                raise HTTPException(status_code=404, detail="Provider not found")
            handler_class = AVAILABLE_LLMS[target]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            models = handler.get_models_list() if hasattr(handler, "get_models_list") else ()
            return {
                "provider": target,
                "models": [{"id": m[0], "name": m[1] if len(m) > 1 else m[0]} for m in models],
            }

        class SetProviderRequest(BaseModel):
            provider: str

        class SetModelRequest(BaseModel):
            model: str
            provider: Optional[str] = None

        class SetLlmSettingsRequest(BaseModel):
            provider: Optional[str] = None
            settings: dict = Field(default_factory=dict)

        @app.post("/api/llm/set-provider")
        def api_set_llm_provider(req: SetProviderRequest):
            from ...constants import AVAILABLE_LLMS
            if req.provider not in AVAILABLE_LLMS:
                raise HTTPException(status_code=400, detail="Unknown provider")
            controller.settings.set_string("language-model", req.provider)
            controller.update_settings()
            return {"status": "ok"}

        @app.post("/api/llm/set-model")
        def api_set_llm_model(req: SetModelRequest):
            target = req.provider or controller.newelle_settings.language_model
            from ...constants import AVAILABLE_LLMS
            if target not in AVAILABLE_LLMS:
                raise HTTPException(status_code=400, detail="Unknown provider")
            handler_class = AVAILABLE_LLMS[target]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            llm_settings = json.loads(controller.settings.get_string("llm-settings"))
            if target not in llm_settings:
                llm_settings[target] = {}
            llm_settings[target]["model"] = req.model
            controller.settings.set_string("llm-settings", json.dumps(llm_settings))
            controller.update_settings()
            return {"status": "ok"}

        @app.get("/api/llm/settings")
        def api_get_llm_settings(provider: Optional[str] = None):
            target = provider or controller.newelle_settings.language_model
            from ...constants import AVAILABLE_LLMS
            if target not in AVAILABLE_LLMS:
                raise HTTPException(status_code=404, detail="Provider not found")
            handler_class = AVAILABLE_LLMS[target]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            extra = handler.get_extra_settings() if hasattr(handler, "get_extra_settings") else []
            llm_settings = json.loads(controller.settings.get_string("llm-settings"))
            values = llm_settings.get(target, {})
            result = []
            for s in extra:
                if isinstance(s, dict):
                    entry = {
                        "key": s.get("key", ""),
                        "title": s.get("title", ""),
                        "description": s.get("description", ""),
                        "type": s.get("type", "entry"),
                    }
                    if "default" in s:
                        entry["default"] = s["default"]
                    if "values" in s:
                        entry["values"] = s["values"]
                    if "password" in s:
                        entry["password"] = s["password"]
                    if "min" in s:
                        entry["min"] = s["min"]
                    if "max" in s:
                        entry["max"] = s["max"]
                    if "step" in s:
                        entry["step"] = s["step"]
                    if "round-digits" in s:
                        entry["round-digits"] = s["round-digits"]
                else:
                    entry = {
                        "key": s.key,
                        "title": s.title,
                        "description": s.description,
                        "type": type(s).__name__,
                    }
                    if hasattr(s, "default"):
                        entry["default"] = s.default
                    if hasattr(s, "values"):
                        entry["values"] = s.values
                    if hasattr(s, "password"):
                        entry["password"] = s.password
                entry["value"] = values.get(entry["key"], entry.get("default"))
                result.append(entry)
            return {"provider": target, "settings": result}

        @app.post("/api/llm/settings")
        def api_set_llm_settings(req: SetLlmSettingsRequest):
            provider = req.provider or controller.newelle_settings.language_model
            settings_values = req.settings
            from ...constants import AVAILABLE_LLMS
            if provider not in AVAILABLE_LLMS:
                raise HTTPException(status_code=400, detail="Unknown provider")
            llm_settings = json.loads(controller.settings.get_string("llm-settings"))
            if provider not in llm_settings:
                llm_settings[provider] = {}
            llm_settings[provider].update(settings_values)
            controller.settings.set_string("llm-settings", json.dumps(llm_settings))
            controller.update_settings()
            return {"status": "ok"}

        def _serialize_duplication_setting(setting, values: dict) -> Optional[dict]:
            """Serialize a duplication setting (dict or ExtraSettings object)."""
            if isinstance(setting, dict):
                entry = {
                    "key": setting.get("key", ""),
                    "title": setting.get("title", ""),
                    "description": setting.get("description", ""),
                    "type": setting.get("type", "entry"),
                }
                if "default" in setting:
                    entry["default"] = setting["default"]
                if "values" in setting:
                    entry["values"] = setting["values"]
                if "password" in setting:
                    entry["password"] = setting["password"]
                if "min" in setting:
                    entry["min"] = setting["min"]
                if "max" in setting:
                    entry["max"] = setting["max"]
                if "step" in setting:
                    entry["step"] = setting["step"]
                if "round-digits" in setting:
                    entry["round-digits"] = setting["round-digits"]
            elif hasattr(setting, "key"):
                entry = {
                    "key": setting.key,
                    "title": setting.title,
                    "description": setting.description,
                    "type": type(setting).__name__,
                }
                if hasattr(setting, "default"):
                    entry["default"] = setting.default
                if hasattr(setting, "values"):
                    entry["values"] = setting.values
                if hasattr(setting, "password"):
                    entry["password"] = setting.password
            else:
                return None
            entry["value"] = values.get(entry["key"], entry.get("default"))
            return entry

        @app.get("/api/llm/duplicable")
        def api_list_duplicable_llms():
            result = []
            for key, descriptor, settings in controller.handlers.get_duplicable_llms():
                flat_settings = []
                pending = list(settings)
                while pending:
                    setting = pending.pop(0)
                    if isinstance(setting, dict) and setting.get("type") == "nested":
                        pending[0:0] = setting.get("extra_settings", [])
                        continue
                    entry = _serialize_duplication_setting(setting, {})
                    if entry is not None:
                        flat_settings.append(entry)
                result.append({
                    "key": key,
                    "title": descriptor.get("title", key),
                    "description": descriptor.get("description", ""),
                    "settings": flat_settings,
                })
            return result

        class DuplicateLlmRequest(BaseModel):
            source: str
            key: str
            title: str
            description: str = ""
            values: dict = Field(default_factory=dict)

        @app.post("/api/llm/duplicate")
        def api_duplicate_llm(req: DuplicateLlmRequest):
            try:
                controller.handlers.duplicate_llm(
                    req.source, req.key, req.title, req.description, req.values
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
            controller.update_settings()
            return {"status": "ok", "key": req.key}

        @app.delete("/api/llm/providers/{key}")
        def api_delete_llm_provider(key: str):
            from ...constants import AVAILABLE_LLMS
            descriptor = AVAILABLE_LLMS.get(key)
            if descriptor is None:
                raise HTTPException(status_code=404, detail="Provider not found")
            if not descriptor.get("duplicated", False):
                raise HTTPException(status_code=400, detail="Only duplicated LLM handlers can be deleted")
            controller.handlers.delete_duplicated_llm(key)
            controller.update_settings()
            return {"status": "ok"}

        # ============================================================ #
        #                    SETTINGS HELPERS                           #
        # ============================================================ #
        @app.get("/api/settings")
        def api_get_settings():
            from ...utility.profile_settings import get_settings_dict
            return get_settings_dict(controller.settings)

        @app.patch("/api/settings")
        def api_patch_settings(req: PatchSettingsRequest):
            from ...utility.profile_settings import restore_settings_from_dict
            restore_settings_from_dict(controller.settings, req.settings)
            if set(req.settings) & {
                "command-execution-permissions",
                "path-security-levels",
                "default-risk-level",
            }:
                from ...utility.command_permissions import CommandPermissionManager
                CommandPermissionManager.invalidate_cache()
            controller.update_settings()
            return {"status": "ok"}

        def _load_json_list_setting(key, fallback):
            try:
                value = json.loads(controller.settings.get_string(key))
            except (json.JSONDecodeError, TypeError):
                value = fallback
            return value if isinstance(value, list) else fallback

        def _permissions_payload():
            return {
                "auto_run": controller.settings.get_boolean("auto-run"),
                "max_run_times": controller.settings.get_int("max-run-times"),
                "default_action": controller.settings.get_string(
                    "default-risk-level"
                ) or "ask",
                "file_rules": _load_json_list_setting(
                    "file-permissions",
                    [
                        {"path": "*", "read": "allow", "write": "ask"},
                        {
                            "path": "{{main_path}}",
                            "read": "allow",
                            "write": "ask",
                        },
                    ],
                ),
                "command_rules": _load_json_list_setting(
                    "command-execution-permissions", []
                ),
                "path_rules": _load_json_list_setting(
                    "path-security-levels",
                    [
                        {"path": "{{main_path}}", "level": "trusted"},
                        {"path": "/tmp", "level": "sandboxed"},
                    ],
                ),
            }

        @app.get("/api/permissions")
        def api_get_permissions():
            return _permissions_payload()

        @app.put("/api/permissions")
        def api_set_permissions(req: PermissionSettingsRequest):
            actions = {"allow", "ask", "block"}
            path_levels = {"yolo", "trusted", "sandboxed", "restricted"}

            if req.auto_run is not None:
                if not isinstance(req.auto_run, bool):
                    raise HTTPException(
                        status_code=400, detail="auto_run must be boolean"
                    )
                controller.settings.set_boolean("auto-run", req.auto_run)
            if req.max_run_times is not None:
                if (
                    not isinstance(req.max_run_times, int)
                    or not 0 <= req.max_run_times <= 30
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="max_run_times must be between 0 and 30",
                    )
                controller.settings.set_int("max-run-times", req.max_run_times)
            if req.default_action is not None:
                if req.default_action not in actions:
                    raise HTTPException(
                        status_code=400, detail="Invalid default action"
                    )
                controller.settings.set_string(
                    "default-risk-level", req.default_action
                )

            if req.file_rules is not None:
                if not isinstance(req.file_rules, list):
                    raise HTTPException(
                        status_code=400, detail="file_rules must be a list"
                    )
                for rule in req.file_rules:
                    if (
                        not isinstance(rule, dict)
                        or not isinstance(rule.get("path"), str)
                        or not rule["path"].strip()
                        or rule.get("read") not in actions
                        or rule.get("write") not in actions
                    ):
                        raise HTTPException(
                            status_code=400, detail="Invalid file permission rule"
                        )
                controller.settings.set_string(
                    "file-permissions", json.dumps(req.file_rules)
                )

            if req.command_rules is not None:
                if not isinstance(req.command_rules, list):
                    raise HTTPException(
                        status_code=400, detail="command_rules must be a list"
                    )
                for rule in req.command_rules:
                    if (
                        not isinstance(rule, dict)
                        or not isinstance(rule.get("pattern"), str)
                        or rule.get("action") not in actions
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid command permission rule",
                        )
                    try:
                        re.compile(rule["pattern"])
                    except re.error as error:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid command pattern: {error}",
                        )
                controller.settings.set_string(
                    "command-execution-permissions",
                    json.dumps(req.command_rules),
                )

            if req.path_rules is not None:
                if not isinstance(req.path_rules, list):
                    raise HTTPException(
                        status_code=400, detail="path_rules must be a list"
                    )
                for rule in req.path_rules:
                    if (
                        not isinstance(rule, dict)
                        or not isinstance(rule.get("path"), str)
                        or not rule["path"].strip()
                        or rule.get("level") not in path_levels
                    ):
                        raise HTTPException(
                            status_code=400, detail="Invalid path security rule"
                        )
                controller.settings.set_string(
                    "path-security-levels", json.dumps(req.path_rules)
                )

            from ...utility.command_permissions import CommandPermissionManager
            CommandPermissionManager.invalidate_cache()
            controller.update_settings()
            return _permissions_payload()

        # ============================================================ #
        #                       SSE STREAMING                           #
        # ============================================================ #
        @app.get("/api/chats/{chat_id}/stream")
        def api_stream_chat_events(chat_id: int):
            """SSE endpoint for real-time generation with tool support."""
            if chat_id not in controller.workspace_chats():
                raise HTTPException(status_code=404, detail="Chat not found")

            q = queue.Queue()
            done_sentinel = object()

            def run():
                # Pop the last user message so run_llm_with_tools can re-add it
                chat = controller.get_chat_by_id(chat_id)
                if not chat or chat[-1].get("User") != "User":
                    q.put(("error", "No user message found in chat"))
                    q.put(done_sentinel)
                    return
                message = chat.pop()["Message"]
                controller.set_chat_by_id(chat_id, chat)

                accumulated = ""
                last_cumulative = ""

                def on_stream(text: str):
                    nonlocal accumulated, last_cumulative
                    # LLM handlers send cumulative text (full response so far).
                    # Compute the actual delta to send to the frontend.
                    if text.startswith(last_cumulative):
                        delta = text[len(last_cumulative):]
                    else:
                        # New iteration (e.g. after a tool call) – cumulative text reset
                        delta = text
                    last_cumulative = text
                    accumulated += delta
                    if delta:
                        q.put(("chunk", delta))

                def on_tool_result(tool_name: str, result):
                    if result.requires_interaction and result.interaction_options:
                        interaction_id = str(uuid.uuid4())[:8]
                        options_data = [{"title": opt.title, "index": i} for i, opt in enumerate(result.interaction_options)]
                        pending_interactions[interaction_id] = {
                            "options": result.interaction_options,
                            "event": threading.Event(),
                        }
                        q.put(("tool_interaction", {
                            "interaction_id": interaction_id,
                            "tool_name": tool_name,
                            "display_text": result.display_text,
                            "options": options_data,
                        }))
                        # Block until the user responds via POST /api/tools/interact
                        pending_interactions[interaction_id]["event"].wait()
                        output = result.get_output() if hasattr(result, 'get_output') else str(result)
                        del pending_interactions[interaction_id]
                        q.put(("tool", {
                            "tool": tool_name,
                            "output": output,
                            "display_text": getattr(result, "display_text", None),
                        }))
                    else:
                        output = result.get_output() if hasattr(result, 'get_output') else str(result)
                        q.put(("tool", {
                            "tool": tool_name,
                            "output": output,
                            "display_text": getattr(result, "display_text", None),
                        }))

                try:
                    controller.run_llm_with_tools(
                        message=message,
                        chat_id=chat_id,
                        on_message_callback=on_stream,
                        on_tool_result_callback=on_tool_result,
                        save_chat=True,
                        force_tools_on_main_thread=True,
                    )
                    q.put(("finished", {"message": accumulated}))
                except Exception as e:
                    q.put(("error", str(e)))
                finally:
                    q.put(done_sentinel)

            def run_in_workspace():
                try:
                    with controller.workspace_request(chat_id):
                        run()
                except Exception as error:
                    q.put(("error", str(error)))
                    q.put(done_sentinel)

            thread = threading.Thread(target=run_in_workspace, daemon=True)
            thread.start()

            def event_generator():
                while True:
                    item = q.get()
                    if item is done_sentinel:
                        yield f"data: {json.dumps({'event': 'done'})}\n\n"
                        break
                    status, data = item
                    if data is not None:
                        try:
                            payload = json.dumps({"event": status, "data": data})
                        except (TypeError, ValueError):
                            payload = json.dumps({"event": status, "data": str(data)})
                    else:
                        payload = json.dumps({"event": status})
                    yield f"data: {payload}\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # ============================================================ #
        #                          TTS                                  #
        # ============================================================ #
        @app.get("/api/tts/providers")
        def api_list_tts_providers():
            from ...constants import AVAILABLE_TTS
            result = []
            for key, info in AVAILABLE_TTS.items():
                result.append({
                    "key": key,
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                })
            return result

        @app.get("/api/tts/status")
        def api_tts_status():
            tts = controller.handlers.tts
            provider = controller.newelle_settings.tts_program
            voice = tts.get_current_voice() if hasattr(tts, "get_current_voice") else ""
            return {
                "provider": provider,
                "voice": voice,
            }

        @app.post("/api/tts/set-provider")
        def api_set_tts_provider(req: SetProviderRequest):
            from ...constants import AVAILABLE_TTS
            if req.provider not in AVAILABLE_TTS:
                raise HTTPException(status_code=400, detail="Unknown provider")
            controller.settings.set_string("tts", req.provider)
            controller.update_settings()
            return {"status": "ok"}

        @app.get("/api/tts/settings")
        def api_get_tts_settings(provider: Optional[str] = None):
            target = provider or controller.newelle_settings.tts_program
            from ...constants import AVAILABLE_TTS
            if target not in AVAILABLE_TTS:
                raise HTTPException(status_code=404, detail="Provider not found")
            handler_class = AVAILABLE_TTS[target]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            extra = handler.get_extra_settings_list()
            result = []
            for s in extra:
                entry = {
                    "key": s.get("key", ""),
                    "title": s.get("title", ""),
                    "description": s.get("description", ""),
                    "type": s.get("type", "entry"),
                }
                for field in ("default", "values", "password", "min", "max", "step", "round-digits"):
                    if field in s:
                        entry[field] = s[field]
                entry["value"] = handler.get_setting(entry["key"])
                result.append(entry)
            return {"provider": target, "settings": result}

        class SetTtsSettingsRequest(BaseModel):
            provider: Optional[str] = None
            settings: dict = Field(default_factory=dict)

        @app.post("/api/tts/settings")
        def api_set_tts_settings(req: SetTtsSettingsRequest):
            provider = req.provider or controller.newelle_settings.tts_program
            settings_values = req.settings
            from ...constants import AVAILABLE_TTS
            if provider not in AVAILABLE_TTS:
                raise HTTPException(status_code=400, detail="Unknown provider")
            handler_class = AVAILABLE_TTS[provider]["class"]
            handler = handler_class(controller.settings, controller.handlers.directory)
            for key, value in settings_values.items():
                handler.set_setting(key, value)
            controller.update_settings()
            return {"status": "ok"}

        @app.get("/api/tts/voices")
        def api_get_tts_voices():
            tts = controller.handlers.tts
            voices = tts.get_voices()
            return {"voices": [v if isinstance(v, str) else v for v in voices]}

        @app.post("/api/tts/play")
        def api_tts_play(text: str):
            tts = controller.handlers.tts
            tts.play(text)
            return {"status": "ok"}

        @app.post("/api/tts/save")
        def api_tts_save_audio(text: str, response_format: str = "wav"):
            tts = controller.handlers.tts
            suffix = f".{response_format}"
            temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            temp_file.close()
            try:
                tts.save_audio(text, temp_file.name)
                with open(temp_file.name, "rb") as f:
                    audio_data = f.read()
                content_type = "audio/mpeg" if response_format == "mp3" else "audio/wav"
                return Response(
                    content=audio_data,
                    media_type=content_type,
                    headers={"Content-Disposition": f"attachment; filename=speech.{response_format}"},
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass

        @app.post("/api/tts/stop")
        def api_tts_stop():
            controller.handlers.tts.stop()
            return {"status": "ok"}

        @app.post("/api/tts/stream")
        def api_tts_stream(text: str, voice: Optional[str] = None, response_format: str = "mp3"):
            tts = controller.handlers.tts
            if not tts.streaming_enabled():
                return api_tts_save_audio(text, response_format)

            import asyncio
            import subprocess
            from subprocess import Popen

            if voice:
                tts.set_voice(voice)

            content_type = "audio/mpeg" if response_format == "mp3" else "audio/wav"
            fmt_args = tts.get_stream_format_args()

            try:
                ffmpeg_process = Popen(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error"] + fmt_args
                    + ["-i", "pipe:0", "-f", response_format, "pipe:1"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail="ffmpeg not found")

            q = queue.Queue()
            done_sentinel = object()

            def reader():
                try:
                    while True:
                        data = ffmpeg_process.stdout.read(4096)
                        if not data:
                            break
                        q.put(data)
                except Exception:
                    pass
                finally:
                    q.put(done_sentinel)

            def writer():
                try:
                    for chunk in tts.get_audio_stream(text):
                        try:
                            ffmpeg_process.stdin.write(chunk)
                        except BrokenPipeError:
                            return
                    try:
                        ffmpeg_process.stdin.close()
                    except Exception:
                        pass
                except Exception:
                    pass

            threading.Thread(target=reader, daemon=True).start()
            threading.Thread(target=writer, daemon=True).start()

            loop = asyncio.get_event_loop()

            async def audio_generator():
                while True:
                    item = await loop.run_in_executor(None, q.get)
                    if item is done_sentinel:
                        break
                    yield item
                try:
                    ffmpeg_process.terminate()
                except Exception:
                    pass

            return StreamingResponse(audio_generator(), media_type=content_type)

        # ============================================================ #
        #                          STT                                  #
        # ============================================================ #
        @app.post("/api/stt/recognize")
        async def api_stt_recognize(file: UploadFile = File(...)):
            stt = controller.handlers.stt
            temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                content = await file.read()
                temp_file.write(content)
                temp_file.flush()
                temp_file.close()
                text = stt.recognize_file(temp_file.name)
                if text is None:
                    text = ""
                return {"text": text}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                try:
                    temp_file.close()
                    os.unlink(temp_file.name)
                except Exception:
                    pass

        # ============================================================ #
        #                          IMAGES                               #
        # ============================================================ #
        # Image codeblocks use the Newelle format:
        #     ```image
        #     <path-or-data-url-or-url>
        #     ```
        # Uploaded images are stored in a per-chat directory and served back
        # ONLY when the requested path appears inside an ```image``` (or
        # ```video```) block within that chat's history, so the API never
        # exposes arbitrary files on disk.
        _IMAGE_BLOCK_RE = re.compile(r"```(?:image|video|file)\s*\n(.*?)```", re.DOTALL)
        # Tool calls whose arguments carry a media path. Maps tool name to the
        # argument key that holds the path.
        _MEDIA_TOOL_ARGS = {
            "show_image": ("image_path_or_url", "path", "image_path"),
            "read_image": ("path", "image_path", "image_path_or_url"),
            "show_video": ("video_path", "path"),
            "generate_image": ("output_path", "path", "image_path"),
            "image_generator": ("output_path", "path", "image_path"),
        }
        _ALLOWED_IMAGE_EXTS = {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg",
            ".mp4", ".mkv", ".webm", ".avi", ".mov",
        }

        def _chat_images_dir(chat_id):
            return os.path.join(controller.config_dir, "chat_images", str(chat_id))

        def _is_media_path(line):
            """True if a string looks like a media filesystem path (not a data/URL)."""
            if not line or line.startswith(("data:", "http://", "https://")):
                return False
            _, ext = os.path.splitext(line.lower())
            return ext in _ALLOWED_IMAGE_EXTS

        def _image_payload_lines_in_chat(chat_id):
            """Return the set of filesystem paths referenced by media anywhere
            in the chat's message history. Used as the allow-list for the
            image-serving endpoint so only media actually shown in the chat
            can be fetched.

            Sources scanned:
              - ```image / ```video / ```file codeblocks
              - File-attachment messages (User="File")
              - Arguments of known media tools (show_image, read_image, ...)
            """
            paths = set()
            chat = None
            try:
                chat = controller.get_chat_by_id(chat_id)
            except Exception:
                chat = None
            if not chat:
                return paths
            for msg in chat:
                if not isinstance(msg, dict):
                    continue
                message = msg.get("Message")
                if not isinstance(message, str):
                    continue
                # 1) image/video/file codeblocks
                for block in _IMAGE_BLOCK_RE.finditer(message):
                    for line in block.group(1).splitlines():
                        line = line.strip()
                        if line and not line.startswith(("data:", "http://", "https://")):
                            paths.add(os.path.abspath(line))
                # 2) File-attachment messages: "User" is "File", Message is the path
                if msg.get("User") == "File":
                    fp = message.strip()
                    if fp and not fp.startswith(("data:", "http://", "https://")):
                        paths.add(os.path.abspath(fp))
                # 3) Tool-call JSON arguments for known media tools.
                #    Tool calls are embedded as ```json blocks containing
                #    {"name": "...", "arguments": {...}}.
                for m in re.finditer(r'"(?:name|tool)"\s*:\s*"([\w]+)"', message):
                    tool_name = m.group(1)
                    if tool_name not in _MEDIA_TOOL_ARGS:
                        continue
                    arg_keys = _MEDIA_TOOL_ARGS[tool_name]
                    for key in arg_keys:
                        for am in re.finditer(
                            rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', message
                        ):
                            val = am.group(1).strip()
                            if val and not val.startswith(("data:", "http://", "https://")):
                                paths.add(os.path.abspath(val))
            return paths

        @app.post("/api/chats/{chat_id}/images")
        async def api_upload_chat_image(chat_id: int, file: UploadFile = File(...)):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            filename = file.filename or "image"
            _, ext = os.path.splitext(filename)
            ext = ext.lower()
            if ext not in _ALLOWED_IMAGE_EXTS:
                # Fall back to a reasonable default if the upload had no/unknown ext.
                ext = ".png"
            images_dir = _chat_images_dir(chat_id)
            os.makedirs(images_dir, exist_ok=True)
            stored_name = f"{uuid.uuid4().hex[:12]}{ext}"
            dest = os.path.join(images_dir, stored_name)
            try:
                content = await file.read()
                with open(dest, "wb") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            return {"path": dest}

        @app.get("/api/chats/{chat_id}/image")
        def api_get_chat_image(chat_id: int, path: str = Query(...)):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            if not path:
                raise HTTPException(status_code=400, detail="Missing path")
            # data: URLs and remote URLs are never served here.
            if path.startswith("data:") or path.startswith("http://") or path.startswith("https://"):
                raise HTTPException(status_code=403, detail="Unsupported image reference")
            target = os.path.abspath(path)
            allowed = _image_payload_lines_in_chat(chat_id)
            if target not in allowed:
                raise HTTPException(status_code=403, detail="Image is not part of this chat")
            if not os.path.isfile(target):
                raise HTTPException(status_code=404, detail="Image file not found")
            try:
                with open(target, "rb") as f:
                    data = f.read()
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            _, ext = os.path.splitext(target.lower())
            content_type_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
                ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
                ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
            }
            content_type = content_type_map.get(ext, "application/octet-stream")
            return Response(content=data, media_type=content_type)

        # ============================================================ #
        #                 OPENAI-COMPATIBLE CHAT ENDPOINT               #
        # ============================================================ #
        class ChatMessage(BaseModel):
            role: str
            content: str

        class ChatCompletionRequest(BaseModel):
            model: Optional[str] = None
            messages: list[ChatMessage]
            stream: Optional[bool] = False

        @app.post("/v1/chat/completions")
        async def api_openai_chat_completions(req: ChatCompletionRequest):
            from ...utility import convert_messages_openai_to_newelle
            llm = controller.handlers.llm
            last_user_message, history, system_prompt = convert_messages_openai_to_newelle(req.messages)

            if not last_user_message:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No user message provided", "type": "invalid_request_error"}},
                )

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            model_name = req.model or (llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default")

            if req.stream:
                return _stream_response(controller, llm, completion_id, created, model_name, last_user_message, history, system_prompt)
            else:
                return _non_stream_response(llm, completion_id, created, model_name, last_user_message, history, system_prompt)

        @app.get("/v1/models")
        def api_list_models():
            llm = controller.handlers.llm
            models = llm.get_models_list() if hasattr(llm, "get_models_list") else ()
            model_list = []
            for m in models:
                model_list.append({
                    "id": m[0],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "newelle",
                })
            if not model_list:
                model_list.append({
                    "id": llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "newelle",
                })
            return {"object": "list", "data": model_list}

        return app

    # ------------------------------------------------------------------ #
    #                      Lifecycle methods                              #
    # ------------------------------------------------------------------ #
    def start(self):
        if self.controller is None:
            return
        if not self.is_installed():
            self._error = "Dependencies not installed"
            print("Cannot start GUI API server: dependencies not installed")
            return
        import uvicorn

        self._error = None
        try:
            app = self._create_app()
            host = self._get_host()
            port = self._get_port()
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            self._server = uvicorn.Server(config)
            thread = threading.Thread(target=self._server.run, daemon=True)
            thread.start()
            self._write_state_file()
            print(f"GUI API server started on {host}:{port}")
        except Exception as e:
            self._error = str(e)
            print(f"Failed to start GUI API server: {e}")

    def stop(self):
        self._clear_state_file()
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            print("GUI API server stopped")

    def _is_locally_running(self):
        return self._server is not None and not self._server.should_exit


# ================================================================== #
#                      Helper functions                                #
# ================================================================== #
def _get_window(controller):
    """Safely get the window object from the controller."""
    ui_controller = getattr(controller, "ui_controller", None)
    if ui_controller is not None and hasattr(ui_controller, 'window'):
        return ui_controller.window
    return None


def _non_stream_response(llm, completion_id, created, model_name, prompt, history, system_prompt):
    from fastapi.responses import JSONResponse

    try:
        result = llm.send_message(prompt, history, system_prompt)
    except Exception as e:
        result = f"[Error: {str(e)}]"

    return JSONResponse(content={
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _stream_response(controller, llm, completion_id, created, model_name, prompt, history, system_prompt):
    from fastapi.responses import StreamingResponse

    q = queue.Queue()
    done_sentinel = object()
    error_container = [None]

    def on_update(full_message: str):
        q.put(("chunk", full_message))

    def run_llm():
        try:
            if hasattr(llm, 'stream_enabled') and llm.stream_enabled():
                llm.send_message_stream(prompt, history, system_prompt, on_update, [0])
            elif hasattr(llm, 'generate_text_stream'):
                llm.generate_text_stream(prompt, history, system_prompt, on_update=on_update)
            else:
                result = llm.send_message(prompt, history, system_prompt)
                q.put(("done", result))
        except Exception as e:
            error_container[0] = str(e)
        finally:
            q.put(done_sentinel)

    thread = threading.Thread(target=run_llm, daemon=True)
    thread.start()

    prev_len = 0

    def event_generator():
        nonlocal prev_len

        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

        while True:
            item = q.get()
            if item is done_sentinel:
                break

            status, full_message = item
            if status == "done":
                delta = full_message[prev_len:] if isinstance(full_message, str) else full_message
            elif status == "chunk":
                delta = full_message[prev_len:]
                prev_len = len(full_message)
            else:
                continue

            if delta:
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': delta}, 'finish_reason': None}]})}\n\n"

        if error_container[0] is not None:
            err_text = f"\n[Error: {error_container[0]}]"
            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': err_text}, 'finish_reason': None}]})}\n\n"

        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
