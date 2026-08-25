from ..extensions import NewelleExtension
from ..tools import InteractionOption, Tool, ToolResult, create_io_tool 
from ..ui.widgets import CommandSessionActionWidget, CopyBox
from gettext import gettext as _
import os
from ..utility.system import is_flatpak
from ..utility.system import get_spawn_command
from ..utility.strings import quote_string, add_S_to_sudo
from ..utility.command_runner import (
    CommandRunner,
    DEFAULT_COMMAND_TIMEOUT,
    MAX_COMMAND_TIMEOUT,
    get_command_execution_manager,
)
from ..utility.command_sessions import (
    CommandSessionError,
    DEFAULT_SESSION_OUTPUT_CHARS,
    DEFAULT_SESSION_WAIT_MS,
    MAX_SESSION_OUTPUT_CHARS,
    MAX_SESSION_WAIT_MS,
    format_session_result,
    get_command_session_manager,
)
from gi.repository import Gtk, Gio
from ..ui import load_image_with_callback
from ..ui.widgets.terminal_dialog import TerminalDialog


class DefaultToolsIntegration(NewelleExtension):
    id = "default_tools"
    name = "Default Tools"

    def _on_copybox_terminal_clicked(self, copybox, command, execution_request_mode):
        shell_command = "cd " + quote_string(os.getcwd()) + "; " + command + "; exec bash"

        if not self.settings.get_boolean("virtualization"):
            shell_command = add_S_to_sudo(shell_command)
            terminal_command = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;alias sudo=\"sudo -S\";" + shell_command]
        else:
            terminal_command = ["bash", "-c", "export TERM=xterm-256color;" + shell_command]

        terminal = TerminalDialog(
            parent_window=getattr(getattr(self, "ui_controller", None), "window", None)
        )

        def save_output(save):
            if save is None:
                return
            copybox.complete_execution(save)

        terminal.load_terminal(terminal_command)
        terminal.save_output_func(save_output)
        terminal.present()

    def _on_session_terminal_clicked(self, session_widget, session_id, chat_id):
        try:
            opened = self.open_session_terminal(session_id, chat_id)
        except CommandSessionError:
            opened = False
        if not opened:
            session_widget.set_active_session_available(False)

    def open_session_terminal(self, session_id: str, chat_id: int | None) -> bool:
        """Open a live persistent session in an interactive terminal dialog."""
        session = get_command_session_manager().get(
            session_id,
            self._session_owner(chat_id),
        )
        if not session.is_running:
            return False

        terminal_dialogs = getattr(self, "_session_terminal_dialogs", None)
        if terminal_dialogs is None:
            terminal_dialogs = {}
            self._session_terminal_dialogs = terminal_dialogs
        existing_dialog = terminal_dialogs.get(session.session_id)
        if existing_dialog is not None:
            existing_dialog.present()
            return True

        terminal = TerminalDialog(
            confirm_output=False,
            parent_window=getattr(getattr(self, "ui_controller", None), "window", None),
        )
        terminal.set_title(f"{terminal.get_title()} · {session.session_id}")
        terminal.load_session(session)
        terminal_dialogs[session.session_id] = terminal

        def forget_dialog(dialog):
            if terminal_dialogs.get(session.session_id) is dialog:
                terminal_dialogs.pop(session.session_id, None)

        terminal.connect("close-request", forget_dialog)
        terminal.present()
        return True

    def terminate_session(self, session_id: str, chat_id: int | None) -> None:
        """Terminate and forget a persistent session owned by a chat."""
        manager = get_command_session_manager()
        session = manager.get(session_id, self._session_owner(chat_id))
        session.terminate()
        manager.forget(session)

    def cancel_command_execution(self, execution_id: str, chat_id: int | None) -> None:
        """Cancel a tracked bounded command owned by a chat."""
        manager = get_command_execution_manager()
        manager.cancel(execution_id, self._session_owner(chat_id))

    @staticmethod
    def _activity_chat_id(owner, scope):
        """Extract a chat ID from the internal chat-scoped owner token."""
        if not isinstance(owner, tuple) or len(owner) != 3:
            return None
        if owner[0] != "chat" or owner[1] != scope:
            return None
        try:
            return int(owner[2])
        except (TypeError, ValueError):
            return owner[2]

    def get_active_command_activities(self) -> list[dict]:
        """Return active command and persistent-session work for the GUI scope."""
        scope = id(getattr(self, "ui_controller", self))
        controller = None
        ui_controller = getattr(self, "ui_controller", None)
        if ui_controller is not None:
            window = getattr(ui_controller, "window", None)
            controller = getattr(window, "controller", None)
            if controller is None:
                controller = getattr(ui_controller, "controller", None)
        chats = getattr(controller, "chats", {}) or {}
        groups = {}

        def group_for(chat_id):
            if chat_id not in groups:
                chat_entry = chats.get(chat_id, {})
                if not chat_entry and isinstance(chat_id, str):
                    try:
                        chat_entry = chats.get(int(chat_id), {})
                    except ValueError:
                        pass
                groups[chat_id] = {
                    "chat_id": chat_id,
                    "chat_name": chat_entry.get(
                        "name", _("Chat {id}").format(id=chat_id)
                    ),
                    "sessions": [],
                    "executions": [],
                }
            return groups[chat_id]

        for session in get_command_session_manager().list_all():
            if not session.is_running:
                continue
            chat_id = self._activity_chat_id(session.owner, scope)
            if chat_id is not None:
                group_for(chat_id)["sessions"].append(session)

        for execution in get_command_execution_manager().list_all():
            if not execution.is_running:
                continue
            chat_id = self._activity_chat_id(execution.owner, scope)
            if chat_id is not None:
                group_for(chat_id)["executions"].append(execution)

        return sorted(groups.values(), key=lambda group: str(group["chat_name"]).lower())

    def _host_prefix(self) -> list[str]:
        if is_flatpak() and not self.settings.get_boolean("virtualization"):
            return get_spawn_command()
        return []

    def _working_dir(self) -> str:
        return self.settings.get_string("path") or os.getcwd()

    def _timeout(self, timeout_seconds: int | None) -> int:
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_COMMAND_TIMEOUT
        try:
            return max(1, min(int(timeout_seconds), MAX_COMMAND_TIMEOUT))
        except (TypeError, ValueError) as error:
            raise CommandSessionError("timeout_seconds must be an integer") from error

    def _session_owner(self, chat_id: int | None):
        if chat_id is None and hasattr(self, "ui_controller"):
            chat_id = self.ui_controller.get_current_chat_id()
        if chat_id is None:
            raise CommandSessionError("A chat ID is required for terminal sessions")
        controller_scope = id(getattr(self, "ui_controller", self))
        return ("chat", controller_scope, str(chat_id))

    @staticmethod
    def _tool_result(output: str) -> ToolResult:
        result = ToolResult()
        result.set_output(output)
        return result

    def _session_action_result(
        self,
        action: str,
        output: str,
        *,
        session_id: str | None = None,
        keys: list | None = None,
        chat_id: int | None = None,
    ) -> ToolResult:
        result = ToolResult()
        result.set_output(output)
        widget = CommandSessionActionWidget(
            action,
            output,
            session_id=session_id,
            keys=keys,
        )
        widget.connect(
            "terminal-clicked",
            lambda session_widget, active_session_id: self._on_session_terminal_clicked(
                session_widget,
                active_session_id,
                chat_id,
            ),
        )
        result.set_widget(widget)
        return result

    @staticmethod
    def _error_output(action: str, error: Exception, *, startup: bool = False) -> str:
        status = "startup-error" if startup else "failure"
        return f"Status: {status}\nAction: {action}\nError: {error}"

    def execute_command(
        self,
        command: str | None,
        timeout_seconds: int | None = None,
        chat_id: int | None = None,
    ) -> str:
        """Run one non-interactive command and return its bounded result."""
        if command is None:
            return "Status: skipped\nThe user skipped the command execution."
        runner = CommandRunner(self._timeout(timeout_seconds))
        return runner.run(
            command,
            self._working_dir(),
            host_prefix=self._host_prefix(),
            owner=(self._session_owner(chat_id) if chat_id is not None else None),
            execution_manager=get_command_execution_manager(),
        ).to_output()

    def _start_session(self, command: str, chat_id: int | None, wait_ms: int, max_output_chars: int) -> str:
        manager = get_command_session_manager()
        session = manager.start(
            command,
            self._working_dir(),
            self._session_owner(chat_id),
            host_prefix=self._host_prefix(),
        )
        initial_output = session.read(
            wait_ms=wait_ms,
            max_chars=max_output_chars,
            mode="incremental",
        )
        return format_session_result(session, initial_output, action="start")

    def _command_request(
        self,
        *,
        action_name: str,
        command: str | None,
        chat_id: int | None,
        timeout_seconds: int | None,
        wait_ms: int,
        max_output_chars: int,
    ) -> ToolResult:
        from ..utility.command_permissions import CommandPermissionManager, CommandAction

        if not isinstance(command, str) or not command.strip():
            return self._tool_result(
                self._error_output(
                    action_name,
                    CommandSessionError(f"command is required for the {action_name} action"),
                    startup=True,
                )
            )

        perm_manager = CommandPermissionManager.get_instance(self.settings)
        working_dir = self._working_dir()
        action, reason = perm_manager.check_command(command, working_dir)

        result = ToolResult(requires_interaction=(action != CommandAction.ALLOW))

        def execute_callback(approved_command):
            if approved_command is None:
                output = "Status: skipped\nThe user skipped the command execution."
            else:
                try:
                    if action_name == "start":
                        output = self._start_session(
                            approved_command,
                            chat_id,
                            wait_ms,
                            max_output_chars,
                        )
                    else:
                        output = self.execute_command(
                            approved_command,
                            timeout_seconds,
                            chat_id=chat_id,
                        )
                except Exception as error:
                    output = self._error_output(
                        action_name,
                        error,
                        startup=(action_name == "start"),
                    )
            result.set_output(output)
            return output

        widget = CopyBox(
            command,
            "console",
            execution_request=True,
            run_callback=execute_callback,
            managed_terminal=(action_name == "start"),
        )
        if action_name == "start":
            widget.connect(
                "terminal-clicked",
                lambda copybox, _command, _mode: self._on_session_terminal_clicked(
                    copybox,
                    copybox.active_session_id,
                    chat_id,
                ),
            )
        else:
            widget.connect("terminal-clicked", self._on_copybox_terminal_clicked)

        if action == CommandAction.BLOCK:
            output = f"Status: blocked\nReason: {reason}"
            widget.complete_execution(output)
            result.set_output(output)
            result.requires_interaction = False
            result.set_display_text("```bash\n" + command + "\n```\n\n**Blocked:** " + reason)
            result.set_widget(widget)
            return result

        if action == CommandAction.ALLOW and self.settings.get_boolean("auto-run"):
            widget._on_execution_run_clicked(None)
        else:
            result.set_intreaction_options([
                InteractionOption(_("Accept"), lambda command=command: execute_callback(command)),
                InteractionOption(_("Skip"), lambda: execute_callback(None))])
            result.requires_interaction = True 
        widget.connect("command-complete", lambda _, output: result.set_output(output))

        result.set_widget(widget)
        result.set_display_text("```bash\n" + command + "\n```")
        return result

    def execute_command_widget(
        self,
        command: str | None = None,
        action: str = "run",
        session_id: str | None = None,
        input_text: str | None = None,
        keys: list | None = None,
        timeout_seconds: int | None = None,
        wait_ms: int = DEFAULT_SESSION_WAIT_MS,
        max_output_chars: int = DEFAULT_SESSION_OUTPUT_CHARS,
        read_mode: str = "incremental",
        chat_id: int | None = None,
    ) -> ToolResult:
        """Run a command or control a chat-owned persistent terminal session."""
        normalized_action = (action or "run").strip().lower().replace("-", "_")
        aliases = {"keys": "send_keys", "kill": "terminate", "sessions": "list"}
        normalized_action = aliases.get(normalized_action, normalized_action)

        try:
            wait_ms = max(0, min(int(wait_ms), MAX_SESSION_WAIT_MS))
            max_output_chars = max(1, min(int(max_output_chars), MAX_SESSION_OUTPUT_CHARS))
        except (TypeError, ValueError) as error:
            output = self._error_output(normalized_action, error)
            if normalized_action in ("read", "send_keys"):
                return self._session_action_result(
                    normalized_action,
                    output,
                    session_id=session_id,
                    keys=keys,
                    chat_id=chat_id,
                )
            return self._tool_result(output)

        if normalized_action in ("run", "start"):
            return self._command_request(
                action_name=normalized_action,
                command=command,
                chat_id=chat_id,
                timeout_seconds=timeout_seconds,
                wait_ms=wait_ms,
                max_output_chars=max_output_chars,
            )

        manager = get_command_session_manager()
        try:
            owner = self._session_owner(chat_id)
            if normalized_action == "list":
                sessions = manager.list(owner)
                lines = ["Status: success", "Action: list", f"Sessions: {len(sessions)}"]
                for session in sessions:
                    state = "running" if session.is_running else "exited"
                    exit_suffix = "" if session.is_running else f", exit_code={session.exit_code}"
                    lines.append(
                        f"- {session.session_id}: {state}{exit_suffix}, pid={session.pid}, "
                        f"cwd={session.working_dir}, command={session.command!r}"
                    )
                return self._tool_result("\n".join(lines))

            session = manager.get(session_id, owner)
            if normalized_action == "read":
                read_result = session.read(
                    wait_ms=wait_ms,
                    max_chars=max_output_chars,
                    mode=read_mode,
                )
                output = format_session_result(session, read_result, action="read")
            elif normalized_action == "write":
                written = session.write_text(input_text)
                output = format_session_result(session, action="write") + f"\nBytes Written: {written}"
            elif normalized_action == "send_keys":
                written = session.send_keys(keys)
                output = format_session_result(session, action="send_keys") + f"\nBytes Written: {written}"
            elif normalized_action == "terminate":
                session.terminate()
                manager.forget(session)
                final_output = session.read(wait_ms=0, max_chars=max_output_chars, mode="snapshot")
                output = format_session_result(session, final_output, action="terminate")
            else:
                raise CommandSessionError(
                    "action must be one of: run, start, read, write, send_keys, list, terminate"
                )
            if normalized_action in ("read", "send_keys"):
                return self._session_action_result(
                    normalized_action,
                    output,
                    session_id=session_id,
                    keys=keys,
                    chat_id=chat_id,
                )
            return self._tool_result(output)
        except Exception as error:
            output = self._error_output(normalized_action, error)
            if normalized_action in ("read", "send_keys"):
                return self._session_action_result(
                    normalized_action,
                    output,
                    session_id=session_id,
                    keys=keys,
                    chat_id=chat_id,
                )
            return self._tool_result(output)

    def execute_command_restore(
        self,
        tool_uuid: str,
        command: str | None = None,
        action: str = "run",
        session_id: str | None = None,
        keys: list | None = None,
        chat_id: int | None = None,
        **_kwargs,
    ):
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)
        normalized_action = (action or "run").strip().lower().replace("-", "_")
        normalized_action = {"keys": "send_keys"}.get(
            normalized_action,
            normalized_action,
        )
        if normalized_action in ("read", "send_keys"):
            output = (
                output
                or "Status: failure\nStored terminal result is unavailable."
            )
            return self._session_action_result(
                normalized_action,
                output,
                session_id=session_id,
                keys=keys,
                chat_id=chat_id,
            )
        if normalized_action not in ("run", "start") or command is None:
            return self._tool_result(output or "Status: failure\nStored terminal result is unavailable.")

        widget = CopyBox(
            command,
            "console",
            execution_request=True,
            managed_terminal=(normalized_action == "start"),
        )
        if normalized_action == "start":
            widget.connect(
                "terminal-clicked",
                lambda copybox, _command, _mode: self._on_session_terminal_clicked(
                    copybox,
                    copybox.active_session_id,
                    chat_id,
                ),
            )
        else:
            widget.connect("terminal-clicked", self._on_copybox_terminal_clicked)
        if output is None or "skipped" in output.lower():
            output = None
        widget.complete_execution(output)
        result = ToolResult()
        result.set_widget(widget)
        result.set_output(output)
        return result

    def show_image(self, image_path_or_url: str):
        image_path = image_path_or_url
        image = Gtk.Image(css_classes=["image"])
        if image_path.startswith("http"):
            img = image
            load_image_with_callback(
                image_path,
                lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf())
            )
        else:
            image.set_from_file(image_path)

        result = ToolResult()
        result.set_widget(image)
        result.set_display_text("```image\n" + image_path + "\n```")
        result.set_output(None)
        return result

    def read_image(
        self,
        path: str | None = None,
        image_path: str | None = None,
    ):
        requested_path = path or image_path
        result = ToolResult()
        if not requested_path:
            result.set_output("Error: An image path is required.")
            return result

        expanded_path = os.path.expanduser(requested_path)
        if not os.path.isabs(expanded_path):
            expanded_path = os.path.join(self._working_dir(), expanded_path)
        resolved_path = os.path.abspath(expanded_path)

        if not os.path.isfile(resolved_path):
            result.set_output(f"Error: Image file not found: {resolved_path}")
            return result

        context_message = f"```image\n{resolved_path}\n```"
        result.set_context_messages([context_message])
        result.set_display_text(context_message)
        result.set_output(context_message)
        return result
    
    def show_video(self, video_path: str):
            result = ToolResult() 
            video = Gtk.Video(css_classes=["video"], vexpand=True, hexpand=True)
            video.set_size_request(-1, 400)
            video.set_file(Gio.File.new_for_path(video_path))
            result.set_widget(video)
            result.set_output(None)
            return result

    def speech_to_text(self, file_path: str):
        return self.stt.recognize_file(file_path)

    def text_to_speech(self, text: str, file_path: str = None, speak: bool = True):
        if self.tts is None:
            return "TTS is not enabled. Please enable TTS in settings."
        
        result_messages = []
        
        if file_path:
            self.tts.save_audio(text, file_path)
            result_messages.append(f"Audio saved to: {file_path}")
        
        if speak:
            self.tts.play(text)
            result_messages.append("Audio played.")
        
        if not result_messages:
            return "No action taken. Provide a file_path to save or set speak=True."
        
        return "\n".join(result_messages)

    def get_tools(self) -> list:
        return [
            Tool(
                name="execute_command",
                description=(
                    "Run a bounded one-shot shell command or control a persistent PTY session. "
                    "Use action=start for interactive programs, then read, write, send_keys, "
                    "list, or terminate with the returned chat-scoped session ID."
                ),
                func=self.execute_command_widget,
                schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["run", "start", "read", "write", "send_keys", "list", "terminate"],
                            "default": "run",
                            "description": "Operation to perform. Omit for a backward-compatible one-shot run.",
                        },
                        "command": {
                            "type": "string",
                            "description": "Shell command; required for run and start.",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session returned by start; required for read, write, send_keys, and terminate.",
                        },
                        "input_text": {
                            "type": "string",
                            "description": "Exact text to write. It is not followed by Enter automatically.",
                        },
                        "keys": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [
                                    "ENTER", "TAB", "SHIFT_TAB", "ESC", "SPACE", "BACKSPACE",
                                    "DELETE", "UP", "DOWN", "RIGHT", "LEFT", "HOME", "END",
                                    "PAGE_UP", "PAGE_DOWN", "CTRL_A", "CTRL_B", "CTRL_C", "CTRL_D",
                                    "CTRL_E", "CTRL_F", "CTRL_G", "CTRL_H", "CTRL_I", "CTRL_J",
                                    "CTRL_K", "CTRL_L", "CTRL_M", "CTRL_N", "CTRL_O", "CTRL_P",
                                    "CTRL_Q", "CTRL_R", "CTRL_S", "CTRL_T", "CTRL_U", "CTRL_V",
                                    "CTRL_W", "CTRL_X", "CTRL_Y", "CTRL_Z", "CTRL_BACKSLASH",
                                    "CTRL_RIGHT_BRACKET",
                                ],
                            },
                            "description": "Special keystrokes for send_keys.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_COMMAND_TIMEOUT,
                            "default": DEFAULT_COMMAND_TIMEOUT,
                            "description": "One-shot run timeout in seconds.",
                        },
                        "wait_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_SESSION_WAIT_MS,
                            "default": DEFAULT_SESSION_WAIT_MS,
                            "description": "How long start/read may wait for fresh terminal output.",
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_SESSION_OUTPUT_CHARS,
                            "default": DEFAULT_SESSION_OUTPUT_CHARS,
                            "description": "Maximum terminal characters returned by start/read/terminate.",
                        },
                        "read_mode": {
                            "type": "string",
                            "enum": ["incremental", "snapshot"],
                            "default": "incremental",
                            "description": "Read only unseen output or a bounded snapshot of recent output.",
                        },
                    },
                },
                title="Execute Command",
                restore_func=self.execute_command_restore,
                default_on=True,
                icon_name="gnome-terminal-symbolic",
            ),
            Tool(
                name="show_image",
                description="Show an image from a given file path or URL.",
                func=self.show_image,
                title="Show Image",
                default_on=True,
                restore_func=self.show_image,
                tools_group=_("Media Display")

            ),
            Tool(
                name="read_image",
                description="Read an image from a local file path and add it to the model context.",
                func=self.read_image,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Local path to the image. Relative paths use the configured working directory.",
                        },
                    },
                    "required": ["path"],
                },
                title="Read Image",
                default_on=True,
                restore_func=self.read_image,
                tools_group=_("Media Display"),
                icon_name="image-x-generic-symbolic",

            ),
            Tool(
                name="show_video",
                description="Show a video from a given file path.",
                func=self.show_video,
                title="Show Video",
                default_on=True,
                restore_func=self.show_video,
                tools_group=_("Media Display")

            ),
            create_io_tool("speech_to_text","Recognize audio files and return their text.",  self.speech_to_text, default_on=False, tools_group=_("Audio")),
            create_io_tool("text_to_speech", "Convert text to speech. Can save to a file and/or speak aloud.", self.text_to_speech, default_on=False, tools_group=_("Audio")),
        ]
