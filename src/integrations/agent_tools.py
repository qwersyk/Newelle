import threading
import json
import time
from gi.repository import Gtk, Adw, GLib, Gio
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from ..ui.widgets.subagent import SubagentWidget
from ..ui.widgets.scheduled_task import ScheduledTaskWidget
from ..ui.widgets.question import QuestionWidget, RestoredQuestionWidget
from ..ui.widgets.comborow import ComboRowHelper
from ..ui.widgets.status import StatusWidget



class AgentToolsIntegration(NewelleExtension):
    id = "agent_tools"
    name = "Agent Tools"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    @property
    def controller(self):
        direct = getattr(self, "runtime_controller", None)
        if direct is not None:
            return direct
        return self.ui_controller.window.controller

    def _available_modes(self):
        """Return the names of all modes, or [] if the controller isn't ready yet.

        Safe to call at any point in the integration lifecycle (including
        ``get_tools()``, which runs before the UI controller is wired up).
        """
        try:
            controller = self.ui_controller.window.controller
        except AttributeError:
            return []
        mm = getattr(controller, "mode_manager", None)
        if mm is None:
            return []
        return list(mm.get_modes().keys())

    def _subagent_catalog(self):
        manager = getattr(self.controller, "subagent_manager", None)
        if manager is None:
            return []
        definitions = manager.get_subagents(enabled_only=True) or {}
        return sorted(
            definitions.items(),
            key=lambda item: str(item[1].get("name", item[0])).casefold(),
        )

    def _run_subagent_schema(self):
        """Schema for named launch, durable resume, and legacy inline launch."""
        catalog = self._subagent_catalog()
        catalog_text = "; ".join(
            f"{identifier} ({definition.get('name', identifier)}"
            + (
                f": {definition['description']}"
                if definition.get("description")
                else ""
            )
            + ")"
            for identifier, definition in catalog
        )
        subagent_property = {
            "type": "string",
            "minLength": 1,
            "description": (
                "Stable ID of a configured subagent. Its current profile and "
                "mode must enable it."
                + (f" Available definitions: {catalog_text}." if catalog_text else "")
            ),
        }
        if catalog:
            subagent_property["enum"] = [identifier for identifier, _ in catalog]
        properties = {
            "task": {
                "type": "string",
                "minLength": 1,
                "description": "Detailed task or next message for the subagent.",
            },
            "subagent": subagent_property,
            "session_id": {
                "type": "string",
                "minLength": 1,
                "description": "Session ID returned by an earlier terminated turn.",
            },
            "system_prompt": {
                "type": "string",
                "minLength": 1,
                "description": "Legacy inline system prompt.",
            },
            "tools": {
                "type": "string",
                "minLength": 1,
                "description": "Legacy inline comma-separated tool names.",
            },
            "skills": {
                "type": "string",
                "description": "Legacy inline comma-separated skill names.",
            },
        }
        return {
            "type": "object",
            "properties": properties,
            "required": ["task"],
            # Runtime validation reports a clear error for conflicting forms;
            # ``anyOf`` remains compatible with providers that materialize
            # optional fields while still requiring one complete launch form.
            "anyOf": [
                {"required": ["task", "subagent"]},
                {"required": ["task", "session_id"]},
                {"required": ["task", "system_prompt", "tools"]},
            ],
        }

    def _has_ui(self):
        ui_controller = getattr(self, "ui_controller", None)
        return ui_controller is not None and getattr(ui_controller, "window", None) is not None

    def _make_subagent_widget(self, task, subagent_name=None, session_id=None):
        if not self._has_ui():
            return None
        if threading.current_thread() is threading.main_thread():
            return SubagentWidget(task, subagent_name=subagent_name, session_id=session_id)

        created = {}
        done = threading.Event()

        def create():
            created["widget"] = SubagentWidget(
                task, subagent_name=subagent_name, session_id=session_id
            )
            done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(create)
        done.wait()
        return created.get("widget")

    def _notify_tool_interaction(self, tool_name):
        if not self._has_ui():
            return

        def notify():
            try:
                window = self.ui_controller.window
                if window and not window.is_active():
                    app = Gio.Application.get_default()
                    if app:
                        notification = Gio.Notification.new(_("Action Required"))
                        notification.set_body(
                            _("The tool '{name}' requires your interaction.").format(
                                name=tool_name
                            )
                        )
                        app.send_notification("tool-interaction", notification)
            except Exception as error:
                print(f"Failed to send notification: {error}")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(notify)

    def _run_subagent(
        self,
        task: str,
        subagent: str = "",
        session_id: str = "",
        system_prompt: str = "",
        tools: str = "",
        skills: str = "",
        tool_uuid=None,
        chat_id=None,
    ):
        """Launch or resume a durable, turn-based subagent session."""
        stop_event = threading.Event()
        result = ToolResult(
            cancel_callback=lambda: self.controller.subagent_runtime.cancel(
                stop_event
            )
        )
        subagent_name = None
        if subagent:
            manager = getattr(self.controller, "subagent_manager", None)
            definition = manager.get_subagent(subagent) if manager is not None else None
            if definition:
                subagent_name = definition.get("name")
        widget = self._make_subagent_widget(
            task, subagent_name=subagent_name, session_id=session_id or None
        )
        if widget is not None:
            result.set_widget(widget)

        def run():
            try:
                if chat_id is None:
                    raise ValueError("The owning main chat is unavailable.")
                if widget is not None:
                    widget.set_status(_("Running…"))

                def on_message(text: str):
                    if widget is not None:
                        widget.update_message(text)

                def on_tool_result(tool_name: str, tool_result: ToolResult):
                    if widget is None:
                        return
                    widget.set_status(_("Tool: ") + tool_name)
                    widget.add_tool_widget(tool_name, tool_result)
                    if tool_result.requires_interaction:
                        widget.expand()
                        self._notify_tool_interaction(tool_name)

                payload = self.controller.subagent_runtime.run_turn(
                    task=task,
                    owner_chat_id=chat_id,
                    subagent=subagent or None,
                    session_id=session_id or None,
                    system_prompt=system_prompt or None,
                    tools=tools,
                    skills=skills,
                    on_message=on_message,
                    on_tool_result=on_tool_result,
                    stop_event=stop_event,
                )
            except Exception as error:
                payload = self.controller.subagent_runtime.error_result(
                    error,
                    session_id=session_id or None,
                    subagent=subagent or None,
                )

            if widget is not None:
                widget.set_session(
                    payload.get("session_id"),
                    subagent_name=subagent_name,
                )
                widget.finish(
                    success=payload.get("status") == "terminated",
                    summary=(
                        _("Completed")
                        if payload.get("status") == "terminated"
                        else _("Stopped")
                        if payload.get("status") == "interrupted"
                        else payload.get("message", _("Failed"))
                    ),
                )
            if not result.is_cancelled:
                result.set_output(json.dumps(payload, ensure_ascii=False))

        threading.Thread(target=run, daemon=True).start()
        return result

    def _stored_tool_output(self, tool_uuid, chat_id=None):
        if chat_id is not None:
            output = self.controller.get_tool_response(
                chat_id, 0, "run_subagent", tool_uuid, strict=True
            )
            if output is not None:
                return output
        if self._has_ui():
            return self.ui_controller.get_tool_result_by_id(tool_uuid)
        return None

    def _restore_subagent(
        self,
        tool_uuid: str,
        task: str,
        subagent: str = "",
        session_id: str = "",
        system_prompt: str = "",
        tools: str = "",
        skills: str = "",
        chat_id=None,
    ):
        output = self._stored_tool_output(tool_uuid, chat_id)
        payload = None
        try:
            payload = json.loads(output) if output else None
        except (json.JSONDecodeError, TypeError):
            pass
        if not isinstance(payload, dict):
            payload = {
                "session_id": session_id or None,
                "subagent": subagent or None,
                "status": "terminated",
                "message": output or "",
                "warnings": [],
            }

        widget = self._make_subagent_widget(
            task,
            subagent_name=subagent or None,
            session_id=payload.get("session_id"),
        )
        restored = ToolResult()
        if widget is not None:
            widget.update_message(payload.get("message", ""))
            status = payload.get("status")
            widget.finish(
                success=status == "terminated",
                summary=(
                    _("Completed")
                    if status == "terminated"
                    else _("Stopped")
                    if status == "interrupted"
                    else _("Failed")
                ),
            )
            restored.set_widget(widget)
        restored.set_output(output)
        return restored

    def _schedule_task(self, task: str, run_at: str = "", cron: str = ""):
        """Schedule a future agent run in a visible chat."""
        scheduled_task = self.controller.create_scheduled_task(
            task=task,
            run_at=run_at.strip() or None,
            cron=cron.strip() or None,
        )

        result = ToolResult()

        widget = ScheduledTaskWidget(
            task=task,
            schedule_type=scheduled_task["schedule_type"],
            run_at=scheduled_task.get("run_at"),
            cron=scheduled_task.get("cron"),
            next_run_at=scheduled_task.get("next_run_at"),
            task_id=scheduled_task["id"],
            controller=self.controller,
            folder_id=scheduled_task.get("folder_id"),
        )
        result.set_widget(widget)

        result.set_output(
            json.dumps(
                {
                    "success": True,
                    "id": scheduled_task["id"],
                    "task": scheduled_task["task"],
                    "schedule_type": scheduled_task["schedule_type"],
                    "run_at": scheduled_task["run_at"],
                    "cron": scheduled_task["cron"],
                    "next_run_at": scheduled_task["next_run_at"],
                    "enabled": scheduled_task["enabled"],
                    "folder_id": scheduled_task.get("folder_id"),
                },
                indent=2,
            )
        )

        return result

    def _restore_schedule_task(self, tool_uuid: str, task: str, run_at: str = "", cron: str = ""):
        """Restore the scheduled task widget from chat history."""
        # Get the saved output from chat history
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)

        # Parse the saved output to get schedule info
        schedule_type = "once"
        saved_run_at = run_at
        saved_cron = cron
        next_run_at = None
        folder_id = None

        if output:
            try:
                data = json.loads(output)
                schedule_type = data.get("schedule_type", "once")
                saved_run_at = data.get("run_at", run_at)
                saved_cron = data.get("cron", cron)
                next_run_at = data.get("next_run_at")
                folder_id = data.get("folder_id")
            except json.JSONDecodeError:
                pass

        result = ToolResult()

        # Create a completed widget
        widget = ScheduledTaskWidget(
            task=task,
            schedule_type=schedule_type,
            run_at=saved_run_at,
            cron=saved_cron,
            next_run_at=next_run_at,
            task_id=tool_uuid[:8] if tool_uuid else "",
            controller=self.controller,
            folder_id=folder_id,
        )

        # Mark as completed
        widget.update_status(_("Task Created"), "success")

        result.set_widget(widget)
        result.set_output(output)
        return result
    def _ask_user(self, question: str, options: str = "", mode: str = "", multiple: bool = False, tool_uuid=None):
        parsed_options = [o.strip() for o in options.split(",") if o.strip()] if options.strip() else []
        if mode not in ("open", "choice", "choice_with_custom"):
            mode = "choice_with_custom" if parsed_options else "open"
        result = ToolResult(requires_interaction=True)
        widget = QuestionWidget(question, parsed_options, mode=mode, multiple=multiple)
        result.set_widget(widget)

        def wait():
            answer = widget.wait_for_answer()
            result.set_output(answer if answer else "")

        thread = threading.Thread(target=wait, daemon=True)
        thread.start()
        return result

    def _restore_ask_user(self, tool_uuid: str, question: str, options: str = "", mode: str = "", multiple: str = ""):
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)
        parsed_options = [o.strip() for o in options.split(",") if o.strip()] if options.strip() else []
        if mode not in ("open", "choice", "choice_with_custom"):
            mode = "choice_with_custom" if parsed_options else "open"
        is_multiple = multiple in (True, "true", "True", "1") if isinstance(multiple, str) else bool(multiple)
        result = ToolResult()
        result.set_widget(RestoredQuestionWidget(question, parsed_options, output or "", mode=mode, multiple=is_multiple))
        result.set_output(output)
        return result

    def _sleep(self, seconds: float):
        """Wait for the specified number of seconds before continuing.

        Args:
            seconds: Number of seconds to sleep. Must be non-negative.
        """
        if seconds < 0:
            seconds = 0
        message = f"Slept for {seconds} second(s)."
        result = ToolResult()
        widget = StatusWidget(
            title=_("Sleeping…"),
            icon_name="alarm-symbolic",
            subtitle=_("Waiting for {n} second(s)").format(n=seconds),
        )
        result.set_widget(widget)

        def wait():
            time.sleep(seconds)
            # Update the widget in place on the GTK main thread, then unblock
            # get_output() so the tool loop can continue.
            def finish():
                widget.update(title=message, subtitle=_("Paused before continuing"))
            GLib.idle_add(finish)
            result.set_output(message)

        thread = threading.Thread(target=wait, daemon=True)
        thread.start()
        return result

    def _restore_sleep(self, seconds: float):
        message = f"Slept for {seconds} second(s)."
        result = ToolResult()
        result.set_widget(StatusWidget(
            title=message,
            icon_name="alarm-symbolic",
            subtitle=_("Paused before continuing"),
        ))
        result.set_output(None)
        return result

    def _switch_mode(self, mode: str):
        """Switch the assistant to a different mode.

        Args:
            mode: Name of the mode to activate.
        """
        mm = getattr(self.controller, "mode_manager", None)
        result = ToolResult()
        if mm is None:
            result.set_output("Error: modes are not available.")
            return result
        try:
            mm.set_active_mode(mode)
        except ValueError:
            available = ", ".join(mm.get_modes().keys())
            result.set_output(f"Error: mode '{mode}' not found. Available modes: {available}")
            return result
        # Propagate skill overrides and rebuild prompts/tools so the next run
        # uses the new mode. Mirrors ModeButton._on_mode_activated.
        active = mm.get_active_mode()
        self.controller.skill_manager.set_mode_overrides(active.get("skills", {}))
        self.controller.update_settings()
        # Refresh every tab's Mode switcher so the UI reflects the new mode.
        try:
            self.controller.ui_controller.window.refresh_mode_buttons()
        except AttributeError:
            pass
        description = active.get("description", "") or ""
        result.set_output(f"Switched to mode: {mode}")
        result.set_widget(StatusWidget(
            title=mode,
            icon_name=active.get("icon", "applications-system-symbolic"),
            subtitle=description or _("Mode activated"),
            badge=_("active"),
        ))
        return result

    def _restore_switch_mode(self, mode: str):
        mm = getattr(self.controller, "mode_manager", None)
        icon_name = "applications-system-symbolic"
        description = ""
        if mm is not None:
            mode_data = mm.get_mode(mode) or {}
            icon_name = mode_data.get("icon", icon_name)
            description = mode_data.get("description", "") or ""
        result = ToolResult()
        result.set_widget(StatusWidget(
            title=mode,
            icon_name=icon_name,
            subtitle=description or _("Mode activated"),
            badge=_("active"),
        ))
        result.set_output(None)
        return result

    def get_tools(self) -> list:
        return [
            Tool(
                name="run_subagent",
                description=(
                    "Launch an enabled configured subagent, resume a terminated "
                    "subagent session, or use the legacy inline prompt/tools form. "
                    "The returned session_id can be resumed with another task."
                ),
                func=self._run_subagent,
                # Resolve at prompt emission so CRUD, profile toggles, Modes,
                # and extension changes are reflected without rebuilding the
                # whole tool registry.
                schema=self._run_subagent_schema,
                title="Run Subagent",
                restore_func=self._restore_subagent,
                default_on=True,
                icon_name="system-run-symbolic",
                tools_group=_("Agent"),
            ),
            Tool(
                name="schedule_task",
                description=(
                    "Schedule a background agent task that will create a visible chat when it runs. "
                    "Provide either run_at for a one-time run or cron for a recurring schedule."
                    "The task argument is the prompt to be executed by the agent. Give a long and detailed task prompt."
                ),
                func=self._schedule_task,
                title="Schedule Task",
                restore_func=self._restore_schedule_task,
                default_on=True,
                icon_name="alarm-symbolic",
                tools_group=_("Agent"),
            ),
            Tool(
                name="ask_user",
                description=(
                    "Ask the user a question and wait for their response. "
                    "Use this when you need clarification, a decision, or user input to proceed.\n"
                    "Modes:\n"
                    "- 'open': free-text answer only, no predefined options.\n"
                    "- 'choice': user must pick from the provided options (no custom text).\n"
                    "- 'choice_with_custom': user can pick from options or type a custom answer.\n"
                    "If mode is not specified, it defaults to 'choice_with_custom' when options are provided, 'open' otherwise.\n"
                    "Set multiple=true to allow selecting more than one option (only for 'choice' and 'choice_with_custom' modes)."
                ),
                func=self._ask_user,
                schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to ask the user.",
                        },
                        "options": {
                            "type": "string",
                            "description": "Comma-separated list of predefined answer choices.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["open", "choice", "choice_with_custom"],
                            "description": "Question mode. 'open'=free text, 'choice'=pick from options only, 'choice_with_custom'=options + custom text.",
                        },
                        "multiple": {
                            "type": "boolean",
                            "description": "Allow the user to select multiple options. Only applies to 'choice' and 'choice_with_custom' modes.",
                        },
                    },
                    "required": ["question"],
                },
                title="Ask User",
                restore_func=self._restore_ask_user,
                default_on=True,
                icon_name="dialog-question-symbolic",
                tools_group=_("Agent"),
            ),
            Tool(
                name="sleep",
                description=(
                    "Wait for a specified number of seconds before continuing. "
                    "Useful when polling or waiting for a condition to become true."
                ),
                func=self._sleep,
                title="Sleep",
                restore_func=self._restore_sleep,
                default_on=True,
                icon_name="alarm-symbolic",
                tools_group=_("Agent"),
            ),
            Tool(
                name="switch_mode",
                description=(
                    "Switch the assistant to a different mode. Modes change which tools, skills and prompts are active. "
                    "Use this when the user asks to change mode or when another mode is better suited to the task."
                ),
                func=self._switch_mode,
                schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": self._available_modes(),
                            "description": "Name of the mode to activate.",
                        },
                    },
                    "required": ["mode"],
                },
                title="Switch Mode",
                restore_func=self._restore_switch_mode,
                default_on=True,
                icon_name="applications-system-symbolic",
                tools_group=_("Agent"),
            ),
        ]
