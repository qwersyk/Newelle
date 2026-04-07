import asyncio
import base64
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import uuid

from ...utility.strings import remove_thinking_blocks

from ...utility.pip import find_module

from ..extra_settings import ExtraSettings
from .interface import Interface


class TelegramInterface(Interface):
    key = "telegram"
    name = "Telegram Bot"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._application = None
        self._loop = None
        self._thread = None
        self._running = False
        self._error = None
        self._user_chats = {}  # user_id -> chat_id mapping
        self._pending_interactions = {}  # interaction_id -> {options, event}
        self._telegram_folder_id = None
        self._chat_counter = 0

    @staticmethod
    def get_extra_requirements() -> list:
        return ["python-telegram-bot", "telegramify-markdown[mermaid]"]
    def is_installed(self) -> bool:
        return find_module("telegram") is not None and find_module("telegramify_markdown") is not None    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting(
                key="bot_token",
                title=_("Bot Token"),
                description=_("Necessary: Telegram bot token obtained from @BotFather"),
                default="",
                password=True
            ),
            ExtraSettings.EntrySetting(
                key="allowed_user",
                title=_("Allowed User ID/Username"),
                description=_("Telegram user ID or @username allowed to use the bot. Leave empty to allow everyone."),
                default="",
            ),
            ExtraSettings.ToggleSetting(
                key="use_edit_message",
                title=_("Use Edit Message for Streaming"),
                description=_("Use edit_message_text instead of send_message_draft for streaming responses. Sends a regular message first, then edits it in place."),
                default=False,
            ),
        ]

    def _get_bot_token(self):
        return self.get_setting("bot_token", search_default=True, return_value="")

    def _get_allowed_user(self):
        return self.get_setting("allowed_user", search_default=True, return_value="")

    def _is_user_allowed(self, user) -> bool:
        allowed = self._get_allowed_user().strip()
        if not allowed:
            return True
        if allowed.startswith("@"):
            return user.username == allowed[1:]
        try:
            return str(user.id) == allowed
        except (ValueError, TypeError):
            return False

    def _ensure_telegram_folder(self) -> int:
        if self._telegram_folder_id is not None:
            return self._telegram_folder_id
        for fid, folder in self.controller.folders.items():
            if folder.get("name") == "Telegram":
                self._telegram_folder_id = fid
                self._count_existing_chats()
                return self._telegram_folder_id
        self._telegram_folder_id = self.controller.create_folder(
            name="Telegram", color="#3584e4", icon="folder-symbolic"
        )
        self._count_existing_chats()
        return self._telegram_folder_id

    def _count_existing_chats(self) -> int:
        folder_id = self._telegram_folder_id
        if folder_id is None or folder_id not in self.controller.folders:
            self._chat_counter = 0
            return 0
        chat_ids = self.controller.folders[folder_id].get("chat_ids", [])
        count = 0
        for cid in chat_ids:
            if cid in self.controller.chats:
                name = self.controller.chats[cid].get("name", "")
                if name.startswith("✈️ Telegram "):
                    try:
                        num = int(name.split()[-1])
                        count = max(count, num)
                    except (ValueError, IndexError):
                        pass
        self._chat_counter = count
        return count

    def _save_last_chat_id(self, user_id: int, chat_id: int):
        self.set_setting(f"last_chat_user_{user_id}", chat_id)

    def _load_last_chat_id(self, user_id: int) -> int | None:
        val = self.get_setting(f"last_chat_user_{user_id}", search_default=True, return_value=None)
        return val

    def _get_or_create_chat(self, user_id: int) -> int:
        if user_id in self._user_chats:
            chat_id = self._user_chats[user_id]
            if chat_id in self.controller.chats:
                return chat_id

        last_chat_id = self._load_last_chat_id(user_id)
        if last_chat_id is not None and last_chat_id in self.controller.chats:
            self._user_chats[user_id] = last_chat_id
            return last_chat_id

        folder_id = self._ensure_telegram_folder()
        self._chat_counter += 1
        chat_name = f"✈️ Telegram {self._chat_counter}"
        chat_id = self.controller.create_visible_chat(name=chat_name, folder_id=folder_id)
        self._user_chats[user_id] = chat_id
        self._save_last_chat_id(user_id, chat_id)
        self.controller.save_chats()
        return chat_id

    def _ensure_controller(self):
        return self.controller is not None

    def _run_async(self, coro):
        """Schedule a coroutine on the bot's event loop from a sync thread."""
        if self._loop is None or not self._loop.is_running():
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future

    # ------------------------------------------------------------------ #
    #                        Bot app builder                              #
    # ------------------------------------------------------------------ #
    def _create_bot_app(self):
        from telegramify_markdown import convert
        from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            CallbackQueryHandler,
            filters,
        )

        controller = self.controller
        iface = self

        async def start_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                await update.message.reply_text("🚫 You are not authorized to use this bot.")
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready yet. Please try again later.")
                return
            user_id = update.effective_user.id
            chat_id = iface._get_or_create_chat(user_id)
            chat_name = controller.chats[chat_id].get("name", "Telegram")
            await update.message.reply_text(
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
            )

        async def new_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return
            user_id = update.effective_user.id
            if context.args:
                name = " ".join(context.args)
                folder_id = iface._ensure_telegram_folder()
                chat_id = controller.create_visible_chat(name=name, folder_id=folder_id)
            else:
                folder_id = iface._ensure_telegram_folder()
                iface._chat_counter += 1
                name = f"✈️ Telegram {iface._chat_counter}"
                chat_id = controller.create_visible_chat(name=name, folder_id=folder_id)
                controller.save_chats()
            iface._user_chats[user_id] = chat_id
            iface._save_last_chat_id(user_id, chat_id)
            await update.message.reply_text(f"🆕 New chat created: \"{name}\" (ID: {chat_id})")

        async def models_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            from ...constants import AVAILABLE_LLMS

            current_provider = controller.newelle_settings.language_model
            llm_settings = json.loads(controller.settings.get_string("llm-settings"))

            lines = []
            for provider_name, provider_info in AVAILABLE_LLMS.items():
                is_current = provider_name == current_provider
                marker = "▶" if is_current else " "
                lines.append(f"{marker} *{provider_info.get('title', provider_name)}*")

                try:
                    handler_class = provider_info["class"]
                    handler = handler_class(controller.settings, controller.handlers.directory)
                    models = handler.get_models_list() if hasattr(handler, "get_models_list") else ()
                    if models:
                        limited_models = list(models)[:40]
                        for m in limited_models:
                            model_id = m[0]
                            model_name = m[1] if len(m) > 1 else m[0]
                            current_model = llm_settings.get(provider_name, {}).get("model", "")
                            model_marker = "  └ " + ("✅ " if (is_current and model_id == current_model) else "   ") + model_name
                            lines.append(model_marker)
                        if len(models) > 40:
                            lines.append(f"  └ _... and {len(models) - 40} more_")
                    else:
                        lines.append("  └ _No models available_")
                except Exception as e:
                    lines.append(f"  └ _Error loading models_")

            text = "🤖 *Available Models:*\n\n" + "\n".join(lines)
            text += "\n\n💡 Use /model [provider:]<model> to switch"
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text, parse_mode="markdown")

        async def model_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            if not context.args:
                llm = controller.handlers.llm
                provider = controller.newelle_settings.language_model
                model = llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default"
                await update.message.reply_text(f"🤖 Current: {provider}:{model}")
                return

            arg = " ".join(context.args)
            from ...constants import AVAILABLE_LLMS

            if ":" in arg:
                provider_name, model = arg.split(":", 1)
                if provider_name not in AVAILABLE_LLMS:
                    providers = ", ".join(AVAILABLE_LLMS.keys())
                    await update.message.reply_text(f"❓ Unknown provider '{provider_name}'. Available: {providers}")
                    return
                controller.settings.set_string("language-model", provider_name)
                llm_settings = json.loads(controller.settings.get_string("llm-settings"))
                if provider_name not in llm_settings:
                    llm_settings[provider_name] = {}
                llm_settings[provider_name]["model"] = model
                controller.settings.set_string("llm-settings", json.dumps(llm_settings))
                controller.update_settings()
                await update.message.reply_text(f"✅ Switched to {provider_name}:{model}")
            else:
                provider_name = controller.newelle_settings.language_model
                llm_settings = json.loads(controller.settings.get_string("llm-settings"))
                if provider_name not in llm_settings:
                    llm_settings[provider_name] = {}
                llm_settings[provider_name]["model"] = arg
                controller.settings.set_string("llm-settings", json.dumps(llm_settings))
                controller.update_settings()
                await update.message.reply_text(f"✅ Switched model to {provider_name}:{arg}")

        async def profile_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            profiles = controller.newelle_settings.profile_settings
            if not context.args:
                current = controller.newelle_settings.current_profile
                names = ", ".join(profiles.keys())
                await update.message.reply_text(f"👤 Current profile: {current}\nAvailable: {names}")
                return

            name = " ".join(context.args)
            if name not in profiles:
                await update.message.reply_text(
                    f"❌ Profile '{name}' not found. Available: {', '.join(profiles.keys())}"
                )
                return

            from ...utility.profile_settings import restore_settings_from_dict
            controller.update_current_profile()
            controller.settings.set_string("current-profile", name)
            profile_data = profiles[name]
            groups = profile_data.get("settings_groups", [])
            if groups:
                saved = profile_data.get("settings", {})
                restore_settings_from_dict(controller.settings, saved)
            controller.update_settings()
            await update.message.reply_text(f"✅ Switched to profile: {name}")

        async def prompts_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            from ...constants import PROMPTS, AVAILABLE_PROMPTS
            ns = controller.newelle_settings
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
                editable = prompt.get("editable", False)
                lines.append(f"[{status}] {key}" + (" (editable)" if editable else ""))
                lines.append(f"    {short}")

            if not lines:
                await update.message.reply_text("📝 No prompts available.")
                return

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text)

        async def tools_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            all_tools = controller.tools.get_all_tools()
            tools_settings = {}
            if hasattr(controller, "newelle_settings"):
                tools_settings = controller.newelle_settings.tools_settings_dict

            if context.args and context.args[0] == "toggle":
                tool_name = " ".join(context.args[1:]) if len(context.args) > 1 else None
                if not tool_name:
                    await update.message.reply_text("📋 Usage: /tools toggle <tool_name>")
                    return
                target = None
                for t in all_tools:
                    if t.name.lower() == tool_name.lower():
                        target = t
                        break
                if target is None:
                    await update.message.reply_text(f"❓ Tool '{tool_name}' not found.")
                    return
                is_enabled = target.default_on
                if target.name in tools_settings and "enabled" in tools_settings[target.name]:
                    is_enabled = tools_settings[target.name]["enabled"]
                ts = controller.newelle_settings.tools_settings_dict
                if target.name not in ts:
                    ts[target.name] = {}
                ts[target.name]["enabled"] = not is_enabled
                controller.newelle_settings.tools_settings_dict = ts
                controller.settings.set_string("tools-settings", json.dumps(ts))
                new_status = "enabled" if not is_enabled else "disabled"
                await update.message.reply_text(f"🔧 Tool '{target.name}' {new_status}.")
                return

            lines = []
            for tool in all_tools:
                is_enabled = tool.default_on
                if tool.name in tools_settings and "enabled" in tools_settings[tool.name]:
                    is_enabled = tools_settings[tool.name]["enabled"]
                status = "ON" if is_enabled else "OFF"
                lines.append(f"[{status}] {tool.name} - {tool.description[:60]}")

            if not lines:
                await update.message.reply_text("🔧 No tools available.")
                return

            text = "\n".join(lines)
            text += "\n\n💡 Use /tools toggle <name> to toggle a tool."
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text)

        async def scheduled_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            tasks = controller.get_scheduled_tasks()
            if not tasks:
                await update.message.reply_text("⏰ No scheduled tasks.")
                return

            lines = []
            for task in tasks:
                status = "RUNNING" if task.get("running") else ("ENABLED" if task.get("enabled") else "DISABLED")
                schedule = task.get("cron") or task.get("run_at") or "manual"
                next_run = task.get("next_run_at", "N/A")
                lines.append(
                    f"[{status}] {task['task'][:60]}\n"
                    f"    ID: {task['id'][:8]}... | Schedule: {schedule}\n"
                    f"    Next: {next_run}"
                )

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text)

        async def skill_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            if not context.args:
                commands = controller.get_commands()
                skill_commands = [c for c in commands if c.name.startswith("skill")]
                if not skill_commands:
                    await update.message.reply_text("⚡ No skills available.")
                    return
                lines = [f"/skill {c.name} - {c.description}" for c in skill_commands]
                await update.message.reply_text("\n".join(lines))
                return

            skill_name = context.args[0]

            commands = controller.get_commands()
            cmd = None
            for c in commands:
                if c.name == f"skill {skill_name}" or c.name == skill_name:
                    cmd = c
                    break
            if cmd is None:
                await update.message.reply_text(f"❌ Command/skill '{skill_name}' not found.")
                return

            try:
                result = cmd.execute()
                output = str(result) if result is not None else "Done."
                if hasattr(result, "get_output"):
                    output = str(result.get_output())
                if len(output) > 4000:
                    output = output[:4000] + "..."
                await update.message.reply_text(output)
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {e}")

        async def cd_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            if not context.args:
                current = controller.settings.get_string("path")
                await update.message.reply_text(f"📂 Current path: {current}")
                return

            new_path = " ".join(context.args)
            expanded = os.path.expanduser(new_path)
            if not os.path.isdir(expanded):
                await update.message.reply_text(f"❌ Directory not found: {new_path}")
                return

            controller.settings.set_string("path", os.path.normpath(new_path))
            controller.update_settings()
            os.chdir(expanded)
            await update.message.reply_text(f"✅ Path changed to: {os.path.normpath(new_path)}")

        async def list_chats_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            current_chat_id = controller.newelle_settings.chat_id
            chat_ids = controller.chat_ids_ordered()
            if controller.newelle_settings.reverse_order:
                chat_ids = list(reversed(chat_ids))
            lines = []
            for cid in chat_ids:
                chat = controller.chats[cid]
                if chat.get("call"):
                    continue
                marker = "▶" if cid == current_chat_id else " "
                name = chat.get("name", f"Chat {cid}")
                msg_count = len(chat.get("chat", []))
                lines.append(f"{marker} {cid}. {name} ({msg_count} messages)")

            if not lines:
                await update.message.reply_text("📭 No chats available.")
                return


            lines = [x[:40] for x in lines]
            text = "📋 *Chats*:\n\n" + "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text, parse_mode="markdown")

        async def peek_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            if not context.args:
                await update.message.reply_text("📋 Usage: /peek <chat_id>")
                return

            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Chat ID must be a number.")
                return

            if target_id not in controller.chats:
                await update.message.reply_text(f"❌ Chat {target_id} not found.")
                return

            chat = controller.chats[target_id]
            name = chat.get("name", f"Chat {target_id}")
            messages = chat.get("chat", [])
            peek_count = min(5, len(messages))
            if not messages:
                await update.message.reply_text(f"📭 Chat \"{name}\" (ID: {target_id}) is empty.")
                return

            lines = [f"👀 *Peek at \"{name}\"* (ID: {target_id}, {len(messages)} messages):\n"]
            for msg in messages[-peek_count:]:
                role = msg.get("User", "Unknown")
                content = msg.get("Message", "")[:100]
                if len(msg.get("Message", "")) > 100:
                    content += "..."
                content = content.replace("\n", " ")
                lines.append(f"*{role}*: {content}")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await update.message.reply_text(text, parse_mode="markdown")

        async def resume_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            if not context.args:
                current_chat_id = controller.newelle_settings.chat_id
                current_name = controller.chats.get(current_chat_id, {}).get("name", "unknown")
                await update.message.reply_text(f"📌 Current chat: \"{current_name}\" (ID: {current_chat_id})\nUsage: /resume <chat_id>")
                return

            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Chat ID must be a number.")
                return

            if target_id not in controller.chats:
                await update.message.reply_text(f"❌ Chat {target_id} not found.")
                return

            chat = controller.chats[target_id]
            if chat.get("call"):
                await update.message.reply_text(f"❌ Chat {target_id} is a hidden call chat.")
                return

            controller.newelle_settings.chat_id = target_id
            controller.settings.set_int("chat", target_id)
            name = chat.get("name", f"Chat {target_id}")
            await update.message.reply_text(f"✅ Resumed chat: \"{name}\" (ID: {target_id})")

        async def autoexec_cmd(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            current = controller.settings.get_boolean("auto-run")
            new_value = not current
            controller.settings.set_boolean("auto-run", new_value)
            controller.auto_run = new_value
            status = "enabled" if new_value else "disabled"

            await update.message.reply_text(f"⚡ Auto command execution {status}")

        async def handle_text_message(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            user_id = update.effective_user.id
            text = update.message.text

            # Check if it's an unrecognized command that matches a registered command
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                if parts:
                    cmd_name = parts[0]
                    commands = controller.get_commands()
                    for cmd in commands:
                        if cmd.name == cmd_name or cmd.name == f"skill {cmd_name}":
                            try:
                                result = cmd.execute()
                                output = str(result) if result is not None else "Done."
                                if hasattr(result, "get_output"):
                                    output = str(result.get_output())
                                if len(output) > 4000:
                                    output = output[:4000] + "..."
                                await update.message.reply_text(output)
                            except Exception as e:
                                await update.message.reply_text(f"❌ Error: {e}")
                            return

            await _process_user_message(iface, update, context, user_id, text)

        async def handle_voice_message(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            user_id = update.effective_user.id
            voice = update.message.voice or update.message.audio
            if voice is None:
                await update.message.reply_text("🎤 Could not process audio.")
                return

            try:
                voice_file = await voice.get_file()
            except Exception as e:
                await update.message.reply_text(f"🎤 Failed to download voice: {e}")
                return

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await voice_file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            try:
                stt = controller.handlers.stt
                if stt is None or not stt.is_installed():
                    await update.message.reply_text("🎤 STT engine not available.")
                    return

                # Convert ogg to wav if possible
                wav_path = tmp_path
                if tmp_path.endswith(".ogg"):
                    wav_path = tmp_path.replace(".ogg", ".wav")
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", tmp_path, wav_path],
                            capture_output=True, timeout=30
                        )
                    except (FileNotFoundError, Exception):
                        wav_path = tmp_path

                transcribed = stt.recognize_file(wav_path)
                if not transcribed:
                    await update.message.reply_text("🎤 Could not transcribe the voice message.")
                    return

                await update.message.reply_text(f"🎤 You said: {transcribed}")
                await _process_user_message(iface, update, context, user_id, transcribed)
            except Exception as e:
                await update.message.reply_text(f"🎤 Transcription error: {e}")
            finally:
                for path in (tmp_path, wav_path if wav_path != tmp_path else None):
                    if path:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass

        async def handle_photo_message(update: Update, context):
            if not iface._is_user_allowed(update.effective_user):
                return
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return

            user_id = update.effective_user.id
            photo = update.message.photo[-1]  # Get largest photo

            try:
                photo_file = await photo.get_file()
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    await photo_file.download_to_drive(tmp.name)
                    img_path = tmp.name

                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                image_str = f"data:image/jpeg;base64,{img_data}"

                caption = update.message.caption or "What do you see in this image?"
                user_text = f"```image\n{image_str}\n```\n{caption}"

                await _process_user_message(iface, update, context, user_id, user_text)
            except Exception as e:
                await update.message.reply_text(f"🖼️ Error processing image: {e}")
            finally:
                try:
                    os.unlink(img_path)
                except (OSError, NameError):
                    pass

        async def handle_callback_query(update: Update, context):
            """Handle inline keyboard button presses for tool interactions."""
            query = update.callback_query

            data = query.data
            if not data.startswith("tool_"):
                print(f"[TG] Callback ignored: not a tool callback")
                return

            parts = data.split("_", 2)
            if len(parts) < 3:
                print(f"[TG] Callback ignored: bad format, parts={parts}")
                return

            interaction_id = parts[1]
            try:
                option_index = int(parts[2])
            except ValueError:
                print(f"[TG] Callback ignored: bad option index")
                return

            print(f"[TG] Tool callback: interaction_id={interaction_id}, index={option_index}")
            print(f"[TG] Pending interactions: {list(iface._pending_interactions.keys())}")
            entry = iface._pending_interactions.get(interaction_id)
            if entry is None:
                print(f"[TG] Interaction not found: {interaction_id}")
                await query.edit_message_text("⏰ This interaction has expired.")
                return

            if option_index < 0 or option_index >= len(entry["options"]):
                print(f"[TG] Option index out of range: {option_index}")
                return

            print(f"[TG] Calling callback for option: {entry['options'][option_index].title}")
            selected = entry["options"][option_index]
            iface._pending_interactions.pop(interaction_id, None)

            def _run_callback():
                try:
                    selected.callback()
                except Exception as e:
                    print(f"Tool callback error: {e}")

            t = threading.Thread(target=_run_callback, daemon=True)
            t.start()
            #await query.edit_message_text(f"Selected: {selected.title}")

        async def _safe_send_message(bot, chat_id, text, **kwargs):
            """Send a message, falling back from markdown to plain text on parse errors."""
            kwargs.pop("parse_mode", None)
            try:
                text, entities = convert(text)
                entities = [e.to_dict() for e in entities]
                return await bot.send_message(chat_id=chat_id, text=text, entities=entities, **kwargs)
            except Exception as e:
                print(e)
                return await bot.send_message(chat_id=chat_id, text=text, **kwargs)

        async def _safe_edit_message(message, text, **kwargs):
            """Edit a message, falling back from markdown to plain text on parse errors."""
            kwargs.pop("parse_mode", None)
            try:
                text, entities = convert(text)
                entities = [e.to_dict() for e in entities]
                return await message.edit_text(text=text, entities=entities, **kwargs)
            except Exception:
                return await message.edit_text(text=text, **kwargs)

        def _strip_thinking(text: str) -> tuple[str, bool]:
            """Remove thinking/reflect blocks from text. Returns (cleaned_text, is_thinking)."""
            # Remove complete <think>...</think> blocks
            cleaned = remove_thinking_blocks(text)  
            # Check if there's an unclosed <think> tag (still in thinking mode)
            is_thinking = '<think>' in cleaned
            
            # If still thinking, remove everything from <think> onwards
            if is_thinking:
                cleaned = ""
            
            return cleaned.strip(), is_thinking

        async def _process_user_message(iface, update, context, user_id, text):
            """Process a user message through the LLM with streaming.
            
            Message flow: LLM response → Tool result → LLM response → Tool result
            Each segment is sent as a separate consecutive message.
            """
            chat_id = iface._get_or_create_chat(user_id)
            tg_chat_id = update.effective_chat.id
            use_edit_message = iface.get_setting("use_edit_message", search_default=True, return_value=False)

            draft_id = random.randint(1, 1000000)

            state = {
                "accumulated": "",
                "last_cumulative": "",
                "error": None,
                "done": False,
                "is_thinking": False,
                "pending_tool": None,
                "tool_result_ready": threading.Event(),
                "tool_message_sent": threading.Event(),
            }
            state_lock = threading.Lock()

            def on_stream(stream_text: str):
                with state_lock:
                    if state["pending_tool"] is not None:
                        return
                    cleaned, was_thinking = _strip_thinking(stream_text)
                    if stream_text.startswith(state["last_cumulative"]):
                        delta = stream_text[len(state["last_cumulative"]):]
                    else:
                        delta = stream_text
                    state["last_cumulative"] = stream_text
                    if not was_thinking and state["is_thinking"]:
                        state["is_thinking"] = False
                        delta = cleaned
                    elif was_thinking:
                        state["is_thinking"] = True
                    if not was_thinking:
                        state["accumulated"] += delta

            def on_tool_result(tool_name: str, result):
                display = result.display_text or ""
                
                with state_lock:
                    state["pending_tool"] = {
                        "name": tool_name,
                        "display": display,
                        "requires_interaction": result.requires_interaction,
                        "interaction_options": result.interaction_options if result.requires_interaction else [],
                    }

                state["tool_result_ready"].set()
                state["tool_message_sent"].wait(timeout=30)
                state["tool_message_sent"].clear()

                with state_lock:
                    state["pending_tool"] = None

            def run_llm():
                try:
                    controller.run_llm_with_tools(
                        message=text,
                        chat_id=chat_id,
                        on_message_callback=on_stream,
                        on_tool_result_callback=on_tool_result,
                        save_chat=True,
                        force_tools_on_main_thread=True,
                    )
                except Exception as e:
                    with state_lock:
                        state["error"] = str(e)
                finally:
                    state["done"] = True

            llm_thread = threading.Thread(target=run_llm, daemon=True)
            llm_thread.start()

            last_sent_text = ""
            sent_message = None

            while not state["done"]:
                await asyncio.sleep(0.3)

                with state_lock:
                    pending_tool = state.get("pending_tool")

                if pending_tool and state["tool_result_ready"].is_set():
                    state["tool_result_ready"].clear()

                    if use_edit_message and sent_message is not None and state["accumulated"]:
                        try:
                            final = state["accumulated"][:4000]
                            await sent_message.edit_text(text=final, parse_mode="markdown")
                        except Exception:
                            pass

                    sent_message = None
                    last_sent_text = ""
                    draft_id = random.randint(1, 1000000)
                    with state_lock:
                        state["accumulated"] = ""
                        state["last_cumulative"] = ""
                        state["is_thinking"] = False

                    tool_text = f"🔧 **{pending_tool['name']}**"
                    if pending_tool["display"]:
                        tool_text += f"\n{pending_tool['display'][:500]}"

                    if len(pending_tool["interaction_options"]) > 0:
                        interaction_id = str(uuid.uuid4())[:8]
                        iface._pending_interactions[interaction_id] = {
                            "options": pending_tool["interaction_options"],
                        }

                        buttons = []
                        for i, opt in enumerate(pending_tool["interaction_options"]):
                            buttons.append(
                                [InlineKeyboardButton(opt.title, callback_data=f"tool_{interaction_id}_{i}")]
                            )
                        reply_markup = InlineKeyboardMarkup(buttons)

                        await _safe_send_message(
                            context.bot, tg_chat_id, tool_text,
                            reply_markup=reply_markup,
                        )
                    else:
                        await _safe_send_message(
                            context.bot, tg_chat_id, tool_text,
                        )

                    with state_lock:
                        state["pending_tool"] = None
                    state["tool_message_sent"].set()
                    continue

                with state_lock:
                    current = state["accumulated"]
                    is_thinking = state["is_thinking"] and not current

                display = current
                if not display and is_thinking:
                    display = "💭 Thinking..."

                if display and display != last_sent_text:
                    try:
                        text_to_send = display[:4000] if len(display) > 4000 else display
                        if use_edit_message:
                            if sent_message is None:
                                sent_message = await _safe_send_message(
                                    context.bot, tg_chat_id, text_to_send,
                                )
                            else:
                                await _safe_edit_message(sent_message, text_to_send)
                        else:
                            await context.bot.send_message_draft(
                                chat_id=tg_chat_id,
                                draft_id=draft_id,
                                text=text_to_send,
                                parse_mode="markdown"
                            )
                        last_sent_text = display
                    except Exception:
                        pass

            llm_thread.join(timeout=5)

            with state_lock:
                final_text = state["accumulated"]
                error = state["error"]

            if error:
                final_text += f"\n\n❌ Error: {error}"

            if not final_text:
                final_text = "🤷 No response."

            if use_edit_message:
                if sent_message is not None and final_text:
                    if len(final_text) <= 4000:
                        await _safe_edit_message(sent_message, final_text)
                    else:
                        chunks = [final_text[i:i + 4000] for i in range(0, len(final_text), 4000)]
                        await _safe_edit_message(sent_message, chunks[0])
                        for chunk in chunks[1:]:
                            await _safe_send_message(context.bot, tg_chat_id, chunk)
                elif not sent_message and final_text:
                    if len(final_text) > 4000:
                        chunks = [final_text[i:i + 4000] for i in range(0, len(final_text), 4000)]
                        for chunk in chunks:
                            await _safe_send_message(context.bot, tg_chat_id, chunk)
                    else:
                        await _safe_send_message(context.bot, tg_chat_id, final_text)
            else:
                if len(final_text) > 4000:
                    chunks = [final_text[i:i + 4000] for i in range(0, len(final_text), 4000)]
                    for chunk in chunks:
                        await _safe_send_message(context.bot, tg_chat_id, chunk)
                else:
                    await _safe_send_message(context.bot, tg_chat_id, final_text)

        # Build application
        token = self._get_bot_token()
        if not token:
            raise ValueError("Bot token not configured")

        app = ApplicationBuilder().token(token).concurrent_updates(True).build()

        # Register handlers
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("new", new_cmd))
        app.add_handler(CommandHandler("models", models_cmd))
        app.add_handler(CommandHandler("model", model_cmd))
        app.add_handler(CommandHandler("profile", profile_cmd))
        app.add_handler(CommandHandler("prompts", prompts_cmd))
        app.add_handler(CommandHandler("tools", tools_cmd))
        app.add_handler(CommandHandler("scheduled", scheduled_cmd))
        app.add_handler(CommandHandler("skill", skill_cmd))
        app.add_handler(CommandHandler("cd", cd_cmd))
        app.add_handler(CommandHandler("list_chats", list_chats_cmd))
        app.add_handler(CommandHandler("peek", peek_cmd))
        app.add_handler(CommandHandler("resume", resume_cmd))
        app.add_handler(CommandHandler("autoexec", autoexec_cmd))
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

        return app

    # ------------------------------------------------------------------ #
    #                      Lifecycle methods                              #
    # ------------------------------------------------------------------ #
    def start(self):
        if self.controller is None:
            return
        if not self.is_installed():
            self._error = "python-telegram-bot not installed"
            print("Cannot start Telegram bot: dependencies not installed")
            return

        self._error = None
        try:
            self._application = self._create_bot_app()
            self._running = True

            def run_bot():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                try:
                    self._loop.run_until_complete(self._application.initialize())
                    self._loop.run_until_complete(self._application.start())
                    self._loop.run_until_complete(self._application.updater.start_polling())
                    print("Telegram bot started")
                    while self._running:
                        self._loop.run_until_complete(asyncio.sleep(1))
                except Exception as e:
                    self._error = str(e)
                    print(f"Telegram bot error: {e}")
                finally:
                    try:
                        self._loop.run_until_complete(self._application.updater.stop())
                        self._loop.run_until_complete(self._application.stop())
                        self._loop.run_until_complete(self._application.shutdown())
                    except Exception:
                        pass
                    self._loop.close()
                    self._loop = None

            self._thread = threading.Thread(target=run_bot, daemon=True)
            self._thread.start()
        except Exception as e:
            self._error = str(e)
            print(f"Failed to start Telegram bot: {e}")

    def stop(self):
        self._running = False
        if self._application is not None:
            try:
                if self._loop and self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._application.updater.stop(), self._loop
                    )
                    future.result(timeout=5)
                    future = asyncio.run_coroutine_threadsafe(
                        self._application.stop(), self._loop
                    )
                    future.result(timeout=5)
                    future = asyncio.run_coroutine_threadsafe(
                        self._application.shutdown(), self._loop
                    )
                    future.result(timeout=5)
            except Exception:
                pass
            self._application = None
        self._thread = None
        print("Telegram bot stopped")

    def is_running(self):
        return self._running and self._thread is not None and self._thread.is_alive()


