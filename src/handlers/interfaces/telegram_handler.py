import asyncio
import base64
import io
import json
import os
import queue
import random
import re
import subprocess
import tempfile
import threading
import uuid
from urllib.parse import unquote_to_bytes

from ...utility.strings import remove_thinking_blocks
from ...tools import ToolResult

from ...utility.pip import find_module

from ..extra_settings import ExtraSettings
from .chat_interface import ChatInterface


class TelegramInterface(ChatInterface):
    key = "telegram"
    name = "Telegram Bot"

    # ChatInterface folder/chat config
    folder_name = "Telegram"
    folder_color = "#3584e4"
    folder_icon = "folder-symbolic"
    chat_name_prefix = "✈️ Telegram"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._application = None
        self._loop = None
        self._thread = None
        self._running = False
        self._error = None
        self._recipients: dict[str, dict] = {}
        self._recipients_lock = threading.Lock()

    _MEDIA_BLOCK_RE = re.compile(
        r"```(image|video|file)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL
    )

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
                title=_("Allowed User IDs/Usernames"),
                description=_("Comma-separated Telegram user IDs or @usernames allowed to use the bot. Leave empty to allow everyone."),
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

    def _get_allowed_users(self) -> list[str]:
        """Return configured allowed users, accepting comma-separated entries."""
        return [
            item.strip()
            for item in re.split(r"[,;\n]", str(self._get_allowed_user() or ""))
            if item.strip()
        ]

    def _is_user_allowed(self, user) -> bool:
        allowed_users = self._get_allowed_users()
        if not allowed_users:
            return True
        for allowed in allowed_users:
            if allowed.startswith("@") and user.username == allowed[1:]:
                return True
            if str(user.id) == allowed:
                return True
        return False

    def set_setting(self, key, value):
        super().set_setting(key, value)
        if key == "allowed_user":
            # The configured recipients are part of the tool schema.
            self.notify_send_message_availability_changed()

    def _ensure_controller(self):
        return self.controller is not None

    def _run_async(self, coro):
        """Schedule a coroutine on the bot's event loop from a sync thread."""
        if self._loop is None or not self._loop.is_running():
            return
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _remember_recipient(self, user, chat_id):
        """Remember the current Telegram chat for an authorized user."""
        user_id = str(user.id)
        label = f"@{user.username}" if user.username else (user.full_name or user_id)
        recipient = {"chat_id": chat_id, "label": label}
        with self._recipients_lock:
            changed = self._recipients.get(user_id) != recipient
            self._recipients[user_id] = recipient
        if changed:
            # Recipient choices are part of the tool schema.
            self.notify_send_message_availability_changed()

    def _get_send_message_recipients(self) -> dict[str, dict]:
        """Combine configured allowed users with the chats Telegram has seen."""
        with self._recipients_lock:
            known_recipients = dict(self._recipients)

        allowed_users = self._get_allowed_users()
        if not allowed_users:
            return known_recipients

        recipients = {}
        for allowed in allowed_users:
            known = known_recipients.get(allowed)
            if known is None and allowed.startswith("@"):
                known = next(
                    (
                        recipient | {"user_id": user_id}
                        for user_id, recipient in known_recipients.items()
                        if recipient.get("label") == allowed
                    ),
                    None,
                )
            recipients[allowed] = known or {
                # Telegram private-chat IDs are the numeric user IDs. A
                # username can still be accepted by Telegram for supported
                # public destinations, while a user who later messages the
                # bot replaces this with their actual current chat ID.
                "chat_id": allowed,
                "label": allowed,
                "user_id": allowed,
            }
        return recipients

    def get_send_message_options_schema(self) -> dict:
        recipients = self._get_send_message_recipients()
        if len(recipients) == 1:
            return {}
        labels = [
            f"{recipient_id} ({recipient.get('label', recipient_id)})"
            for recipient_id, recipient in recipients.items()
        ]
        return {
            "recipient": {
                "type": "string",
                "enum": list(recipients),
                "description": (
                    "Authorized Telegram user to receive the message. Available: "
                    + (", ".join(labels) if labels else "none yet")
                ),
            },
        }

    def get_send_message_required_options(self) -> list[str]:
        return [] if len(self._get_send_message_recipients()) == 1 else ["recipient"]

    @staticmethod
    async def _send_text(bot, chat_id, text):
        """Send text while preserving Newelle's Markdown conversion behavior."""
        from telegramify_markdown import convert

        try:
            text_md, entities = convert(text)
            return await bot.send_message(
                chat_id=chat_id,
                text=text_md,
                entities=[entity.to_dict() for entity in entities],
            )
        except Exception:
            return await bot.send_message(chat_id=chat_id, text=text)

    @staticmethod
    def _media_input(source: str, kind: str):
        """Turn a local path or data URL into a Telegram upload value."""
        if not source.startswith("data:"):
            return source
        try:
            header, encoded = source.split(",", 1)
            mime_type = header[5:].split(";", 1)[0]
            payload = (
                base64.b64decode(encoded)
                if ";base64" in header.lower()
                else unquote_to_bytes(encoded)
            )
        except (ValueError, TypeError, base64.binascii.Error) as error:
            raise ValueError(f"Invalid {kind} data URL: {error}") from error
        extension = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "video/mp4": ".mp4", "video/webm": ".webm",
        }.get(mime_type, "")
        upload = io.BytesIO(payload)
        upload.name = f"newelle-{kind}{extension}"
        return upload

    def _message_text(self, message: str, *, streaming=False) -> str:
        """Return text content without Newelle media blocks.

        During streaming, also hide an incomplete media block. This keeps a
        partial `````image`` block from being rendered as ordinary text before
        the model finishes writing its path.
        """
        text = self._MEDIA_BLOCK_RE.sub("", message)
        if streaming:
            text = re.sub(r"```(?:image|video|file)\s*\n.*$", "", text, flags=re.I | re.S)
        return text.strip()

    def _has_media_blocks(self, message: str) -> bool:
        return self._MEDIA_BLOCK_RE.search(message) is not None

    async def _finalize_streamed_text(
        self,
        *,
        bot,
        chat_id,
        accumulated,
        use_edit_message,
        sent_message,
        last_rendered,
        send_message,
        edit_message,
    ):
        """Publish the completed stream as a persistent Telegram message."""
        visible_text = self._message_text(accumulated)

        # A Telegram draft is only a transient streaming preview. In draft
        # mode the completed text must always be delivered with send_message,
        # even when it is identical to the last draft. Edit mode already
        # operates on a persistent message, so an unchanged edit can be
        # skipped.
        if not visible_text or (
            use_edit_message and visible_text == last_rendered
        ):
            return sent_message, last_rendered

        chunks = [
            visible_text[i:i + 4000]
            for i in range(0, len(visible_text), 4000)
        ]
        if use_edit_message:
            if sent_message is not None:
                await edit_message(sent_message, chunks[0])
            else:
                sent_message = await send_message(bot, chat_id, chunks[0])
            for chunk in chunks[1:]:
                await send_message(bot, chat_id, chunk)
        else:
            for chunk in chunks:
                await send_message(bot, chat_id, chunk)

        return sent_message, visible_text

    async def _send_media_blocks(self, bot, chat_id, message):
        """Upload Newelle image, video, and file blocks as native Telegram media."""
        for match in self._MEDIA_BLOCK_RE.finditer(message):
            kind = match.group(1).lower()
            source = match.group(2).strip().splitlines()[0].strip() if match.group(2).strip() else ""
            if not source:
                raise ValueError(f"The {kind} block does not contain a file path or URL.")
            upload = self._media_input(source, kind)
            if kind == "image":
                await bot.send_photo(chat_id=chat_id, photo=upload)
            elif kind == "video":
                await bot.send_video(chat_id=chat_id, video=upload)
            else:
                await bot.send_document(chat_id=chat_id, document=upload)

    async def _send_outgoing_message(self, chat_id, message, bot=None):
        """Deliver text and Newelle media code blocks to a Telegram chat."""
        bot = bot or self._application.bot
        text = self._message_text(message)
        if text:
            for chunk_start in range(0, len(text), 4000):
                await self._send_text(bot, chat_id, text[chunk_start:chunk_start + 4000])
        await self._send_media_blocks(bot, chat_id, message)

    def send_message(self, message, extra_options):
        """Send a message to a known authorized Telegram recipient."""
        recipients = self._get_send_message_recipients()
        recipient_id = str(extra_options.get("recipient", ""))
        if not recipient_id and len(recipients) == 1:
            recipient_id = next(iter(recipients))
        recipient = recipients.get(recipient_id)
        result = ToolResult()
        if recipient is None:
            result.set_output(
                "Error: unknown Telegram recipient. Choose one of the configured "
                "allowed users."
            )
            return result
        if self._application is None or self._loop is None or not self._loop.is_running():
            result.set_output("Error: the Telegram interface is not ready to send messages.")
            return result

        try:
            future = self._run_async(self._send_outgoing_message(recipient["chat_id"], message))
            future.result(timeout=30)
            chat_id = self.get_or_create_chat(recipient.get("user_id", recipient_id))
            self.controller.chats[chat_id]["chat"].append({
                "User": "Assistant",
                "Message": message,
                "UUID": int(uuid.uuid4()),
                "Profile": self.controller.newelle_settings.current_profile,
            })
            self.controller.save_chats()
            result.set_output(f"Message sent to Telegram user {recipient_id}.")
        except Exception as error:
            result.set_output(f"Error sending Telegram message: {error}")
        return result

    # ------------------------------------------------------------------ #
    #       Tool-interaction hook: send Telegram inline keyboard           #
    # ------------------------------------------------------------------ #

    def handle_tool_interaction(self, user_id, tool_name, result, interaction_id):
        """Schedule an inline keyboard send on the Telegram event loop, then return.

        The LLM thread will block on ``result.get_output()`` immediately after
        this method returns, so the keyboard delivery races ahead of user input.
        """
        # We can only send if the Telegram loop is running.
        if self._loop is None or not self._loop.is_running():
            return
        options = self._pending_interactions.get(interaction_id, {}).get("options", [])
        if not options:
            return
        # Retrieve the Telegram chat id stored when the interaction was registered.
        tg_chat_id = self._pending_interactions.get(interaction_id, {}).get("tg_chat_id")
        bot = self._pending_interactions.get(interaction_id, {}).get("bot")
        if tg_chat_id is None or bot is None:
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        display = result.display_text or ""
        tool_text = f"🔧 **{tool_name}**"
        display_text = self._message_text(display)
        if display_text:
            tool_text += f"\n{display_text[:500]}"

        buttons = [
            [InlineKeyboardButton(opt.title, callback_data=f"tool_{interaction_id}_{i}")]
            for i, opt in enumerate(options)
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        async def _send():
            try:
                from telegramify_markdown import convert
                text_md, entities = convert(tool_text)
                entities = [e.to_dict() for e in entities]
                await bot.send_message(
                    chat_id=tg_chat_id, text=text_md,
                    entities=entities, reply_markup=reply_markup
                )
            except Exception:
                await bot.send_message(
                    chat_id=tg_chat_id, text=tool_text, reply_markup=reply_markup
                )
            await self._send_media_blocks(bot, tg_chat_id, display)

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        future.result(timeout=30)

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

        iface = self

        # ---- helpers -------------------------------------------------- #

        async def _safe_send_message(bot, chat_id, text, **kwargs):
            kwargs.pop("parse_mode", None)
            try:
                text_md, entities = convert(text)
                entities = [e.to_dict() for e in entities]
                return await bot.send_message(
                    chat_id=chat_id, text=text_md, entities=entities, **kwargs
                )
            except Exception as e:
                print(e)
                return await bot.send_message(chat_id=chat_id, text=text, **kwargs)

        async def _safe_edit_message(message, text, **kwargs):
            kwargs.pop("parse_mode", None)
            try:
                text_md, entities = convert(text)
                entities = [e.to_dict() for e in entities]
                return await message.edit_text(text=text_md, entities=entities, **kwargs)
            except Exception:
                return await message.edit_text(text=text, **kwargs)

        def _check(update: Update) -> bool:
            return iface._is_user_allowed(update.effective_user)

        # ---- command handlers (thin wrappers over ChatInterface) ------- #

        # Helper: run a built-in command and send the plain-text response
        async def _reply(update: Update, context, name: str, args):
            if not _check(update):
                return
            iface._remember_recipient(update.effective_user, update.effective_chat.id)
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return
            user_id = str(update.effective_user.id)
            resp = iface.run_command(name, user_id, list(args) if args else [])
            await _safe_send_message(context.bot, update.effective_chat.id, resp)

        async def start_cmd(update: Update, context):
            await _reply(update, context, "start", context.args)

        async def new_cmd(update: Update, context):
            await _reply(update, context, "new", context.args)

        async def models_cmd(update: Update, context):
            await _reply(update, context, "models", context.args)

        async def model_cmd(update: Update, context):
            await _reply(update, context, "model", context.args)

        async def profile_cmd(update: Update, context):
            await _reply(update, context, "profile", context.args)

        async def workspace_cmd(update: Update, context):
            await _reply(update, context, "workspace", context.args)

        async def workspaces_cmd(update: Update, context):
            await _reply(update, context, "workspaces", context.args)

        async def move_workspace_cmd(update: Update, context):
            await _reply(update, context, "move_workspace", context.args)

        async def mode_cmd(update: Update, context):
            await _reply(update, context, "mode", context.args)

        async def prompts_cmd(update: Update, context):
            await _reply(update, context, "prompts", context.args)

        async def tools_cmd(update: Update, context):
            await _reply(update, context, "tools", context.args)

        async def scheduled_cmd(update: Update, context):
            await _reply(update, context, "scheduled", context.args)

        async def skill_cmd(update: Update, context):
            await _reply(update, context, "skill", context.args)

        async def cd_cmd(update: Update, context):
            await _reply(update, context, "cd", context.args)

        async def list_chats_cmd(update: Update, context):
            await _reply(update, context, "list_chats", context.args)

        async def peek_cmd(update: Update, context):
            await _reply(update, context, "peek", context.args)

        async def resume_cmd(update: Update, context):
            await _reply(update, context, "resume", context.args)

        async def autoexec_cmd(update: Update, context):
            await _reply(update, context, "autoexec", context.args)

        # ---- text / voice / photo / callback handlers ----------------- #

        async def handle_text_message(update: Update, context):
            if not _check(update):
                return
            iface._remember_recipient(update.effective_user, update.effective_chat.id)
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return
            user_id = str(update.effective_user.id)
            text = update.message.text

            cmd_resp = iface.try_handle_command(user_id, text)
            if cmd_resp is not None:
                await _safe_send_message(context.bot, update.effective_chat.id, cmd_resp)
                return

            await _process_user_message(update, context, user_id, text)

        async def handle_voice_message(update: Update, context):
            if not _check(update):
                return
            iface._remember_recipient(update.effective_user, update.effective_chat.id)
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return
            user_id = str(update.effective_user.id)
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
                stt = iface.controller.handlers.stt
                if stt is None or not stt.is_installed():
                    await update.message.reply_text("🎤 STT engine not available.")
                    return
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
                await _process_user_message(update, context, user_id, transcribed)
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
            if not _check(update):
                return
            iface._remember_recipient(update.effective_user, update.effective_chat.id)
            if not iface._ensure_controller():
                await update.message.reply_text("⏳ Controller not ready.")
                return
            user_id = str(update.effective_user.id)
            photo = update.message.photo[-1]
            img_path = None
            try:
                photo_file = await photo.get_file()
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    await photo_file.download_to_drive(tmp.name)
                    img_path = tmp.name
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                caption = update.message.caption or "What do you see in this image?"
                user_text = f"```image\ndata:image/jpeg;base64,{img_data}\n```\n{caption}"
                await _process_user_message(update, context, user_id, user_text)
            except Exception as e:
                await update.message.reply_text(f"🖼️ Error processing image: {e}")
            finally:
                if img_path:
                    try:
                        os.unlink(img_path)
                    except OSError:
                        pass

        async def handle_callback_query(update: Update, context):
            """Handle inline keyboard button presses for tool interactions."""
            if not _check(update):
                return
            iface._remember_recipient(update.effective_user, update.effective_chat.id)
            query = update.callback_query
            data = query.data
            if not data.startswith("tool_"):
                return
            parts = data.split("_", 2)
            if len(parts) < 3:
                return
            interaction_id = parts[1]
            try:
                option_index = int(parts[2])
            except ValueError:
                return

            entry = iface._pending_interactions.get(interaction_id)
            if entry is None:
                await query.edit_message_text("⏰ This interaction has expired.")
                return
            if option_index < 0 or option_index >= len(entry.get("options", [])):
                return

            def _run_callback():
                try:
                    iface.resolve_pending_interaction(interaction_id, option_index)
                except Exception as e:
                    print(f"Tool callback error: {e}")

            threading.Thread(target=_run_callback, daemon=True).start()

        # ---- streaming message processor ------------------------------ #

        async def _process_user_message(update: Update, context, user_id: str, text: str):
            """Run text through process_message, deliver streaming updates to Telegram."""
            tg_chat_id = update.effective_chat.id
            use_edit_message = iface.get_setting(
                "use_edit_message", search_default=True, return_value=False
            )
            draft_id = random.randint(1, 1_000_000)

            # Single queue for all events from the LLM thread:
            # ("text", delta_str) | ("tool", event_dict) | ("done", err_or_None)
            event_q: queue.Queue = queue.Queue()

            def on_chunk(delta: str):
                event_q.put(("text", delta))

            def on_tool_event(event: dict):
                # Inject Telegram context into pending interaction so that
                # handle_tool_interaction can find bot + tg_chat_id.
                if event.get("type") == "tool_interaction":
                    iid = event.get("interaction_id")
                    if iid and iid in iface._pending_interactions:
                        iface._pending_interactions[iid]["tg_chat_id"] = tg_chat_id
                        iface._pending_interactions[iid]["bot"] = context.bot
                # For non-interactive tool results, show a short status line.
                if event.get("type") == "tool_result":
                    event_q.put(("tool", event))
                # tool_interaction is handled via handle_tool_interaction override

            def run():
                try:
                    iface.process_message(
                        user_id, text, on_chunk=on_chunk, on_tool_event=on_tool_event
                    )
                    event_q.put(("done", None))
                except Exception as e:
                    event_q.put(("done", str(e)))

            llm_thread = threading.Thread(target=run, daemon=True)
            llm_thread.start()

            accumulated = ""
            sent_message = None
            last_sent = ""
            last_rendered = ""
            done = False
            error = None

            async def _finalize_current_message():
                """Finish text delivery, then upload any completed media blocks."""
                nonlocal sent_message, last_rendered
                sent_message, last_rendered = await iface._finalize_streamed_text(
                    bot=context.bot,
                    chat_id=tg_chat_id,
                    accumulated=accumulated,
                    use_edit_message=use_edit_message,
                    sent_message=sent_message,
                    last_rendered=last_rendered,
                    send_message=_safe_send_message,
                    edit_message=_safe_edit_message,
                )
                if iface._has_media_blocks(accumulated):
                    await iface._send_media_blocks(context.bot, tg_chat_id, accumulated)

            while not done:
                await asyncio.sleep(0.3)

                # Drain all events queued since last poll
                while True:
                    try:
                        kind, data = event_q.get_nowait()
                    except queue.Empty:
                        break

                    if kind == "done":
                        done = True
                        error = data
                        break
                    elif kind == "text":
                        accumulated += data
                    elif kind == "tool":
                        # Non-interactive tool result: finalize current text, then
                        # show the tool status line, then reset for next segment.
                        if accumulated:
                            try:
                                await _finalize_current_message()
                                last_sent = accumulated
                            except Exception:
                                pass
                        sent_message = None
                        last_sent = ""
                        last_rendered = ""
                        draft_id = random.randint(1, 1_000_000)
                        accumulated = ""

                        tool_text = f"🔧 **{data['tool_name']}**"
                        if data.get("display_text"):
                            tool_text += f"\n{data['display_text']}"
                        await iface._send_outgoing_message(
                            tg_chat_id, tool_text, bot=context.bot
                        )

                # Update streaming display with latest accumulated text
                if not done and accumulated and accumulated != last_sent:
                    try:
                        text_to_send = iface._message_text(accumulated, streaming=True)[:4000]
                        if text_to_send:
                            if use_edit_message:
                                if sent_message is None:
                                    sent_message = await _safe_send_message(
                                        context.bot, tg_chat_id, text_to_send
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
                        last_sent = accumulated
                        last_rendered = text_to_send
                    except Exception:
                        pass

            llm_thread.join(timeout=5)

            if error:
                accumulated += f"\n\n❌ Error: {error}"
            if not accumulated:
                accumulated = "🤷 No response."

            await _finalize_current_message()

        # ---- Build application ---------------------------------------- #

        token = self._get_bot_token()
        if not token:
            raise ValueError("Bot token not configured")

        app = ApplicationBuilder().token(token).concurrent_updates(True).build()

        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("new", new_cmd))
        app.add_handler(CommandHandler("models", models_cmd))
        app.add_handler(CommandHandler("model", model_cmd))
        app.add_handler(CommandHandler("profile", profile_cmd))
        app.add_handler(CommandHandler("workspace", workspace_cmd))
        app.add_handler(CommandHandler("workspaces", workspaces_cmd))
        app.add_handler(CommandHandler("move_workspace", move_workspace_cmd))
        app.add_handler(CommandHandler("mode", mode_cmd))
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
                    self._write_state_file()
                    self.notify_send_message_availability_changed()
                    print("Telegram bot started")
                    while self._running:
                        self._loop.run_until_complete(asyncio.sleep(1))
                except Exception as e:
                    self._error = str(e)
                    print(f"Telegram bot error: {e}")
                finally:
                    self._clear_state_file()
                    try:
                        self._loop.run_until_complete(self._application.updater.stop())
                        self._loop.run_until_complete(self._application.stop())
                        self._loop.run_until_complete(self._application.shutdown())
                    except Exception:
                        pass
                    self._loop.close()
                    self._loop = None
                    self.notify_send_message_availability_changed()

            self._thread = threading.Thread(target=run_bot, daemon=True)
            self._thread.start()
        except Exception as e:
            self._error = str(e)
            print(f"Failed to start Telegram bot: {e}")

    def stop(self):
        self._clear_state_file()
        self._running = False
        if self._application is not None:
            try:
                if self._loop and self._loop.is_running():
                    for coro in [
                        self._application.updater.stop(),
                        self._application.stop(),
                        self._application.shutdown(),
                    ]:
                        try:
                            asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=5)
                        except Exception:
                            pass
            except Exception:
                pass
            self._application = None
        self._thread = None
        self.notify_send_message_availability_changed()
        print("Telegram bot stopped")

    def _is_locally_running(self):
        return self._running and self._thread is not None and self._thread.is_alive()
