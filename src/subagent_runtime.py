"""Durable, turn-based execution for configured subagents."""

from __future__ import annotations

import copy
import datetime
import json
import threading
import uuid
from typing import Any, Callable

from .constants import AVAILABLE_LLMS, PROMPTS
from .handlers.handler import IsolatedSettings, SettingsCache
from .tools import Tool, ToolRegistry, ToolResult
from .utility.replacehelper import PromptFormatter, replace_variables_dict


SESSION_KEY = "subagent_session"


class SubagentRuntimeError(ValueError):
    """An actionable error that can be returned to the calling model."""


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _name_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise SubagentRuntimeError("Tools and skills must be lists or comma-separated strings.")
    result = []
    for item in values:
        name = str(item).strip()
        if name and name not in result:
            result.append(name)
    return result


class SubagentSessionRuntime:
    """Owns subagent sessions while definitions stay in ``SubagentManager``.

    Session metadata lives alongside the hidden call chat so both transcript
    and launch configuration are written by the application's existing atomic
    chat persistence path.
    """

    def __init__(self, controller):
        self.controller = controller
        self._state_lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}
        self._pending_cleanup_owners: set[int] = set()
        self._deleted_owners: set[int] = set()

    # -- persistence -------------------------------------------------

    def recover_sessions(self) -> None:
        """Make sessions abandoned by a previous process resumable."""
        changed = False
        for chat in getattr(self.controller, "chats", {}).values():
            metadata = chat.get(SESSION_KEY)
            if not isinstance(metadata, dict):
                continue
            if metadata.get("status") == "running":
                metadata["status"] = "interrupted"
                metadata["updated_at"] = _utc_now()
                metadata["error"] = "The application stopped while this subagent was running."
                changed = True
        if changed:
            self.controller.save_chats()

    def _find_session(self, session_id: str) -> tuple[int, dict, dict]:
        wanted = str(session_id or "").strip()
        for chat_id, chat in getattr(self.controller, "chats", {}).items():
            metadata = chat.get(SESSION_KEY)
            if isinstance(metadata, dict) and metadata.get("id") == wanted:
                return chat_id, chat, metadata
        raise SubagentRuntimeError(f"Subagent session '{wanted}' was not found.")

    def _new_session(self, owner_chat_id: int, snapshot: dict) -> tuple[int, dict]:
        session_id = str(uuid.uuid4())
        now = _utc_now()
        metadata = {
            "id": session_id,
            "owner_chat_id": owner_chat_id,
            "subagent": snapshot.get("id"),
            "name": snapshot.get("name") or "Subagent",
            "snapshot": copy.deepcopy(snapshot),
            "status": "interrupted",
            "created_at": now,
            "updated_at": now,
        }
        chat_id = self.controller.create_call_chat(
            name=f"Subagent: {metadata['name']}",
            metadata={SESSION_KEY: metadata},
        )
        # ``create_call_chat`` copies caller-owned metadata. Mutate the stored
        # instance so subsequent status and snapshot updates are durable.
        return chat_id, self.controller.chats[chat_id][SESSION_KEY]

    def _save_metadata(self, metadata: dict, status: str, **updates) -> None:
        metadata["status"] = status
        metadata["updated_at"] = _utc_now()
        metadata.update(updates)
        self.controller.save_chats()

    def cleanup_owner_sessions(self, owner_chat_id: int) -> int:
        """Remove sessions owned by a deleted main chat.

        Active executions are stopped and removed once their tool loop has
        unwound, avoiding writes into a chat that disappeared mid-generation.
        """
        removed = 0
        with self._state_lock:
            self._pending_cleanup_owners.add(owner_chat_id)
            active_ids = {
                session_id
                for session_id, state in self._active.items()
                if state.get("owner_chat_id") == owner_chat_id
            }
            for session_id in active_ids:
                self.cancel(self._active[session_id]["stop_event"])

            chat_lock = getattr(
                self.controller, "chat_state_lock", self._state_lock
            )
            with chat_lock:
                for chat_id, chat in list(
                    getattr(self.controller, "chats", {}).items()
                ):
                    metadata = chat.get(SESSION_KEY)
                    if not isinstance(metadata, dict):
                        continue
                    if metadata.get("owner_chat_id") != owner_chat_id:
                        continue
                    if metadata.get("id") in active_ids:
                        continue
                    del self.controller.chats[chat_id]
                    removed += 1
            if not active_ids:
                self._pending_cleanup_owners.discard(owner_chat_id)
        if removed:
            self.controller.save_chats()
        return removed

    def delete_owner_sessions(self, owner_chat_id: int) -> int:
        """Permanently close an owner and remove or stop all of its sessions."""
        with self._state_lock:
            self._deleted_owners.add(owner_chat_id)
            return self.cleanup_owner_sessions(owner_chat_id)

    def cancel(self, stop_event: threading.Event) -> None:
        """Stop the model and release an interactive tool for one active turn."""
        stop_event.set()
        with self._state_lock:
            state = next(
                (
                    active
                    for active in self._active.values()
                    if active.get("stop_event") is stop_event
                ),
                None,
            )
            if state is None:
                return
            tool_result = state.get("tool_result")
            if tool_result is not None:
                try:
                    tool_result.cancel()
                except Exception:
                    pass
            model = state.get("model")
            if model is not None:
                try:
                    model.stop()
                except Exception:
                    pass

    # -- definition snapshots --------------------------------------

    def _manager_definition(self, identifier: str) -> dict | None:
        manager = getattr(self.controller, "subagent_manager", None)
        if manager is None:
            raise SubagentRuntimeError("Configured subagents are not available yet.")
        getter = getattr(manager, "get_subagent", None) or getattr(manager, "get", None)
        if getter is None:
            raise SubagentRuntimeError("The subagent registry does not support lookup.")
        definition = getter(identifier)
        return copy.deepcopy(definition) if isinstance(definition, dict) else None

    def _is_definition_enabled(self, identifier: str) -> bool:
        manager = getattr(self.controller, "subagent_manager", None)
        checker = getattr(manager, "is_enabled", None)
        return bool(checker(identifier)) if checker is not None else True

    def _provider_model(
        self,
        provider,
        model,
        use_secondary_model: bool = False,
    ) -> tuple[str, str | None]:
        provider = str(provider or "").strip()
        if use_secondary_model:
            provider = self.controller.newelle_settings.secondary_language_model
        elif not provider:
            provider = self.controller.newelle_settings.language_model
        if provider not in AVAILABLE_LLMS:
            raise SubagentRuntimeError(
                f"Subagent provider '{provider}' is not currently available."
            )

        model = str(model or "").strip() or None
        if model is None:
            settings_key = (
                "llm-secondary-settings"
                if use_secondary_model
                else "llm-settings"
            )
            try:
                settings = json.loads(
                    self.controller.settings.get_string(settings_key)
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                settings = {}
            selected = settings.get(provider, {}).get("model")
            if selected is not None and str(selected).strip():
                model = str(selected).strip()
        return provider, model

    def create_snapshot(
        self,
        *,
        subagent: str | None = None,
        system_prompt: str | None = None,
        tools=None,
        skills=None,
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[dict, list[str]]:
        """Resolve either a named definition or the legacy inline form."""
        warnings = []
        identifier = str(subagent or "").strip()
        if identifier:
            if system_prompt or _name_list(tools) or _name_list(skills) or provider or model:
                raise SubagentRuntimeError(
                    "A named subagent cannot be combined with inline prompt, tools, skills, provider, or model."
                )
            definition = self._manager_definition(identifier)
            if definition is None:
                raise SubagentRuntimeError(f"Subagent '{identifier}' was not found.")
            canonical_id = str(definition.get("id") or identifier)
            if not self._is_definition_enabled(canonical_id):
                raise SubagentRuntimeError(f"Subagent '{canonical_id}' is disabled in the current mode.")
            snapshot = {
                "id": canonical_id,
                "name": definition.get("name") or canonical_id,
                "description": definition.get("description") or "",
                "system_prompt": definition.get("system_prompt") or "",
                "include_agents_md": bool(
                    definition.get("include_agents_md", False)
                ),
                "tools": _name_list(definition.get("tools")),
                "skills": _name_list(definition.get("skills")),
                "provider": definition.get("provider"),
                "model": definition.get("model"),
                "use_secondary_model": bool(
                    definition.get("use_secondary_model", False)
                ),
                "default_on": bool(definition.get("default_on", True)),
                "source": definition.get("source") or "user",
                "read_only": bool(definition.get("read_only", False)),
                "extension_id": definition.get("extension_id"),
            }
            if isinstance(definition.get("tool_settings"), dict):
                snapshot["tool_settings"] = copy.deepcopy(definition["tool_settings"])
        else:
            if not str(system_prompt or "").strip():
                raise SubagentRuntimeError(
                    "Legacy inline launch requires a non-empty system_prompt."
                )
            requested_tools = _name_list(tools)
            if not requested_tools:
                raise SubagentRuntimeError("Legacy inline launch requires at least one tool.")
            snapshot = {
                "id": None,
                "name": "Inline subagent",
                "description": "",
                "system_prompt": str(system_prompt).strip(),
                "include_agents_md": False,
                "tools": requested_tools,
                "skills": _name_list(skills),
                "provider": provider,
                "model": model,
                "use_secondary_model": False,
                "default_on": True,
                "source": "inline",
                "read_only": False,
                "extension_id": None,
            }

        if "run_subagent" in snapshot["tools"]:
            snapshot["tools"].remove("run_subagent")
            warnings.append("run_subagent was removed to prevent recursive delegation.")

        resolved_provider, resolved_model = self._provider_model(
            snapshot.get("provider"),
            snapshot.get("model"),
            snapshot.get("use_secondary_model", False),
        )
        snapshot["provider"] = resolved_provider
        snapshot["model"] = resolved_model
        snapshot["version"] = 1
        snapshot["expanded_tools"] = []

        profile_tool_settings = getattr(
            self.controller.newelle_settings, "tools_settings_dict", {}
        )
        own_tool_settings = snapshot.get("tool_settings", {})
        snapshot["tool_settings"] = {
            name: copy.deepcopy(
                own_tool_settings.get(name, profile_tool_settings.get(name, {}))
            )
            for name in snapshot["tools"]
        }
        return snapshot, warnings

    def _validate_snapshot(self, snapshot: dict) -> None:
        provider = snapshot.get("provider")
        if provider not in AVAILABLE_LLMS:
            raise SubagentRuntimeError(
                f"Snapshotted provider '{provider}' is no longer available."
            )
        missing_tools = [
            name
            for name in _name_list(snapshot.get("tools"))
            if name == "run_subagent" or self.controller.tools.get_tool(name) is None
        ]
        if missing_tools:
            raise SubagentRuntimeError(
                "Snapshotted tools are unavailable: " + ", ".join(missing_tools)
            )
        skill_manager = getattr(self.controller, "skill_manager", None)
        available_skills = getattr(skill_manager, "skills", {}) if skill_manager else {}
        missing_skills = [
            name for name in _name_list(snapshot.get("skills")) if name not in available_skills
        ]
        if missing_skills:
            raise SubagentRuntimeError(
                "Snapshotted skills are unavailable: " + ", ".join(missing_skills)
            )

    # -- execution --------------------------------------------------

    def _isolated_model(self, snapshot: dict):
        provider = snapshot["provider"]
        descriptor = AVAILABLE_LLMS[provider]
        use_secondary_model = bool(snapshot.get("use_secondary_model", False))
        settings_key = (
            "llm-secondary-settings" if use_secondary_model else "llm-settings"
        )
        try:
            all_settings = json.loads(
                self.controller.settings.get_string(settings_key)
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            all_settings = {}
        all_settings = copy.deepcopy(all_settings)
        provider_settings = all_settings.setdefault(provider, {})
        if snapshot.get("model") is not None:
            provider_settings["model"] = snapshot["model"]
        overlay = IsolatedSettings(
            self.controller.settings,
            {settings_key: json.dumps(all_settings)},
        )
        model = descriptor["class"](overlay, self.controller.models_dir)
        model.set_secondary_settings(use_secondary_model)
        if snapshot.get("model") is None:
            selected = model.get_selected_model()
            if selected is not None and str(selected).strip():
                snapshot["model"] = str(selected).strip()
        return model, overlay

    def _build_registry(self, snapshot: dict, message_box: dict, stop_event) -> ToolRegistry:
        registry = ToolRegistry()
        selected_skills = set(snapshot.get("skills", []))
        has_tool_search = "tool_search" in snapshot["tools"]
        for name in snapshot["tools"]:
            tool = self.controller.tools.get_tool(name)
            if tool is None or name in ("run_subagent", "tool_search"):
                continue
            if name == "activate_skill":
                skill_manager = self.controller.skill_manager

                def activate_scoped_skill(name: str):
                    result = ToolResult()
                    if name not in selected_skills:
                        result.set_output(
                            f"Skill '{name}' is not assigned to this subagent."
                        )
                    else:
                        result.set_output(
                            skill_manager.render_skill(
                                name, require_enabled=False
                            )
                        )
                    return result

                registry.register_tool(Tool(
                    name="activate_skill",
                    description=(
                        "Load one of this subagent's assigned skills. Available "
                        "skills: " + (", ".join(sorted(selected_skills)) or "none")
                    ),
                    func=activate_scoped_skill,
                    title=tool.title,
                    icon_name=tool.icon_name,
                    default_on=True,
                ))
            else:
                registry.register_tool(tool)

        if has_tool_search:
            def search_scoped_tool(tool_name: str):
                result = ToolResult()
                if registry.get_tool(tool_name) is None:
                    result.set_output(
                        f"Tool '{tool_name}' is not assigned to this subagent."
                    )
                else:
                    result.set_output(registry.get_tool_schema(tool_name))
                return result

            registry.register_tool(Tool(
                name="tool_search",
                description=(
                    "Return the full schema for one tool assigned to this subagent."
                ),
                func=search_scoped_tool,
                title="Tool Search",
                default_on=True,
            ))

        def send_message_to_main(content: str):
            """Return a message to the parent and terminate this subagent turn."""
            message_box["message"] = str(content)
            stop_event.set()
            result = ToolResult(stop_generation=True)
            result.set_output(None)
            return result

        registry.register_tool(Tool(
            name="send_message_to_main",
            description=(
                "Send a message to the main agent and end this subagent turn. "
                "The main agent can reply by resuming the returned session id."
            ),
            func=send_message_to_main,
            title="Message Main Agent",
            default_on=True,
        ))
        return registry

    def _build_prompts(
        self,
        snapshot: dict,
        registry: ToolRegistry,
        expanded: set,
        task: str,
        chat_id: int,
    ) -> list[str]:
        local_tool_names = {tool.name for tool in registry.get_all_tools()}
        enabled = {name: True for name in local_tool_names}
        tools_prompt = registry.get_tools_prompt(
            enabled_tools_dict=enabled,
            tools_settings=snapshot.get("tool_settings", {}),
            expanded_tools=expanded,
        )
        skill_manager = getattr(self.controller, "skill_manager", None)
        selected_skills = [
            skill_manager.skills[name]
            for name in snapshot["skills"]
            if name in skill_manager.skills
        ]
        skills_catalog = "\n".join(
            f"- **{skill.name}**: {skill.description}"
            for skill in selected_skills
        )
        simple_variables = replace_variables_dict(
            tools=tools_prompt,
            skills=skills_catalog,
        )

        global_tool_names = {
            tool.name for tool in self.controller.tools.get_all_tools()
        }
        previous_history = "\n".join(
            f"{entry.get('User', '')}: {entry.get('Message', '')}"
            for entry in self.controller.chats[chat_id].get("chat", [])
        )

        def get_scoped_variable(name):
            if name in local_tool_names:
                return True
            if name in global_tool_names:
                return False
            if name == "skills_available":
                return bool(selected_skills)
            if name.lower() == "tools":
                return tools_prompt
            if name.lower() == "skills":
                return skills_catalog
            if name == "message":
                return task
            if name == "history":
                return previous_history
            return self.controller.get_variable(name)

        formatter = PromptFormatter(simple_variables, get_scoped_variable)
        prompts = [formatter.format(snapshot.get("system_prompt") or "")]
        if snapshot.get("include_agents_md"):
            # Same "agent.md" prompt the main agent can enable: the content of
            # AGENTS.md from the current directory, resolved through {AGENTSMD}.
            agents_md = formatter.format(PROMPTS.get("agent.md", "{AGENTSMD}"))
            if agents_md.strip():
                prompts.append(agents_md)
        for skill_name in snapshot["skills"]:
            prompts.append(skill_manager.render_skill(skill_name, require_enabled=False))

        if tools_prompt:
            prompts.append(PROMPTS.get("tools", "").replace("{TOOLS}", tools_prompt))
        prompts.append(
            "When you need to hand control or your result back, call "
            "send_message_to_main(content). Calling it ends this turn. If you do "
            "not call it, your final response is sent to the main agent instead."
        )
        return prompts

    @staticmethod
    def error_result(error: Exception | str, session_id=None, subagent=None, warnings=None) -> dict:
        return {
            "session_id": session_id,
            "subagent": subagent,
            "status": "failed",
            "message": str(error),
            "warnings": list(warnings or []),
        }

    def run_turn(
        self,
        *,
        task: str,
        owner_chat_id: int,
        subagent: str | None = None,
        session_id: str | None = None,
        system_prompt: str | None = None,
        tools=None,
        skills=None,
        provider: str | None = None,
        model: str | None = None,
        on_message: Callable[[str], None] | None = None,
        on_tool_result: Callable[[str, ToolResult], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict:
        task = str(task or "").strip()
        if not task:
            raise SubagentRuntimeError("A subagent task cannot be empty.")
        stop_event = stop_event or threading.Event()
        with self._state_lock:
            if (
                owner_chat_id in self._deleted_owners
                or owner_chat_id not in getattr(self.controller, "chats", {})
            ):
                raise SubagentRuntimeError("The owning main chat no longer exists.")

            requested_session = str(session_id or "").strip()
            warnings = []
            if requested_session:
                if subagent or system_prompt or _name_list(tools) or _name_list(skills) or provider or model:
                    raise SubagentRuntimeError(
                        "session_id resume cannot be combined with a subagent or inline configuration."
                    )
                chat_id, _chat, metadata = self._find_session(requested_session)
                if metadata.get("owner_chat_id") != owner_chat_id:
                    raise SubagentRuntimeError(
                        "This subagent session belongs to a different main chat."
                    )
                snapshot = copy.deepcopy(metadata.get("snapshot") or {})
                if not snapshot:
                    raise SubagentRuntimeError(
                        "This session has no resumable configuration snapshot."
                    )
            else:
                snapshot, warnings = self.create_snapshot(
                    subagent=subagent,
                    system_prompt=system_prompt,
                    tools=tools,
                    skills=skills,
                    provider=provider,
                    model=model,
                )
                chat_id, metadata = self._new_session(owner_chat_id, snapshot)
                requested_session = metadata["id"]

            try:
                self._validate_snapshot(snapshot)
            except Exception as error:
                self._save_metadata(metadata, "failed", error=str(error))
                return self.error_result(
                    error,
                    session_id=requested_session,
                    subagent=snapshot.get("id"),
                    warnings=warnings,
                )
            if requested_session in self._active:
                raise SubagentRuntimeError(
                    f"Subagent session '{requested_session}' is already running."
                )
            self._active[requested_session] = {
                "owner_chat_id": owner_chat_id,
                "stop_event": stop_event,
                "model": None,
                "tool_result": None,
            }

        message_box: dict[str, str | None] = {"message": None}
        model_handler = None
        overlay = None
        try:
            self._save_metadata(metadata, "running", error=None)
            expanded = set(snapshot.get("expanded_tools") or [])
            registry = self._build_registry(snapshot, message_box, stop_event)
            prompts = self._build_prompts(
                snapshot, registry, expanded, task, chat_id
            )
            model_handler, overlay = self._isolated_model(snapshot)
            with self._state_lock:
                self._active[requested_session]["model"] = model_handler
            if stop_event.is_set():
                model_handler.stop()

            # Local backends need the same explicit load step as regular
            # handlers; remote handlers implement this as a cheap no-op.
            if not stop_event.is_set():
                model_handler.load_model(None)
            metadata["snapshot"] = copy.deepcopy(snapshot)
            self.controller.save_chats()

            last_message = [""]

            def capture_message(text):
                text = str(text)
                if text.startswith(last_message[0]) or last_message[0].startswith(text):
                    last_message[0] = text
                else:
                    last_message[0] += text
                if on_message is not None:
                    on_message(last_message[0])

            def capture_tool_result(tool_name, tool_result):
                with self._state_lock:
                    state = self._active.get(requested_session)
                    if state is not None:
                        state["tool_result"] = tool_result
                if stop_event.is_set():
                    tool_result.cancel()
                if on_tool_result is not None:
                    on_tool_result(tool_name, tool_result)

            final = self.controller.run_llm_with_tools(
                message=task,
                chat_id=chat_id,
                system_prompt=prompts,
                on_message_callback=capture_message,
                on_tool_result_callback=capture_tool_result,
                save_chat=True,
                force_tools_on_main_thread=True,
                tool_registry=registry,
                extension_processing=False,
                model=model_handler,
                tools_settings=snapshot.get("tool_settings", {}),
                expanded_tools=expanded,
                stop_generation_event=stop_event,
            )
            snapshot["expanded_tools"] = sorted(expanded)
            metadata["snapshot"] = copy.deepcopy(snapshot)
            message = message_box["message"]
            if message is None:
                message = str(final or last_message[0] or "")
            was_cancelled = stop_event.is_set() and message_box["message"] is None
            status = "interrupted" if was_cancelled else "terminated"
            self._save_metadata(
                metadata,
                status,
                error="The subagent turn was stopped." if was_cancelled else None,
            )
            return {
                "session_id": requested_session,
                "subagent": snapshot.get("id"),
                "status": status,
                "message": message,
                "warnings": warnings,
            }
        except Exception as error:
            if stop_event.is_set() and message_box["message"] is None:
                self._save_metadata(metadata, "interrupted", error=str(error))
                return {
                    "session_id": requested_session,
                    "subagent": snapshot.get("id"),
                    "status": "interrupted",
                    "message": "The subagent turn was stopped.",
                    "warnings": warnings,
                }
            self._save_metadata(metadata, "failed", error=str(error))
            return self.error_result(
                error,
                session_id=requested_session,
                subagent=snapshot.get("id"),
                warnings=warnings,
            )
        finally:
            if model_handler is not None:
                try:
                    model_handler.destroy()
                except Exception:
                    pass
            if overlay is not None:
                SettingsCache._instances.pop(overlay, None)
            delete_owner = False
            with self._state_lock:
                self._active.pop(requested_session, None)
                delete_owner = owner_chat_id in self._pending_cleanup_owners
            if delete_owner:
                self.cleanup_owner_sessions(owner_chat_id)
