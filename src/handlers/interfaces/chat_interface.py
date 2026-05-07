"""
Shared base class for chat-style interfaces (Telegram, API v2, …).

Provides:
- Per-user persistent chat creation under a named folder
- Slash-command dispatch that returns plain text (no protocol-specific code)
- LLM-with-tools execution via ``process_message`` (blocking, call from a thread)
- Pending tool-interaction tracking and resolution
"""

import json
import os
import threading
import uuid

from ...utility.strings import remove_thinking_blocks
from .interface import Interface


class ChatInterface(Interface):
    """Base class for text-channel interfaces that share a command set and LLM pipeline.

    Subclasses **must** set the class-level folder/chat attributes and may
    override ``handle_tool_interaction`` to provide richer UI (e.g. Telegram
    inline keyboards).
    """

    # --- Subclasses override these ---
    folder_name: str = "Chat"
    folder_color: str = "#3584e4"
    folder_icon: str = "folder-symbolic"
    chat_name_prefix: str = "💬 Chat"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._folder_id = None
        self._chat_counter = 0
        self._user_chats: dict[str, int] = {}
        # interaction_id -> {options, user_id, tool_name, result}
        self._pending_interactions: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    #                     Folder / Chat management                         #
    # ------------------------------------------------------------------ #

    def _ensure_folder(self) -> int:
        if self._folder_id is not None:
            return self._folder_id
        for fid, folder in self.controller.folders.items():
            if folder.get("name") == self.folder_name:
                self._folder_id = fid
                self._count_existing_chats()
                return self._folder_id
        self._folder_id = self.controller.create_folder(
            name=self.folder_name, color=self.folder_color, icon=self.folder_icon
        )
        self._count_existing_chats()
        return self._folder_id

    def _count_existing_chats(self) -> int:
        folder_id = self._folder_id
        if folder_id is None or folder_id not in self.controller.folders:
            self._chat_counter = 0
            return 0
        chat_ids = self.controller.folders[folder_id].get("chat_ids", [])
        count = 0
        for cid in chat_ids:
            if cid in self.controller.chats:
                name = self.controller.chats[cid].get("name", "")
                if name.startswith(self.chat_name_prefix):
                    try:
                        num = int(name.split()[-1])
                        count = max(count, num)
                    except (ValueError, IndexError):
                        pass
        self._chat_counter = count
        return count

    def _save_last_chat_id(self, user_id, chat_id: int):
        self.set_setting(f"last_chat_user_{user_id}", chat_id)

    def _load_last_chat_id(self, user_id) -> int | None:
        return self.get_setting(
            f"last_chat_user_{user_id}", search_default=True, return_value=None
        )

    def get_or_create_chat(self, user_id) -> int:
        """Return the persistent chat ID for *user_id*, creating one if needed."""
        user_id = str(user_id)
        if user_id in self._user_chats:
            chat_id = self._user_chats[user_id]
            if chat_id in self.controller.chats:
                return chat_id
        last_chat_id = self._load_last_chat_id(user_id)
        if last_chat_id is not None and last_chat_id in self.controller.chats:
            self._user_chats[user_id] = last_chat_id
            return last_chat_id
        folder_id = self._ensure_folder()
        self._chat_counter += 1
        chat_name = f"{self.chat_name_prefix} {self._chat_counter}"
        chat_id = self.controller.create_visible_chat(name=chat_name, folder_id=folder_id)
        self._user_chats[user_id] = chat_id
        self._save_last_chat_id(user_id, chat_id)
        self.controller.save_chats()
        return chat_id

    # ------------------------------------------------------------------ #
    #               LLM / tool-aware blocking execution                    #
    # ------------------------------------------------------------------ #

    def process_message(self, user_id, text, *, on_chunk=None, on_tool_event=None) -> str:
        """Run *text* through ``run_llm_with_tools`` and return the final response.

        This method is **blocking** – call it from a worker thread.

        Args:
            user_id:        Identifies the per-user persistent chat.
            text:           The user message text.
            on_chunk:       Optional ``(delta: str) -> None`` called for each
                            incremental text chunk (thinking blocks stripped).
            on_tool_event:  Optional ``(event: dict) -> None`` called when a tool
                            result arrives.  For interactive tools the dict has
                            ``type="tool_interaction"`` and an ``interaction_id``
                            that can later be resolved with
                            :meth:`resolve_pending_interaction`.
        """
        user_id = str(user_id)
        chat_id = self.get_or_create_chat(user_id)

        accumulated = ""
        last_cumulative = ""
        is_thinking = False
        state_lock = threading.Lock()

        def _on_message(cumulative_text: str):
            nonlocal accumulated, last_cumulative, is_thinking
            with state_lock:
                cleaned = remove_thinking_blocks(cumulative_text)
                still_thinking = "<think>" in cleaned
                if still_thinking:
                    cleaned = ""

                if cumulative_text.startswith(last_cumulative):
                    raw_delta = cumulative_text[len(last_cumulative):]
                else:
                    raw_delta = cumulative_text
                last_cumulative = cumulative_text

                if not still_thinking and is_thinking:
                    is_thinking = False
                    delta = cleaned
                elif still_thinking:
                    is_thinking = True
                    delta = ""
                else:
                    delta = raw_delta

                if not still_thinking:
                    accumulated += delta
                    if on_chunk and delta:
                        on_chunk(delta)

        def _on_tool(tool_name: str, result):
            display = result.display_text or ""
            event: dict = {
                "tool_name": tool_name,
                "display_text": display,
            }
            if result.requires_interaction:
                interaction_id = str(uuid.uuid4())[:8]
                options = result.interaction_options or []
                event["type"] = "tool_interaction"
                event["interaction_id"] = interaction_id
                event["options"] = [
                    {"index": i, "title": opt.title} for i, opt in enumerate(options)
                ]
                self._pending_interactions[interaction_id] = {
                    "options": options,
                    "user_id": user_id,
                    "tool_name": tool_name,
                    "result": result,
                }
                if on_tool_event:
                    on_tool_event(event)
                # Subclasses (e.g. Telegram) block here to send the keyboard
                # before the LLM thread hits result.get_output().
                self.handle_tool_interaction(user_id, tool_name, result, interaction_id)
            else:
                event["type"] = "tool_result"
                if on_tool_event:
                    on_tool_event(event)

        self.controller.run_llm_with_tools(
            message=text,
            chat_id=chat_id,
            on_message_callback=_on_message,
            on_tool_result_callback=_on_tool,
            save_chat=True,
            force_tools_on_main_thread=True,
        )
        return accumulated

    # ------------------------------------------------------------------ #
    #                     Interaction resolution                           #
    # ------------------------------------------------------------------ #

    def handle_tool_interaction(self, user_id, tool_name, result, interaction_id):
        """Hook called when an interactive tool result needs user input.

        The default implementation is a no-op: the caller is expected to
        surface the options via ``on_tool_event`` and later call
        :meth:`resolve_pending_interaction`.  Subclasses with richer UI
        (e.g. Telegram) override this to send native UI elements and block
        until the send completes.
        """

    def resolve_pending_interaction(self, interaction_id: str, option_index: int) -> bool:
        """Invoke an interaction option, unblocking the paused LLM thread.

        The *option_index* is 0-based.  Returns ``True`` on success.
        """
        entry = self._pending_interactions.pop(interaction_id, None)
        if entry is None:
            return False
        options = entry.get("options", [])
        if option_index < 0 or option_index >= len(options):
            return False
        try:
            options[option_index].callback()
        except Exception as e:
            print(f"[ChatInterface] Interaction callback error: {e}")
        return True

    # ------------------------------------------------------------------ #
    #                       Command dispatch                               #
    # ------------------------------------------------------------------ #

    def run_command(self, name: str, user_id, args: list[str]) -> str:
        """Execute a built-in or controller command, return plain-text response."""
        user_id = str(user_id)
        handler = self._BUILTIN_COMMANDS.get(name)
        if handler:
            try:
                return handler(self, user_id, args)
            except Exception as e:
                return f"❌ Error in /{name}: {e}"
        # Fall through to commands registered by extensions / integrations
        for cmd in self.controller.get_commands():
            if cmd.name == name or cmd.name == f"skill {name}":
                try:
                    result = cmd.execute()
                    output = str(result) if result is not None else "Done."
                    if hasattr(result, "get_output"):
                        output = str(result.get_output())
                    return output[:4000]
                except Exception as e:
                    return f"❌ Error: {e}"
        return f"❌ Unknown command: /{name}"

    def try_handle_command(self, user_id, text: str) -> str | None:
        """If *text* is a slash command, dispatch it and return the response.

        Returns ``None`` if *text* is not a command.
        """
        if not text.startswith("/"):
            return None
        parts = text[1:].split(None, 1)
        if not parts:
            return None
        name = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""
        args = args_str.split() if args_str else []
        return self.run_command(name, user_id, args)

    # ------------------------------------------------------------------ #
    #                       Built-in commands                              #
    # ------------------------------------------------------------------ #

    def _cmd_start(self, user_id, args):
        chat_id = self.get_or_create_chat(user_id)
        chat_name = self.controller.chats[chat_id].get("name", "Chat")
        return (
            f"👋 Welcome to Newelle! Your chat: \"{chat_name}\" (ID: {chat_id})\n\n"
            "📋 Commands:\n"
            "🆕 /new - Create a new chat\n"
            "🤖 /models - List available models\n"
            "🔀 /model [provider:]model - Switch model\n"
            "👤 /profile <name> - Switch profile\n"
            "📝 /prompts - View prompts\n"
            "🔧 /tools - View/toggle tools\n"
            "⏰ /scheduled - View scheduled tasks\n"
            "⚡ /skill <name> - Execute a skill command\n"
            "📂 /cd [path] - Change working directory\n"
            "📋 /list_chats - List all chats\n"
            "👀 /peek <chat_id> - Preview messages from a chat\n"
            "🔄 /resume <chat_id> - Switch to a different chat\n"
            "⚡ /autoexec - Enable/disable auto command execution\n"
            "🔢 /option <n> - Choose an option when prompted\n"
        )

    def _cmd_new(self, user_id, args):
        folder_id = self._ensure_folder()
        if args:
            name = " ".join(args)
        else:
            self._chat_counter += 1
            name = f"{self.chat_name_prefix} {self._chat_counter}"
        chat_id = self.controller.create_visible_chat(name=name, folder_id=folder_id)
        self.controller.save_chats()
        self._user_chats[user_id] = chat_id
        self._save_last_chat_id(user_id, chat_id)
        return f"🆕 New chat created: \"{name}\" (ID: {chat_id})"

    def _cmd_models(self, user_id, args):
        from ...constants import AVAILABLE_LLMS

        current_provider = self.controller.newelle_settings.language_model
        llm_settings = json.loads(self.controller.settings.get_string("llm-settings"))
        lines = []
        for provider_name, provider_info in AVAILABLE_LLMS.items():
            is_current = provider_name == current_provider
            marker = "▶" if is_current else " "
            lines.append(f"{marker} *{provider_info.get('title', provider_name)}*")
            try:
                handler_class = provider_info["class"]
                handler = handler_class(
                    self.controller.settings, self.controller.handlers.directory
                )
                models = handler.get_models_list() if hasattr(handler, "get_models_list") else ()
                if models:
                    for m in list(models)[:40]:
                        model_id = m[0]
                        model_name = m[1] if len(m) > 1 else m[0]
                        current_model = llm_settings.get(provider_name, {}).get("model", "")
                        tick = "✅ " if (is_current and model_id == current_model) else "   "
                        lines.append(f"  └ {tick}{model_name}")
                    if len(models) > 40:
                        lines.append(f"  └ _... and {len(models) - 40} more_")
                else:
                    lines.append("  └ _No models available_")
            except Exception:
                lines.append("  └ _Error loading models_")
        text = "🤖 *Available Models:*\n\n" + "\n".join(lines)
        text += "\n\n💡 Use /model [provider:]<model> to switch"
        return text[:4000]

    def _cmd_model(self, user_id, args):
        from ...constants import AVAILABLE_LLMS

        if not args:
            llm = self.controller.handlers.llm
            provider = self.controller.newelle_settings.language_model
            model = (
                llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default"
            )
            return f"🤖 Current: {provider}:{model}"
        arg = " ".join(args)
        if ":" in arg:
            provider_name, model = arg.split(":", 1)
            if provider_name not in AVAILABLE_LLMS:
                return (
                    f"❓ Unknown provider '{provider_name}'. "
                    f"Available: {', '.join(AVAILABLE_LLMS.keys())}"
                )
            self.controller.settings.set_string("language-model", provider_name)
            llm_settings = json.loads(self.controller.settings.get_string("llm-settings"))
            llm_settings.setdefault(provider_name, {})["model"] = model
            self.controller.settings.set_string("llm-settings", json.dumps(llm_settings))
            self.controller.update_settings()
            return f"✅ Switched to {provider_name}:{model}"
        provider_name = self.controller.newelle_settings.language_model
        llm_settings = json.loads(self.controller.settings.get_string("llm-settings"))
        llm_settings.setdefault(provider_name, {})["model"] = arg
        self.controller.settings.set_string("llm-settings", json.dumps(llm_settings))
        self.controller.update_settings()
        return f"✅ Switched model to {provider_name}:{arg}"

    def _cmd_profile(self, user_id, args):
        profiles = self.controller.newelle_settings.profile_settings
        if not args:
            current = self.controller.newelle_settings.current_profile
            names = ", ".join(profiles.keys())
            return f"👤 Current profile: {current}\nAvailable: {names}"
        name = " ".join(args)
        if name not in profiles:
            return f"❌ Profile '{name}' not found. Available: {', '.join(profiles.keys())}"
        from ...utility.profile_settings import restore_settings_from_dict

        self.controller.update_current_profile()
        self.controller.settings.set_string("current-profile", name)
        profile_data = profiles[name]
        if profile_data.get("settings_groups"):
            restore_settings_from_dict(self.controller.settings, profile_data.get("settings", {}))
        self.controller.update_settings()
        return f"✅ Switched to profile: {name}"

    def _cmd_prompts(self, user_id, args):
        from ...constants import PROMPTS, AVAILABLE_PROMPTS

        ns = self.controller.newelle_settings
        ps = ns.prompts_settings or {}
        merged = ns.prompts if hasattr(ns, "prompts") else {}
        lines = []
        for prompt in AVAILABLE_PROMPTS:
            if not prompt.get("show_in_settings", True):
                continue
            key = prompt.get("key")
            setting_name = prompt.get("setting_name")
            is_active = ps.get(setting_name, prompt.get("default", False))
            if isinstance(merged, dict) and key in merged:
                text = merged[key]
            else:
                raw = PROMPTS.get(key, "")
                text = raw if isinstance(raw, str) else ""
            status = "ON" if is_active else "OFF"
            short = text[:80].replace("\n", " ") + ("..." if len(text) > 80 else "")
            lines.append(
                f"[{status}] {key}"
                + (" (editable)" if prompt.get("editable", False) else "")
            )
            lines.append(f"    {short}")
        return ("\n".join(lines) if lines else "📝 No prompts available.")[:4000]

    def _cmd_tools(self, user_id, args):
        all_tools = self.controller.tools.get_all_tools()
        tools_settings = (
            self.controller.newelle_settings.tools_settings_dict
            if hasattr(self.controller, "newelle_settings")
            else {}
        )
        if args and args[0] == "toggle":
            tool_name = " ".join(args[1:]) if len(args) > 1 else None
            if not tool_name:
                return "📋 Usage: /tools toggle <tool_name>"
            target = next((t for t in all_tools if t.name.lower() == tool_name.lower()), None)
            if target is None:
                return f"❓ Tool '{tool_name}' not found."
            is_enabled = target.default_on
            if target.name in tools_settings and "enabled" in tools_settings[target.name]:
                is_enabled = tools_settings[target.name]["enabled"]
            ts = self.controller.newelle_settings.tools_settings_dict
            ts.setdefault(target.name, {})["enabled"] = not is_enabled
            self.controller.newelle_settings.tools_settings_dict = ts
            self.controller.settings.set_string("tools-settings", json.dumps(ts))
            return f"🔧 Tool '{target.name}' {'enabled' if not is_enabled else 'disabled'}."
        lines = []
        for tool in all_tools:
            is_enabled = tool.default_on
            if tool.name in tools_settings and "enabled" in tools_settings[tool.name]:
                is_enabled = tools_settings[tool.name]["enabled"]
            lines.append(
                f"[{'ON' if is_enabled else 'OFF'}] {tool.name} - {tool.description[:60]}"
            )
        if not lines:
            return "🔧 No tools available."
        return ("\n".join(lines) + "\n\n💡 Use /tools toggle <name> to toggle a tool.")[:4000]

    def _cmd_scheduled(self, user_id, args):
        tasks = self.controller.get_scheduled_tasks()
        if not tasks:
            return "⏰ No scheduled tasks."
        lines = []
        for task in tasks:
            status = (
                "RUNNING" if task.get("running")
                else ("ENABLED" if task.get("enabled") else "DISABLED")
            )
            schedule = task.get("cron") or task.get("run_at") or "manual"
            lines.append(
                f"[{status}] {task['task'][:60]}\n"
                f"    ID: {task['id'][:8]}... | Schedule: {schedule}\n"
                f"    Next: {task.get('next_run_at', 'N/A')}"
            )
        return "\n".join(lines)[:4000]

    def _cmd_skill(self, user_id, args):
        if not args:
            commands = self.controller.get_commands()
            skill_commands = [c for c in commands if c.name.startswith("skill")]
            if not skill_commands:
                return "⚡ No skills available."
            return "\n".join(f"/skill {c.name} - {c.description}" for c in skill_commands)
        skill_name = args[0]
        for cmd in self.controller.get_commands():
            if cmd.name == f"skill {skill_name}" or cmd.name == skill_name:
                try:
                    result = cmd.execute()
                    output = str(result) if result is not None else "Done."
                    if hasattr(result, "get_output"):
                        output = str(result.get_output())
                    return output[:4000]
                except Exception as e:
                    return f"❌ Error: {e}"
        return f"❌ Command/skill '{skill_name}' not found."

    def _cmd_cd(self, user_id, args):
        if not args:
            return f"📂 Current path: {self.controller.settings.get_string('path')}"
        new_path = " ".join(args)
        expanded = os.path.expanduser(new_path)
        if not os.path.isdir(expanded):
            return f"❌ Directory not found: {new_path}"
        self.controller.settings.set_string("path", os.path.normpath(new_path))
        self.controller.update_settings()
        os.chdir(expanded)
        return f"✅ Path changed to: {os.path.normpath(new_path)}"

    def _cmd_list_chats(self, user_id, args):
        current_chat_id = self.controller.newelle_settings.chat_id
        chat_ids = self.controller.chat_ids_ordered()
        if self.controller.newelle_settings.reverse_order:
            chat_ids = list(reversed(chat_ids))
        lines = []
        for cid in chat_ids:
            chat = self.controller.chats[cid]
            if chat.get("call"):
                continue
            marker = "▶" if cid == current_chat_id else " "
            name = chat.get("name", f"Chat {cid}")
            msg_count = len(chat.get("chat", []))
            lines.append(f"{marker} {cid}. {name} ({msg_count} messages)"[:80])
        if not lines:
            return "📭 No chats available."
        return ("*Chats*:\n\n" + "\n".join(lines))[:4000]

    def _cmd_peek(self, user_id, args):
        if not args:
            return "📋 Usage: /peek <chat_id>"
        try:
            target_id = int(args[0])
        except ValueError:
            return "❌ Chat ID must be a number."
        if target_id not in self.controller.chats:
            return f"❌ Chat {target_id} not found."
        chat = self.controller.chats[target_id]
        name = chat.get("name", f"Chat {target_id}")
        messages = chat.get("chat", [])
        if not messages:
            return f"📭 Chat \"{name}\" (ID: {target_id}) is empty."
        lines = [f"👀 *Peek at \"{name}\"* (ID: {target_id}, {len(messages)} messages):\n"]
        for msg in messages[-5:]:
            content = msg.get("Message", "")[:100]
            if len(msg.get("Message", "")) > 100:
                content += "..."
            lines.append(f"*{msg.get('User', 'Unknown')}*: {content.replace(chr(10), ' ')}")
        return "\n".join(lines)[:4000]

    def _cmd_resume(self, user_id, args):
        if not args:
            current_chat_id = self.controller.newelle_settings.chat_id
            current_name = self.controller.chats.get(current_chat_id, {}).get("name", "unknown")
            return (
                f"📌 Current chat: \"{current_name}\" (ID: {current_chat_id})\n"
                "Usage: /resume <chat_id>"
            )
        try:
            target_id = int(args[0])
        except ValueError:
            return "❌ Chat ID must be a number."
        if target_id not in self.controller.chats:
            return f"❌ Chat {target_id} not found."
        chat = self.controller.chats[target_id]
        if chat.get("call"):
            return f"❌ Chat {target_id} is a hidden call chat."
        self.controller.newelle_settings.chat_id = target_id
        self.controller.settings.set_int("chat", target_id)
        self._user_chats[user_id] = target_id
        self._save_last_chat_id(user_id, target_id)
        name = chat.get("name", f"Chat {target_id}")
        return f"✅ Resumed chat: \"{name}\" (ID: {target_id})"

    def _cmd_autoexec(self, user_id, args):
        current = self.controller.settings.get_boolean("auto-run")
        new_value = not current
        self.controller.settings.set_boolean("auto-run", new_value)
        self.controller.auto_run = new_value
        return f"⚡ Auto command execution {'enabled' if new_value else 'disabled'}"

    def _cmd_option(self, user_id, args):
        user_pending = [
            (iid, entry)
            for iid, entry in self._pending_interactions.items()
            if entry.get("user_id") == user_id
        ]
        if not args:
            if not user_pending:
                return "ℹ️ No pending interactions."
            iid, entry = user_pending[-1]
            opts = entry.get("options", [])
            lines = [f"🔢 Pending interaction for tool '{entry.get('tool_name', '?')}':"]
            lines += [f"  {i + 1}) {opt.title}" for i, opt in enumerate(opts)]
            lines.append("Use /option <n> to choose.")
            return "\n".join(lines)
        try:
            idx = int(args[0]) - 1  # user provides 1-based index
        except ValueError:
            return "❌ Usage: /option <number>"
        if not user_pending:
            return "ℹ️ No pending interactions."
        iid, _ = user_pending[-1]
        if self.resolve_pending_interaction(iid, idx):
            return f"✅ Option {idx + 1} selected."
        return f"❌ Invalid option index {idx + 1}."

    # map command name -> unbound method (populated after class body is complete)
    _BUILTIN_COMMANDS: dict = {
        "start": _cmd_start,
        "new": _cmd_new,
        "models": _cmd_models,
        "model": _cmd_model,
        "profile": _cmd_profile,
        "prompts": _cmd_prompts,
        "tools": _cmd_tools,
        "scheduled": _cmd_scheduled,
        "skill": _cmd_skill,
        "cd": _cmd_cd,
        "list_chats": _cmd_list_chats,
        "peek": _cmd_peek,
        "resume": _cmd_resume,
        "autoexec": _cmd_autoexec,
        "option": _cmd_option,
    }
