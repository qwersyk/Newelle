"""
ChatTab - A self-contained chat tab widget for multi-tab parallel chat support.

Each ChatTab owns its own:
- ChatHistory instance
- Input box (attach, record, text entry, send button)
- Streaming state (stream_number, status, streaming_lock, etc.)
- Message sending and generation lifecycle
"""

from gi.repository import Gtk, Adw, Gio, Gdk, GObject, GLib, GdkPixbuf
import threading
import time
import re
import gettext
import subprocess
import base64

from .chat_history import ChatHistory
from .multiline import MultilineEntry
from .documents_reader import DocumentReaderWidget
from .message import Message
from ...utility.strings import (
    convert_think_codeblocks,
    remove_markdown,
    remove_emoji,
)
from ...utility.system import is_flatpak
from ...utility.media import extract_supported_files

_ = gettext.gettext


class ChatTab(Gtk.Box):
    """A self-contained chat tab with its own chat history, input, and streaming state."""

    __gsignals__ = {
        "chat-name-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "generation-started": (GObject.SignalFlags.RUN_LAST, None, ()),
        "generation-stopped": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, window, chat_id: int):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        
        self.window = window
        self._chat_id = chat_id
        self.controller = window.controller
        self.tab_page = None  # Will be set after tab is added to TabView
        
        # Streaming state - isolated per tab
        self.stream_number_variable = 0
        self.stream_tools = False
        self.streaming_pending = False
        self.streaming_lock = threading.Lock()
        self.streamed_content = ""
        self.is_thinking = False
        self.thinking_text = ""
        self.main_text = ""
        self.current_streaming_message = None
        self.streaming_box = None
        self.last_update = 0
        
        # Generation state
        self.active_tool_results = []
        self.auto_run_times = 0
        self.last_generation_time = None
        self.last_token_num = None
        
        # Recording state
        self.recording = False
        self.video_recorder = None
        
        # Attachment state
        self.attached_image_data = None
        
        # Error tracking
        self.last_error_box = None
        
        self.suggestions_timer_id = None
        self.connect("map", self._on_map)

        # Build UI
        self._build_ui()
        
    def _setup_chat_history(self, chat_history):
        """Connect signals for a ChatHistory object."""
        chat_history.connect("focus-input", lambda _: self.focus_input())
        chat_history.connect("branch-requested", self._on_branch_requested)
        chat_history.connect("clear-requested", self._on_clear_requested)
        chat_history.connect("continue-requested", self._on_continue_requested)
        chat_history.connect("regenerate-requested", self._on_regenerate_requested)
        chat_history.connect("stop-requested", self._on_stop_requested)
        chat_history.connect("files-dropped", self._on_files_dropped)

    def _build_ui(self):
        """Build the tab UI with chat history and input box."""
        # Notification overlay for toasts
        self.notification_block = Adw.ToastOverlay()
        self.append(self.notification_block)
        
        # History stack for transitions
        self.history_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=250
        )
        self.notification_block.set_child(self.history_stack)
        
        # Initial chat history
        self.chat_history = ChatHistory(self, self.chat, self._chat_id)
        self._setup_chat_history(self.chat_history)
        self.chat_history.populate_chat()
        
        self.history_stack.add_child(self.chat_history)
        
        # Separator
        self.append(Gtk.Separator())
        
        # Input box
        self._build_input_box()
        
    def _build_input_box(self):
        """Build the input box with attach, record, text entry, and send buttons."""
        self.input_box = Gtk.Box(
            halign=Gtk.Align.FILL,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
            spacing=6,
        )
        self.input_box.set_valign(Gtk.Align.CENTER)
        
        # Quick toggles
        self._build_quick_toggles()
        
        # Attach button
        self.attach_button = Gtk.Button(
            css_classes=["flat", "circular"], icon_name="attach-symbolic"
        )
        self.attach_button.connect("clicked", self.attach_file)
        self.input_box.append(self.attach_button)
        
        # Attached image preview
        self.attached_image = Gtk.Image(visible=False)
        self.attached_image.set_size_request(36, 36)
        self.input_box.append(self.attached_image)
        
        # Update attach button visibility based on model capabilities
        self._update_attach_visibility()
        
        # Screen recording button
        self.screen_record_button = Gtk.Button(
            icon_name="media-record-symbolic",
            css_classes=["flat"],
            halign=Gtk.Align.CENTER,
        )
        self.screen_record_button.connect("clicked", self.start_screen_recording)
        self.input_box.append(self.screen_record_button)
        
        if not self.model.supports_video_vision():
            self.screen_record_button.set_visible(False)
        
        # Text entry
        self.input_panel = MultilineEntry(not self.controller.newelle_settings.send_on_enter)
        self.input_panel.set_on_image_pasted(self.image_pasted)
        self.input_box.append(self.input_panel)
        self.input_panel.set_placeholder(_("Send a message..."))
        
        # Mic button
        self.mic_button = Gtk.Button(
            css_classes=["suggested-action"],
            icon_name="audio-input-microphone-symbolic",
            width_request=36,
            height_request=36,
        )
        self.mic_button.set_vexpand(False)
        self.mic_button.set_valign(Gtk.Align.CENTER)
        self.mic_button.connect("clicked", self.start_recording)
        self.recording_button = self.mic_button
        self.input_box.append(self.mic_button)
        
        # Send button
        send_box = Gtk.Box()
        send_box.set_vexpand(False)
        self.send_button = Gtk.Button(
            css_classes=["suggested-action"],
            icon_name="go-next-symbolic",
            width_request=36,
            height_request=36,
        )
        self.send_button.set_vexpand(False)
        self.send_button.set_valign(Gtk.Align.CENTER)
        send_box.append(self.send_button)
        self.input_box.append(send_box)
        
        self.input_panel.set_on_enter(self.on_entry_activate)
        self.send_button.connect("clicked", self.on_entry_button_clicked)
        
        self.append(self.input_box)
        
    def _build_quick_toggles(self):
        """Build quick toggle buttons for settings."""
        self.quick_toggles = Gtk.MenuButton(
            css_classes=["flat"], icon_name="controls-big"
        )
        self.quick_toggles_popover = Gtk.Popover()
        entries = [
            {"setting_name": "rag-on", "title": _("Local Documents")},
            {"setting_name": "memory-on", "title": _("Long Term Memory")},
            {"setting_name": "tts-on", "title": _("TTS")},
            {"setting_name": "websearch-on", "title": _("Web search")},
        ]
        
        # Only add virtualization option if running in Flatpak
        if is_flatpak():
            entries.append({"setting_name": "virtualization", "title": _("Virtualization")})
        
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        container.set_margin_start(12)
        container.set_margin_end(12)
        container.set_margin_top(6)
        container.set_margin_bottom(6)
        
        for entry in entries:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=entry["title"], xalign=0, hexpand=True)
            row.append(label)
            
            switch = Gtk.Switch()
            switch.set_active(self.controller.settings.get_boolean(entry["setting_name"]))
            
            def on_switch_toggled(switch, _, setting_name=entry["setting_name"]):
                self.controller.settings.set_boolean(setting_name, switch.get_active())
            
            switch.connect("notify::active", on_switch_toggled)
            row.append(switch)
            container.append(row)
        
        self.quick_toggles_popover.set_child(container)
        self.quick_toggles.set_popover(self.quick_toggles_popover)
        self.input_box.append(self.quick_toggles)
        self.quick_toggles_popover.connect("closed", self._update_toggles)
        
    def _update_toggles(self, *_):
        """Update settings when quick toggles popover is closed."""
        self.controller.update_settings()
        
    def _update_attach_visibility(self):
        """Update attach button visibility based on model capabilities."""
        model = self.model
        rag_handler = self.window.rag_handler
        
        if (
            not model.supports_vision()
            and not model.supports_video_vision()
            and (
                len(model.get_supported_files())
                + (len(rag_handler.get_supported_files()) if rag_handler is not None else 0)
                == 0
            )
        ):
            self.attach_button.set_visible(False)
        else:
            self.attach_button.set_visible(True)
            
    # Properties
    @property
    def chat_id(self) -> int:
        """Get the chat ID for this tab."""
        return self._chat_id
    
    @property
    def chat(self) -> list:
        """Get the chat data for this tab."""
        if self._chat_id < len(self.controller.chats):
            return self.controller.chats[self._chat_id]["chat"]
        return []
    
    @chat.setter
    def chat(self, value: list):
        """Set the chat data for this tab."""
        if self._chat_id < len(self.controller.chats):
            self.controller.chats[self._chat_id]["chat"] = value
            
    @property
    def chat_name(self) -> str:
        """Get the chat name for this tab."""
        if self._chat_id < len(self.controller.chats):
            return self.controller.chats[self._chat_id].get("name", _("New Chat"))
        return _("New Chat")
    
    @property
    def status(self) -> bool:
        """Get the generation status (True = ready, False = generating)."""
        return self.chat_history.status
    
    @status.setter
    def status(self, value: bool):
        """Set the generation status."""
        self.chat_history.status = value
        
    @property
    def model(self):
        """Get the LLM model from handlers."""
        return self.controller.handlers.llm
    
    @property
    def tts(self):
        """Get the TTS handler."""
        return self.controller.handlers.tts
    
    @property
    def tts_enabled(self) -> bool:
        """Check if TTS is enabled."""
        return self.controller.newelle_settings.tts_enabled
    
    @property
    def rag_handler(self):
        """Get the RAG handler."""
        return self.window.rag_handler
    
    # Tab management
    def set_tab_page(self, tab_page: Adw.TabPage):
        """Set the tab page reference."""
        self.tab_page = tab_page
        self._update_tab_title()
        
    def _update_tab_title(self):
        """Update the tab title to reflect the chat name."""
        if self.tab_page:
            self.tab_page.set_title(self.chat_name)
            
    def update_tab_indicator(self):
        """Update tab indicator to show generation status."""
        if self.tab_page:
            if not self.status:
                # Generating - show loading indicator
                self.tab_page.set_loading(True)
            else:
                self.tab_page.set_loading(False)
    
    def switch_to_chat(self, chat_id: int):
        """Switch this tab to display a different chat with animation.
        
        Args:
            chat_id: The ID of the chat to switch to
        """
        if not self.status:
            # Cannot switch while generating
            return
        
        # Determine direction based on chat IDs
        # If new ID > old ID, we are moving down the list (slide up)
        # If new ID < old ID, we are moving up the list (slide down)
        # Note: This assumes chat IDs are chronological or sorted in some way
        
        # Determine if we should reverse the direction based on settings
        reverse_order = self.controller.newelle_settings.reverse_order
        
        if chat_id > self._chat_id:
            # Moving to a newer chat (or older if reversed)
            transition = Gtk.StackTransitionType.SLIDE_UP if not reverse_order else Gtk.StackTransitionType.SLIDE_DOWN
        else:
            # Moving to an older chat (or newer if reversed)
            transition = Gtk.StackTransitionType.SLIDE_DOWN if not reverse_order else Gtk.StackTransitionType.SLIDE_UP
            
        self.history_stack.set_transition_type(transition)
        self.history_stack.set_transition_duration(300)
        
        # Update internal chat_id
        self._chat_id = chat_id
        
        # Update tab title
        self._update_tab_title()
        
        # Create new chat history
        old_history = self.chat_history
        self.chat_history = ChatHistory(self, self.chat, self._chat_id)
        self._setup_chat_history(self.chat_history)
        self.chat_history.populate_chat()
        
        # Add to stack and switch
        self.history_stack.add_child(self.chat_history)
        self.history_stack.set_visible_child(self.chat_history)
        
        self.start_suggestions_timer()

        # Remove old history after animation
        def remove_old():
            self.history_stack.remove(old_history)
            return False
            
        GLib.timeout_add(550, remove_old)
                
    # Input handling
    def focus_input(self):
        """Focus the input panel."""
        self.input_panel.grab_focus()
        
    def on_entry_button_clicked(self, *a):
        """Handle send button click."""
        self.on_entry_activate(self.input_panel)
        
    def on_entry_activate(self, entry):
        """Send a message when input is pressed."""
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("The message cannot be sent until the program is finished"),
                    timeout=2,
                )
            )
            return False
        
        text = entry.get_text()
        entry.set_text("")
        
        if text and not text.isspace():
            if self.attached_image_data is not None:
                if self.attached_image_data.endswith((".png", ".jpg", ".jpeg", ".webp")) or \
                   self.attached_image_data.startswith("data:image/jpeg;base64,"):
                    text = "```image\n" + self.attached_image_data + "\n```\n" + text
                elif self.attached_image_data.endswith((".mp4", ".mkv", ".webm", ".avi")):
                    text = "```video\n" + self.attached_image_data + "\n```\n" + text
                else:
                    text = "```file\n" + self.attached_image_data + "\n```\n" + text
                self.delete_attachment(self.attach_button)
            
            self.chat.append({"User": "User", "Message": text})
            self.chat_history.show_message(text, True, id_message=len(self.chat) - 1, is_user=True)
            
            # Store current profile in chat data
            if self._chat_id < len(self.controller.chats):
                self.controller.chats[self._chat_id]["profile"] = self.window.current_profile

        GLib.timeout_add(200, self.chat_history.scrolled_chat)
        threading.Thread(target=self.send_message).start()
        self.send_button_start_spinner()
        
    def send_button_start_spinner(self):
        """Show spinner on send button."""
        spinner = Gtk.Spinner(spinning=True)
        self.send_button.set_child(spinner)
        
    def remove_send_button_spinner(self):
        """Remove spinner from send button."""
        self.send_button.set_child(None)
        self.send_button.set_icon_name("go-next-symbolic")
        
    # Message sending and streaming
    def send_message(self, manual=True):
        """Send a message in the chat and get bot answer."""
        if manual:
            self.auto_run_times = 0
        
        self.stream_number_variable += 1
        stream_number_variable = self.stream_number_variable
        self.status = False
        self.emit("generation-started")
        GLib.idle_add(self.update_tab_indicator)
        GLib.idle_add(self.chat_history.set_generating, True)
        
        # Start creating the message
        if self.model.stream_enabled():
            self.streamed_message = ""
            self.curr_label = ""
            self.streaming_label = None
            self.last_update = time.time()
            self.stream_thinking = False
            GLib.idle_add(self.create_streaming_message_label)
            
        def run_generation():
            for status, data in self.controller.generate_response(
                stream_number_variable, 
                self.update_message,
                chat_id=self._chat_id
            ):
                if self.stream_number_variable != stream_number_variable:
                    break
                
                if status == 'reload_chat':
                    GLib.idle_add(self.show_chat)
                elif status == 'reload_message':
                    GLib.idle_add(self.reload_message, data)
                elif status == 'error':
                    def handle_error_ui():
                        self.chat_history.show_message(data, False, -1, False, False, True)
                        self.remove_send_button_spinner()
                        self.status = True
                        self.update_tab_indicator()
                        self.emit("generation-stopped")
                    GLib.idle_add(handle_error_ui)
                elif status == 'done':
                    GLib.idle_add(self.remove_send_button_spinner)
                    GLib.idle_add(self.show_chat)
                elif status == 'finished':
                    def finish_safe():
                        self._handle_generation_finished(data, stream_number_variable)
                    GLib.idle_add(finish_safe)
        
        threading.Thread(target=run_generation).start()
        
    def _handle_generation_finished(self, data, stream_number_variable):
        """Handle generation completion."""
        message_label = data['message']
        prompts = data['prompts']
        self.last_generation_time = data['time']
        self.last_token_num = (data['input_tokens'], data['output_tokens'])
        
        if hasattr(self, "current_streaming_message") and self.current_streaming_message:
            # Streaming was active, finalize the existing widget
            streaming_widget = self.current_streaming_message
            self.chat.append({
                "User": "Assistant", 
                "Message": message_label, 
                "UUID": streaming_widget.chunk_uuid
            })
            self.chat_history.update_history(self.chat)
            self.add_prompt("\n".join(prompts))
            
            final_message = message_label
            
            def finalize_stream():
                streaming_widget.update_content(final_message, is_streaming=False)
                streaming_widget.finish_streaming()
                self.chat_history._finalize_message_display()
                self.save_chat()
                
                # Handle deferred tool execution and continuation
                if streaming_widget.state.get("has_terminal_command", False):
                    threads = streaming_widget.state.get("running_threads", [])
                    parallel = self.controller.newelle_settings.parallel_tool_execution
                    current_stream = self.stream_number_variable
                    
                    def wait_and_continue():
                        if not parallel:
                            for t in threads:
                                if not t.is_alive():
                                    t.start()
                                t.join()
                        else:
                            for t in threads:
                                t.join()
                        
                        if self.stream_number_variable != current_stream:
                            return
                        
                        if threads and streaming_widget.state.get("should_continue", False):
                            self.send_message(manual=False)
                        else:
                            GLib.idle_add(self.chat_history.scrolled_chat)
                    
                    threading.Thread(target=wait_and_continue).start()
                else:
                    GLib.idle_add(self.chat_history.scrolled_chat)
            
            finalize_stream()
            self.current_streaming_message = None
        else:
            # No streaming, standard display
            self.chat_history.show_message(
                message_label,
                False,
                -1,
                False,
                False,
                False,
                "\n".join(prompts),
            )
        
        GLib.idle_add(self.chat_history.set_generating, False)
        GLib.idle_add(self.remove_send_button_spinner)
        GLib.idle_add(self.update_tab_indicator)
        self.emit("generation-stopped")
        
        # Generate suggestions
        self.generate_suggestions()

        # Generate chat name
        if self.controller.newelle_settings.auto_generate_name and len(self.chat) == 2:
            GLib.idle_add(self.generate_chat_name)
        
        # TTS
        tts_thread = None
        if self.tts_enabled:
            message_label = convert_think_codeblocks(message_label)
            message = re.sub(r"```.*?```", "", message_label, flags=re.DOTALL)
            message = remove_markdown(message)
            message = remove_emoji(message)
            if message.strip() and not message.isspace():
                tts_thread = threading.Thread(
                    target=self.tts.play_audio, args=(message,)
                )
                tts_thread.start()
        
        # Wait for TTS to finish before restarting recording
        def restart_recording():
            if not self.window.automatic_stt_status:
                return
            if tts_thread is not None:
                tts_thread.join()
            GLib.idle_add(self.start_recording, self.recording_button)
        
        if self.controller.newelle_settings.automatic_stt:
            threading.Thread(target=restart_recording).start()
            
    def create_streaming_message_label(self):
        """Create a label for message streaming."""
        self.streamed_content = ""
        self.streaming_pending = False
        
        next_message_id = len(self.chat)
        self.current_streaming_message = Message(
            "",
            is_user=False,
            parent_window=self,
            id_message=next_message_id,
        )
        self.streaming_box = self.chat_history.add_message(
            "Assistant",
            self.current_streaming_message,
            id_message=next_message_id,
            editable=True,
        )
        try:
            if hasattr(self.chat_history, "messages_box") and len(self.chat_history.messages_box) > 0:
                self.chat_history.messages_box.pop()
        except (AttributeError, IndexError):
            pass
        self.streaming_box.set_overflow(Gtk.Overflow.VISIBLE)
        
    def update_message(self, message, stream_number_variable, *args):
        """Update message label when streaming (thread-safe)."""
        if self.stream_number_variable != stream_number_variable:
            return
        
        if time.time() - self.last_update >= 0.2:
            self.last_update = time.time()
            GLib.idle_add(self.refresh_streaming_ui, message, stream_number_variable)
            
    def refresh_streaming_ui(self, message, stream_number_variable):
        """Update the UI with the latest streamed content (main thread)."""
        if self.stream_number_variable != stream_number_variable:
            return GLib.SOURCE_REMOVE
        
        if hasattr(self, 'current_streaming_message') and self.current_streaming_message:
            self.current_streaming_message.update_content(message, is_streaming=True)
        
        return GLib.SOURCE_REMOVE
    
    def add_reading_widget(self, documents):
        """Add document reading widget during streaming."""
        d = [doc.replace("file:", "") for doc in documents if doc.startswith("file:")]
        documents = d
        if self.model.stream_enabled() and hasattr(self, "current_streaming_message"):
            if self.current_streaming_message is not None:
                self.reading = DocumentReaderWidget()
                for document in documents:
                    self.reading.add_document(document)
                self.current_streaming_message.append(self.reading)
            
    def remove_reading_widget(self):
        """Remove document reading widget."""
        try:
            if hasattr(self, "reading") and hasattr(self, "current_streaming_message"):
                if self.current_streaming_message is not None and self.reading is not None:
                    parent = self.reading.get_parent()
                    if parent == self.current_streaming_message:
                        self.current_streaming_message.remove(self.reading)
                    self.reading = None
        except (AttributeError, TypeError, RuntimeError):
            pass
            
    def add_prompt(self, prompt: str | None):
        """Add prompt metadata to the last message."""
        if prompt is None:
            return
        self.chat[-1]["enlapsed"] = self.last_generation_time
        self.chat[-1]["Prompt"] = prompt
        self.chat[-1]["InputTokens"] = self.last_token_num[0]
        self.chat[-1]["OutputTokens"] = self.last_token_num[1]
        
    def reload_message(self, message_id: int):
        """Reload a message in the chat history."""
        if len(self.chat_history.messages_box) < message_id:
            return
        if self.chat[message_id]["User"] == "Console":
            return
        message_box = self.chat_history.messages_box[message_id + 1]
        overlay = message_box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return
        old_label = content_box.get_last_child()
        if old_label is not None:
            content_box.remove(old_label)
            content_box.append(
                self.chat_history.show_message(
                    self.chat[message_id]["Message"],
                    id_message=message_id,
                    is_user=self.chat[message_id]["User"] == "User",
                    return_widget=True,
                    restore=True
                )
            )
            
    # Chat management
    def show_chat(self):
        """Reload and display the chat."""
        self.stream_tools = False
        self.last_error_box = None
        self.chat_history.show_chat()
        
    def save_chat(self):
        """Save the chat to disk."""
        self.controller.save_chats()
        
    def clear_chat(self):
        """Clear the current chat."""
        self.notification_block.add_toast(
            Adw.Toast(title=_("Chat is cleared"), timeout=2)
        )
        self.chat.clear()
        for tool_result in self.active_tool_results:
            tool_result.cancel()
        self.active_tool_results = []
        self.show_chat()
        self.stream_number_variable += 1
        GLib.idle_add(self.chat_history.update_button_text)
        
    def stop_chat(self):
        """Stop the current generation."""
        self.model.stop()
        for tool_result in self.active_tool_results:
            tool_result.cancel()
        self.active_tool_results = []
        self.status = True
        self.stream_number_variable += 1
        GLib.idle_add(self.chat_history.update_button_text)
        GLib.idle_add(self.update_tab_indicator)
        self.notification_block.add_toast(
            Adw.Toast(title=_("The message generation was stopped"), timeout=2)
        )
        GLib.idle_add(self.show_chat)
        self.remove_send_button_spinner()
        self.emit("generation-stopped")
        
    def continue_message(self):
        """Continue the last message."""
        if self.chat_history.chat[-1]["User"] not in ["Assistant", "Console", "User"]:
            self.notification_block.add_toast(
                Adw.Toast(title=_("You can no longer continue the message."), timeout=2)
            )
        else:
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()
            
    def regenerate_message(self):
        """Regenerate the last message."""
        if self.chat_history.chat[-1]["User"] in ["Assistant", "Console"]:
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant", "Console"]:
                    self.chat.pop(i)
                else:
                    break
            self.show_chat()
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()
        elif self.chat_history.last_error_box is not None:
            self.show_chat()
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()
        else:
            self.notification_block.add_toast(
                Adw.Toast(title=_("You can no longer regenerate the message."), timeout=2)
            )
            
    def generate_chat_name(self):
        """Generate a name for the chat based on content."""
        def generate():
            name = self.window.secondary_model.generate_chat_name(
                self.controller.newelle_settings.prompts["generate_name_prompt"],
                self.controller.get_history(chat=self.chat)
            )
            if name:
                name = name.strip().strip('"').strip("'")
                if self._chat_id < len(self.controller.chats):
                    self.controller.chats[self._chat_id]["name"] = name
                    self.save_chat()
                    GLib.idle_add(self._update_tab_title)
                    GLib.idle_add(self.window.update_history)
                    GLib.idle_add(self.emit,"chat-name-changed", name)

        threading.Thread(target=generate).start()
        
    # Signal handlers from ChatHistory
    def _on_branch_requested(self, chat_history, message_id: int):
        """Handle branch request from chat history."""
        self.window.create_branch(message_id, self._chat_id)
        
    def _on_clear_requested(self, chat_history):
        """Handle clear request from chat history."""
        self.clear_chat()
        
    def _on_continue_requested(self, chat_history):
        """Handle continue request from chat history."""
        self.continue_message()
        
    def _on_regenerate_requested(self, chat_history):
        """Handle regenerate request from chat history."""
        self.regenerate_message()
        
    def _on_stop_requested(self, chat_history):
        """Handle stop request from chat history."""
        self.stop_chat()
        
    def _on_files_dropped(self, chat_history, data):
        """Handle files dropped on chat history."""
        self.window.handle_file_drag(None, data, 0, 0)
        
    # File attachment
    def attach_file(self, button):
        """Open file chooser to attach a file."""
        self.window.attach_file(button)
        
    def image_pasted(self, image):
        """Handle image pasted into input."""
        self.window.image_pasted(image)
        
    def delete_attachment(self, button):
        """Delete the current attachment."""
        self.attached_image_data = None
        self.attach_button.set_icon_name("attach-symbolic")
        self.attach_button.set_css_classes(["circular", "flat"])
        self.attach_button.disconnect_by_func(self.delete_attachment)
        self.attach_button.connect("clicked", self.attach_file)
        self.attached_image.set_visible(False)
        self.screen_record_button.set_visible(self.window.model.supports_video_vision())
        
    def add_file(self, file_path=None, file_data=None):
        """Add a file attachment and update the UI, also generates thumbnail for videos

        Args:
            file_path (): file path for the file
            file_data (): file data for the file
        """
        if file_path is not None:
            if file_path.lower().endswith((".mp4", ".avi", ".mov")):
                cmd = [
                    "ffmpeg",
                    "-i",
                    file_path,
                    "-vframes",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
                frame_data = subprocess.run(cmd, capture_output=True).stdout

                if frame_data:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(frame_data)
                    loader.close()
                    self.attached_image.set_from_pixbuf(loader.get_pixbuf())
                else:
                    self.attached_image.set_from_icon_name("video-x-generic")
            elif file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.attached_image.set_from_file(file_path)
            else:
                self.attached_image.set_from_icon_name("text-x-generic")

            self.attached_image_data = file_path
            self.attached_image.set_visible(True)
        elif file_data is not None:
            base64_image = base64.b64encode(file_data).decode("utf-8")
            self.attached_image_data = f"data:image/jpeg;base64,{base64_image}"
            loader = GdkPixbuf.PixbufLoader()
            loader.write(file_data)
            loader.close()
            self.attached_image.set_from_pixbuf(loader.get_pixbuf())
            self.attached_image.set_visible(True)

        self.attach_button.set_icon_name("user-trash-symbolic")
        self.attach_button.set_css_classes(["destructive-action", "circular"])
        self.attach_button.connect("clicked", self.delete_attachment)
        # Disconnect the attach_file handler - we need to get the handler ID
        # The attach_file was connected in _build_ui, so we need to disconnect it here
        # Since we can't directly disconnect by func in this case, we'll rebuild the button state
        self.attach_button.disconnect_by_func(self.attach_file)
        self.screen_record_button.set_visible(False)
        
    # Recording
    def start_recording(self, button):
        """Start voice recording."""
        try:
            button.disconnect_by_func(self.start_recording)
        except TypeError:
            # Handler was not connected to this function
            pass
        self.window.start_recording(button)
        
    def start_screen_recording(self, button):
        """Start screen recording."""
        self.window.start_screen_recording(button)
        
    # Bot response (for suggestions)
    def send_bot_response(self, button):
        """Send a bot response suggestion."""
        self.send_button_start_spinner()
        text = button.get_child().get_label()
        self.chat.append({"User": "User", "Message": text})
        self.chat_history.show_message(text, id_message=len(self.chat) - 1, is_user=True)
        
        # Store current profile in chat data
        if self._chat_id < len(self.controller.chats):
            self.controller.chats[self._chat_id]["profile"] = self.window.current_profile
        
        threading.Thread(target=self.send_message).start()

    # Suggestions
    def _on_map(self, widget):
        """Handle map event (when tab is shown)."""
        self.start_suggestions_timer()

    def start_suggestions_timer(self):
        """Start timer to generate suggestions if tab remains active."""
        if self.suggestions_timer_id is not None:
            GLib.source_remove(self.suggestions_timer_id)
        self.suggestions_timer_id = GLib.timeout_add(2000, self._on_suggestions_timer)

    def _on_suggestions_timer(self):
        """Timer callback to generate suggestions."""
        self.suggestions_timer_id = None
        # Check if tab is active (mapped and selected)
        if self.window.get_active_chat_tab() == self and self.get_mapped():
             self.generate_suggestions()
        return False

    def generate_suggestions(self):
        """Create the suggestions and update the UI when it's finished"""
        if not self.status or self.chat_history.has_suggestions(): # Don't generate if currently generating a message or suggestions are already shown
             return

        def generate():
            try:
                suggestions = self.controller.handlers.secondary_llm.get_suggestions(
                    self.controller.newelle_settings.prompts["get_suggestions_prompt"],
                    self.controller.newelle_settings.offers,
                    self.controller.get_history(chat=self.chat_history.chat)
                )
                GLib.idle_add(self.chat_history.populate_suggestions, suggestions)
            except Exception as e:
                print(e)
                pass
        
        threading.Thread(target=generate).start()

