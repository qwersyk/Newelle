import asyncio
import base64
import json
import os
import queue
import random
import subprocess
import tempfile
import threading
import uuid

from ...utility.strings import remove_thinking_blocks

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

    def _ensure_controller(self):
        return self.controller is not None

    def _run_async(self, coro):
        """Schedule a coroutine on the bot's event loop from a sync thread."""
        if self._loop is None or not self._loop.is_running():
            return
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

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
        if display:
            tool_text += f"\n{display[:500]}"

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
            done = False
            error = None

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
                        if accumulated and accumulated != last_sent:
                            try:
                                final = accumulated[:4000]
                                if use_edit_message and sent_message is not None:
                                    await _safe_edit_message(sent_message, final)
                                else:
                                    await _safe_send_message(context.bot, tg_chat_id, final)
                                last_sent = accumulated
                            except Exception:
                                pass
                        sent_message = None
                        last_sent = ""
                        draft_id = random.randint(1, 1_000_000)
                        accumulated = ""

                        tool_text = f"🔧 **{data['tool_name']}**"
                        if data.get("display_text"):
                            tool_text += f"\n{data['display_text'][:500]}"
                        await _safe_send_message(context.bot, tg_chat_id, tool_text)

                # Update streaming display with latest accumulated text
                if not done and accumulated and accumulated != last_sent:
                    try:
                        text_to_send = accumulated[:4000]
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
                    except Exception:
                        pass

            llm_thread.join(timeout=5)

            if error:
                accumulated += f"\n\n❌ Error: {error}"
            if not accumulated:
                accumulated = "🤷 No response."

            # Final send / edit
            if use_edit_message:
                if accumulated != last_sent:
                    if len(accumulated) <= 4000:
                        if sent_message is not None:
                            await _safe_edit_message(sent_message, accumulated)
                        else:
                            await _safe_send_message(context.bot, tg_chat_id, accumulated)
                    else:
                        chunks = [accumulated[i:i + 4000] for i in range(0, len(accumulated), 4000)]
                        if sent_message is not None:
                            await _safe_edit_message(sent_message, chunks[0])
                            for chunk in chunks[1:]:
                                await _safe_send_message(context.bot, tg_chat_id, chunk)
                        else:
                            for chunk in chunks:
                                await _safe_send_message(context.bot, tg_chat_id, chunk)
            else:
                if accumulated != last_sent:
                    if len(accumulated) > 4000:
                        for chunk in [accumulated[i:i + 4000] for i in range(0, len(accumulated), 4000)]:
                            await _safe_send_message(context.bot, tg_chat_id, chunk)
                    else:
                        await _safe_send_message(context.bot, tg_chat_id, accumulated)

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
        print("Telegram bot stopped")

    def _is_locally_running(self):
        return self._running and self._thread is not None and self._thread.is_alive()
