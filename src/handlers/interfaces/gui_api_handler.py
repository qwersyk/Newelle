import io
import json
import os
import queue
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
        from typing import Optional
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
            max_tool_calls: int = 10
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
                }
                result.append(entry)
            return result

        @app.get("/api/chats/{chat_id}/history")
        def api_get_chat_history(chat_id: int):
            """Get full message timeline for one chat."""
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            return controller.get_chat_by_id(chat_id)

        @app.post("/api/chats")
        def api_create_chat(req: CreateChatRequest):
            chat_id = controller.create_visible_chat(
                name=req.name, profile=req.profile, folder_id=req.folder_id
            )
            return {"chat_id": chat_id}

        @app.put("/api/chats/{chat_id}/rename")
        def api_rename_chat(chat_id: int, req: RenameChatRequest):
            if chat_id not in controller.chats:
                raise HTTPException(status_code=404, detail="Chat not found")
            controller.chats[chat_id]["name"] = req.name
            controller.save_chats()
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
            if chat_id not in controller.chats:
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
            new_id = controller.create_visible_chat(name=source["name"] + " (copy)")
            controller.chats[new_id]["chat"] = [m.copy() for m in source.get("chat", [])]
            controller.save_chats()
            return {"chat_id": new_id}

        @app.post("/api/chats/{chat_id}/choose")
        def api_choose_chat(chat_id: int):
            controller.newelle_settings.chat_id = chat_id
            controller.settings.set_int("chat", chat_id)
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
                result.append({
                    "name": tool.name,
                    "description": getattr(tool, 'description', ''),
                    "enabled": is_enabled,
                    "default_on": tool.default_on,
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
            enabled_map = {}
            if hasattr(controller, 'newelle_settings'):
                enabled_map = getattr(controller.newelle_settings, 'interfaces_enabled', {}) or {}
            for key, info in AVAILABLE_INTERFACES.items():
                iface = controller.handlers.interfaces.get(key) if hasattr(controller.handlers, 'interfaces') else None
                result.append({
                    "key": key,
                    "name": info.get("title", key),
                    "title": info.get("title", key),
                    "description": info.get("description", ""),
                    "enabled": bool(enabled_map.get(key, True)),
                    "running": iface.is_running() if iface else False,
                    "error": getattr(iface, '_error', None) if iface else None,
                })
            return result

        @app.post("/api/interfaces/set-enabled")
        def api_set_interface_enabled(req: SetInterfaceEnabledRequest):
            if not hasattr(controller, 'newelle_settings'):
                raise HTTPException(status_code=503, detail="Settings not loaded")
            enabled_map = controller.newelle_settings.interfaces_enabled
            if req.enabled:
                enabled_map[req.interface_key] = True
            else:
                enabled_map[req.interface_key] = False
            controller.newelle_settings.interfaces_enabled = enabled_map
            controller.settings.set_string("interfaces-enabled", json.dumps(enabled_map))
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
            if hasattr(controller, 'newelle_settings'):
                return controller.newelle_settings.interfaces_enabled
            return {}

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
            for fid, folder in controller.folders.items():
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
            controller.update_settings()
            return {"status": "ok"}

        # ============================================================ #
        #                       SSE STREAMING                           #
        # ============================================================ #
        @app.get("/api/chats/{chat_id}/stream")
        def api_stream_chat_events(chat_id: int):
            """SSE endpoint for real-time generation with tool support."""
            if chat_id not in controller.chats:
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
                        q.put(("tool", {"tool": tool_name, "output": output}))
                    else:
                        output = result.get_output() if hasattr(result, 'get_output') else str(result)
                        q.put(("tool", {"tool": tool_name, "output": output}))

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

            thread = threading.Thread(target=run, daemon=True)
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
            print(f"GUI API server started on {host}:{port}")
        except Exception as e:
            self._error = str(e)
            print(f"Failed to start GUI API server: {e}")

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            print("GUI API server stopped")

    def is_running(self):
        return self._server is not None and not self._server.should_exit


# ================================================================== #
#                      Helper functions                                #
# ================================================================== #
def _get_window(controller):
    """Safely get the window object from the controller."""
    if controller.ui_controller is not None and hasattr(controller.ui_controller, 'window'):
        return controller.ui_controller.window
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
