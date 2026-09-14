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
import re
import gettext
import subprocess
import base64

from .chat_history import ChatHistory
from .multiline import MultilineEntry
from .mode_switcher import ModeButton
from .context_indicator import ContextIndicator
from .documents_reader import DocumentReaderWidget
from .message import Message
from .. import apply_css_to_widget
from ...utility.strings import (
    clean_message_tts,
    convert_think_codeblocks,
    remove_markdown,
    remove_emoji,
)
from ...utility.system import is_flatpak
from ...utility.media import extract_supported_files, file_matches_patterns
from ...tools import Command

_ = gettext.gettext

_STREAM_REVEAL_INTERVAL_MS = 30
_STREAM_REVEAL_TARGET_FRAMES = 4
_STREAM_REVEAL_MAX_CHARS = 48


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
        self._stream_target_content = ""
        self._stream_reveal_source_id = None
        self._stream_reveal_generation = None
        self.is_thinking = False
        self.thinking_text = ""
        self.main_text = ""
        self.current_streaming_message = None
        self.streaming_box = None
        
        # Generation state
        self.active_tool_results = []
        self.auto_run_times = 0
        self.tool_call_count = 0
        self.last_generation_time = None
        self.last_token_num = None
        self.last_time_to_first_token = None
        self.last_time_to_first_token_no_thinking = None
        
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
        """Build the stacked-card input box.

        Two layouts share the same widgets:
        - normal: text on top, controls in a row below it;
        - compact: a single row where an options button and the mic/send
          buttons float over the bottom-right corner of the text entry.
        """
        self.input_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.FILL,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
            spacing=6,
        )
        # Frame the whole composer as a single rounded card. We reuse Adwaita's
        # `card` class for the themed surface + border, then round the corners.
        # The inner MultilineEntry has its own .card/.frame stripped so it
        # blends into this outer card instead of drawing a second frame.
        self.input_box.add_css_class("card")
        self.input_box.add_css_class("input-card")
        apply_css_to_widget(self.input_box, """
            .card.input-card {
                border-radius: 14px;
                padding: 6px 10px;
            }
            /* Ensure the inner text view inherits the card background. */
            .input-card scrolledwindow,
            .input-card text {
                background-color: transparent;
            }
        """)
        self.input_box.set_valign(Gtk.Align.CENTER)

        # --- Text entry ---
        self.input_panel = MultilineEntry(not self.controller.newelle_settings.send_on_enter)
        self.input_panel.set_on_image_pasted(self.image_pasted)
        # The outer card frames the entry; drop the inner MultilineEntry chrome.
        self.input_panel.remove_css_class("card")
        self.input_panel.remove_css_class("frame")
        self.input_panel.set_placeholder(_("Send a message..."))

        # --- Controls shared by both layouts ---
        self._input_layout_compact = None
        self._actions_row = None
        self._compact_overlay = None
        self._build_input_action_widgets()

        # --- Layout (compact overlays the buttons on the text entry) ---
        self.set_compact_input(self.controller.newelle_settings.compact_input_bar)

        self._update_attach_visibility()

        self.input_panel.set_on_enter(self.on_entry_activate)
        self.send_button.connect("clicked", self.on_entry_button_clicked)

        self._build_command_popover()
        self.input_panel.input_panel.get_buffer().connect("changed", self._on_input_changed)

        # SHIFT+TAB cycles through Modes while focused in the input box.
        mode_key_ctrl = Gtk.EventControllerKey.new()
        mode_key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        mode_key_ctrl.connect("key-pressed", self._on_mode_cycle_key_pressed)
        self.input_panel.input_panel.add_controller(mode_key_ctrl)

        self.append(self.input_box)

        # Populate the thinking control from the current model's capabilities.
        self._populate_thinking_control()

    def _build_input_action_widgets(self):
        """Create the input controls used by the normal and compact layouts."""
        self.attach_button = Gtk.Button(
            css_classes=["flat", "circular"], icon_name="attach-symbolic",
            tooltip_text=_("Attach file"),
        )
        self.attach_button.connect("clicked", self.attach_file)

        self.attached_image = Gtk.Image(visible=False)
        self.attached_image.set_size_request(36, 36)
        self.attachment_mode = Gtk.DropDown.new_from_strings([
            _("Automatic"), _("Send to model"), _("Use RAG"),
        ])
        self.attachment_mode.set_tooltip_text(_("How to process this document"))
        self.attachment_mode.set_visible(False)

        self.screen_record_button = Gtk.Button(
            icon_name="media-record-symbolic",
            css_classes=["flat", "circular"],
            tooltip_text=_("Screen recording"),
        )
        self.screen_record_button.connect("clicked", self.start_screen_recording)
        if not self.vision_model.supports_video_vision():
            self.screen_record_button.set_visible(False)

        # Quick toggles popover button
        self._build_quick_toggles()

        # Mode switcher (built only if the controller has a mode manager).
        self.mode_button = None
        if getattr(self.controller, "mode_manager", None) is not None:
            self.mode_button = ModeButton(self.controller, self.window)

        # Thinking-effort control (auto-hidden unless the model opts in).
        self.thinking_button = self._build_thinking_control()

        self.mic_button = Gtk.Button(
            css_classes=["flat", "circular"],
            icon_name="audio-input-microphone-symbolic",
            width_request=36,
            height_request=36,
            tooltip_text=_("Record"),
        )
        self.mic_button.set_vexpand(False)
        self.mic_button.set_valign(Gtk.Align.CENTER)
        self.mic_button.connect("clicked", self.start_recording)
        self.recording_button = self.mic_button

        self.send_button = Gtk.Button(
            css_classes=["suggested-action"],
            icon_name="go-next-symbolic",
            width_request=36,
            height_request=36,
            tooltip_text=_("Send"),
        )
        self.send_button.set_vexpand(False)
        self.send_button.set_valign(Gtk.Align.CENTER)

        # Context usage indicator (pie-chart ring).
        self.context_indicator = ContextIndicator()
        self.context_indicator.set_valign(Gtk.Align.CENTER)

    def set_compact_input(self, compact: bool):
        """Switch the input bar between the normal and the compact layout.

        The text entry and every control keep their state (typed text,
        attachment, recording...) because the same widgets are reparented.
        """
        if self._input_layout_compact == compact:
            return
        self._teardown_input_layout()
        if compact:
            self._build_compact_input_layout()
        else:
            self._build_normal_input_layout()
        self._input_layout_compact = compact

    @staticmethod
    def _detach_from_parent(widget):
        """Unparent a widget so it can be moved into another container."""
        parent = widget.get_parent()
        if parent is None:
            return
        if isinstance(parent, Gtk.Overlay):
            parent.remove_overlay(widget)
        else:
            parent.remove(widget)

    def _teardown_input_layout(self):
        """Remove the current layout, keeping the shared widgets alive."""
        if self._input_layout_compact is not None:
            if self._input_layout_compact:
                # Free the text entry from the overlay before discarding it.
                self._compact_overlay.set_child(None)
                self.input_box.remove(self._compact_overlay)
                self._compact_overlay = None
            else:
                self.input_box.remove(self.input_panel)
                self.input_box.remove(self._actions_row)
                self._actions_row = None
        # Always detach the shared widgets: on the first layout build they
        # still sit in their creation-time containers. Popovers wrap their
        # child in an internal container and offer no remove(), so their
        # content is detached first.
        self.quick_toggles_popover.set_child(None)
        if getattr(self, "compact_options_popover", None) is not None:
            self.compact_options_popover.set_child(None)
        for widget in (
            self.attach_button, self.attached_image, self.attachment_mode, self.screen_record_button,
            self.quick_toggles, self.quick_toggles_box, self.mode_button,
            self.thinking_button, self.mic_button, self.send_button,
            self.context_indicator, getattr(self, "compact_options_button", None),
        ):
            if widget is not None:
                self._detach_from_parent(widget)

    def _build_normal_input_layout(self):
        """Classic layout: text on top, controls in a row below it."""
        # Undo the compact layout's text view and button adjustments.
        self.input_panel.input_panel.set_bottom_margin(0)
        self.mic_button.set_size_request(36, 36)
        self.send_button.set_size_request(36, 36)
        # The quick toggles go back into their own popover button.
        self.quick_toggles_popover.set_child(self.quick_toggles_box)

        actions_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_start=2,
            margin_end=2,
        )

        # Left cluster order: attach / screen record / quick toggles / mode / effort
        left_cluster = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        left_cluster.append(self.attach_button)
        left_cluster.append(self.attached_image)
        left_cluster.append(self.attachment_mode)
        left_cluster.append(self.screen_record_button)
        left_cluster.append(self.quick_toggles)
        if self.mode_button is not None:
            left_cluster.append(self.mode_button)
        left_cluster.append(self.thinking_button)
        actions_row.append(left_cluster)

        # Right cluster (pushed to the end)
        actions_row.append(Gtk.Box(hexpand=True))
        right_cluster = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        right_cluster.append(self.mic_button)
        right_cluster.append(self.send_button)
        right_cluster.append(self.context_indicator)
        actions_row.append(right_cluster)

        self._actions_row = actions_row
        self.input_box.append(self.input_panel)
        self.input_box.append(actions_row)

    def _build_compact_input_layout(self):
        """Compact layout: a single row, the controls float over the text.

        The options, mic and send buttons sit together on the bottom-right
        corner, so the text keeps the full width and no extra row is added
        below it. The context indicator stays hidden in this mode.
        """
        # Reserve the bottom of the text view so no text runs under the
        # overlay buttons; this replaces the height of the actions row.
        self.input_panel.input_panel.set_bottom_margin(30)
        self.mic_button.set_size_request(28, 28)
        self.send_button.set_size_request(28, 28)

        self._ensure_compact_options()
        self._populate_compact_options()

        overlay = Gtk.Overlay()
        overlay.set_child(self.input_panel)

        right_cluster = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=2,
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            margin_end=2,
            margin_bottom=2,
        )
        right_cluster.append(self.compact_options_button)
        right_cluster.append(self.mic_button)
        right_cluster.append(self.send_button)
        overlay.add_overlay(right_cluster)

        self._compact_overlay = overlay
        self.input_box.append(overlay)

    def _ensure_compact_options(self):
        """Create the compact-layout options popover button (once)."""
        if getattr(self, "compact_options_button", None) is not None:
            return
        self.compact_options_button = Gtk.MenuButton(
            css_classes=["flat", "circular"],
            icon_name="controls-big-symbolic",
            tooltip_text=_("Input options"),
        )
        self.compact_options_button.set_valign(Gtk.Align.CENTER)
        self.compact_options_popover = Gtk.Popover()
        self.compact_options_button.set_popover(self.compact_options_popover)
        # Apply the quick toggles when the popover is closed, like the
        # standalone quick toggles popover does.
        self.compact_options_popover.connect("closed", self._update_toggles)
        # Rows are synced with the buttons' visibility every time it opens.
        self.compact_options_popover.connect(
            "notify::visible", self._sync_compact_options_rows
        )
        self.compact_options_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self.compact_options_box.set_margin_start(10)
        self.compact_options_box.set_margin_end(10)
        self.compact_options_box.set_margin_top(10)
        self.compact_options_box.set_margin_bottom(10)
        self.compact_options_box.set_size_request(280, -1)
        self.compact_options_popover.set_child(self.compact_options_box)
        self._compact_attach_row = None
        self._compact_record_row = None

    def _populate_compact_options(self):
        """Fill the compact options popover with the controls hidden from the bar."""
        box = self.compact_options_box
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

        # Attach + attachment preview
        attach_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        attach_row.append(self.attach_button)
        attach_row.append(Gtk.Label(
            label=_("Attach file"), hexpand=True, xalign=0,
        ))
        attach_row.append(self.attached_image)
        box.append(attach_row)
        box.append(self.attachment_mode)
        self._compact_attach_row = attach_row

        # Screen recording
        record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        record_row.append(self.screen_record_button)
        record_row.append(Gtk.Label(
            label=_("Screen recording"), hexpand=True, xalign=0,
        ))
        box.append(record_row)
        self._compact_record_row = record_row

        box.append(Gtk.Separator())

        # Quick toggles embedded directly, with no nested popover.
        box.append(self.quick_toggles_box)

        if self.mode_button is not None:
            box.append(Gtk.Separator())
            box.append(self.mode_button)
        box.append(self.thinking_button)

        self._sync_compact_options_rows()

    def _sync_compact_options_rows(self, *args):
        """Show the attach/record rows only when their buttons are visible."""
        if getattr(self, "compact_options_button", None) is None:
            return
        if self._compact_attach_row is not None:
            self._compact_attach_row.set_visible(self.attach_button.get_visible())
        if self._compact_record_row is not None:
            self._compact_record_row.set_visible(self.screen_record_button.get_visible())

    def _build_command_popover(self):
        """Build the slash-command hints popover attached to the input panel."""
        self._cmd_popover = Gtk.Popover()
        self._cmd_popover.set_parent(self.input_panel)
        self._cmd_popover.set_position(Gtk.PositionType.TOP)
        self._cmd_popover.set_autohide(False)
        self._cmd_popover.set_has_arrow(False)

        self._cmd_list = Gtk.ListBox()
        self._cmd_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._cmd_list.add_css_class("navigation-sidebar")
        self._cmd_list.connect("row-activated", self._on_command_selected)

        scroll = Gtk.ScrolledWindow(
            max_content_height=220,
            propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroll.set_child(self._cmd_list)
        scroll.set_size_request(300, -1)
        self._cmd_popover.set_child(scroll)
        self._cmd_selected_index = -1

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_cmd_key_pressed)
        self.input_panel.input_panel.add_controller(key_ctrl)

    def _on_cmd_key_pressed(self, controller, keyval, keycode, state):
        if not self._cmd_popover.get_visible():
            return False

        if keyval == Gdk.KEY_Up:
            self._move_cmd_selection(-1)
            return True
        elif keyval == Gdk.KEY_Down:
            self._move_cmd_selection(1)
            return True
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            selected = self._cmd_list.get_selected_row()
            if selected is None:
                selected = self._cmd_list.get_row_at_index(0)
            if selected is not None:
                self._on_command_selected(self._cmd_list, selected)
                return True
            
        return False

    def _on_mode_cycle_key_pressed(self, controller, keyval, keycode, state):
        # CTRL+M cycles to the next Mode (forward), wrapping around.
        if keyval == Gdk.KEY_m and (state & Gdk.ModifierType.CONTROL_MASK):
            self._cycle_mode()
            return True
        return False

    def _cycle_mode(self):
        """Switch to the next Mode and refresh the mode button + settings."""
        mm = getattr(self.controller, "mode_manager", None)
        if mm is None:
            return
        name = mm.cycle_mode()
        # Propagate skill overrides and reload prompt/tool mode settings.
        active = mm.get_active_mode()
        self.controller.skill_manager.set_mode_overrides(active.get("skills", {}))
        self.controller.update_settings()
        if self.mode_button is not None:
            self.mode_button.refresh()
        self.notification_block.add_toast(
            Adw.Toast.new(_("Mode: {0}").format(name))
        )

    def _move_cmd_selection(self, step):
        selected = self._cmd_list.get_selected_row()
        current_index = selected.get_index() if selected else (-1 if step > 0 else 0)
        
        count = 0
        while self._cmd_list.get_row_at_index(count) is not None:
            count += 1
            
        if count == 0:
            return
            
        new_index = max(0, min(current_index + step, count - 1))
        row = self._cmd_list.get_row_at_index(new_index)
        if row is not None:
            self._cmd_list.select_row(row)

    def _on_input_changed(self, buffer):
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if text.startswith("/"):
            query = text[1:].split(" ", 1)[0].lower()
            self._show_command_hints(query)
        else:
            self._cmd_popover.popdown()

    def _show_command_hints(self, query):
        while True:
            row = self._cmd_list.get_row_at_index(0)
            if row is None:
                break
            self._cmd_list.remove(row)

        commands = self.controller.get_commands()
        matches = [c for c in commands if query == "" or query in c.name.lower() or query in c.description.lower()]

        if not matches:
            self._cmd_popover.popdown()
            return

        for cmd in matches:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
            icon = Gtk.Image(icon_name=cmd.icon_name, pixel_size=18)
            icon.add_css_class("dim-label")
            box.append(icon)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name_label = Gtk.Label(label="/" + cmd.name, halign=Gtk.Align.START, xalign=0)
            name_label.add_css_class("heading")
            name_label.add_css_class("caption")
            text_box.append(name_label)

            desc_label = Gtk.Label(label=cmd.description, halign=Gtk.Align.START, xalign=0, ellipsize=2)
            desc_label.add_css_class("dim-label")
            desc_label.add_css_class("caption")
            text_box.append(desc_label)

            box.append(text_box)
            row.set_child(box)
            row.cmd_data = cmd
            self._cmd_list.append(row)

        first_row = self._cmd_list.get_row_at_index(0)
        if first_row is not None:
            self._cmd_list.select_row(first_row)

        self._cmd_popover.popup()

    def _on_command_selected(self, listbox, row):
        if row is None:
            return
        cmd = row.cmd_data
        full_text = self.input_panel.get_text()
        parts = full_text.split(" ", 1)
        args_str = parts[1] if len(parts) > 1 else ""

        required_args = cmd.schema.get("required", [])
        if required_args and not args_str.strip():
            self.input_panel.set_text(f"/{cmd.name} ")
            buffer = self.input_panel.input_panel.get_buffer()
            buffer.place_cursor(buffer.get_end_iter())
            self.input_panel.grab_focus()
            return

        self._cmd_popover.popdown()
        self.input_panel.set_text("")
        self._execute_command(cmd, args_str)

    def _execute_command(self, cmd, args_str=""):
        kwargs = {}
        if args_str:
            if "properties" in cmd.schema:
                for param_name in cmd.schema.get("properties", {}):
                    if cmd.schema["properties"][param_name]["type"] == "string":
                        kwargs[param_name] = args_str.strip()
                        break
        import uuid
        msg_uuid = int(uuid.uuid4())
        kwargs['msg_uuid'] = msg_uuid
        kwargs['chat_id'] = self._chat_id
        result = cmd.execute(**kwargs)
        if result is not None and result.widget is not None:
            display_text = f"/{cmd.name} {args_str}".strip()
            self.chat.append({"User": "Command", "Message": display_text, "UUID": msg_uuid})
            self.chat_history.hide_placeholder()
            self.chat_history.add_message("Command", result.widget, id_message=len(self.chat) - 1, editable=True)
            self.chat_history._finalize_message_display()
            GLib.idle_add(self.chat_history.scrolled_chat)
            def async_get_output():
                if result.get_output() is not None:
                    self.chat.append({"User": "Console", "Message": result.get_output()})
            thread = threading.Thread(target=async_get_output)
            thread.start()

    def _build_quick_toggles(self):
        """Build quick toggle buttons for settings (a popover MenuButton).

        The switches live in their own box (``quick_toggles_box``) so the
        compact layout can embed them directly in its options popover.
        """
        self.quick_toggles = Gtk.MenuButton(
            css_classes=["flat", "circular"], icon_name="controls-big",
            tooltip_text=_("Quick toggles"),
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
        
        self.quick_toggles_box = container
        self.quick_toggles_popover.set_child(container)
        self.quick_toggles.set_popover(self.quick_toggles_popover)
        self.quick_toggles_popover.connect("closed", self._update_toggles)
        
    def _update_toggles(self, *_):
        """Update settings when quick toggles popover is closed."""
        self.controller.update_settings()

    def _build_thinking_control(self):
        """Build the thinking-effort MenuButton (hidden unless the model opts in).

        The popover lists the levels returned by ``model.get_thinking_modes()``;
        selecting one calls ``model.set_thinking_mode()`` and reloads settings.
        Hidden entirely when the handler returns ``None``.
        """
        button = Gtk.MenuButton(css_classes=["flat"], valign=Gtk.Align.CENTER)
        button.set_visible(False)
        button.connect("notify::visible", lambda *_: None)
        self._thinking_popover = Gtk.Popover()
        button.set_popover(self._thinking_popover)
        self._thinking_label = Gtk.Label(label="")
        self._thinking_arrow = Gtk.Image(icon_name="pan-down-symbolic")
        self._thinking_arrow.add_css_class("dim-label")
        _box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        _box.append(Gtk.Image(icon_name="brain-augemnted-symbolic", pixel_size=16))
        _box.append(self._thinking_label)
        _box.append(self._thinking_arrow)
        button.set_child(_box)
        return button

    def _populate_thinking_control(self):
        """Rebuild the thinking control from the current model's capabilities."""
        model = self.model
        modes = model.get_thinking_modes() if hasattr(model, "get_thinking_modes") else None
        if not modes:
            self.thinking_button.set_visible(False)
            self._thinking_popover.set_child(None)
            return

        current = model.get_thinking_mode()
        # Label: show the label of the current value, fallback to the value.
        label = next((lbl for val, lbl in modes if val == current), modes[0][1])
        self._thinking_label.set_label(label)
        self.thinking_button.set_visible(True)

        list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        list_box.set_size_request(220, -1)
        for value, lbl in modes:
            row = Adw.ActionRow(title=lbl, activatable=True)
            if value == current:
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
            row.connect("activated", lambda _r, v=value: self._on_thinking_selected(v))
            list_box.append(row)
        self._thinking_popover.set_child(list_box)

    def _on_thinking_selected(self, value: str):
        try:
            self.model.set_thinking_mode(value)
        except Exception as e:
            print("Thinking mode error:", e)
        self.controller.update_settings()
        self._thinking_popover.popdown()
        self._populate_thinking_control()

    def refresh_mode_and_thinking(self):
        """Refresh the mode switcher label and the thinking control.

        Called on LLM change and after mode edits so the controls reflect the
        current state.
        """
        if self.mode_button is not None:
            self.mode_button.refresh()
        self._populate_thinking_control()
        
    def _update_attach_visibility(self):
        """Update attach button visibility based on model capabilities."""
        model = self.model
        vision_model = self.vision_model
        rag_handler = self.window.rag_handler
        
        if (
            not vision_model.supports_vision()
            and not vision_model.supports_video_vision()
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
        if self._chat_id in self.controller.chats:
            return self.controller.chats[self._chat_id]["chat"]
        return []

    @chat.setter
    def chat(self, value: list):
        """Set the chat data for this tab."""
        if self._chat_id in self.controller.chats:
            self.controller.chats[self._chat_id]["chat"] = value

    @property
    def chat_name(self) -> str:
        """Get the chat name for this tab."""
        if self._chat_id in self.controller.chats:
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
    def vision_model(self):
        """Get the LLM configured for image and video chats."""
        return self.controller.get_vision_model()
    
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
        text = entry.get_text()

        if text.startswith("/") and self._cmd_popover.get_visible():
            selected = self._cmd_list.get_selected_row()
            if selected is None:
                selected = self._cmd_list.get_row_at_index(0)
            if selected is not None:
                self._on_command_selected(self._cmd_list, selected)
                return False

        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("The message cannot be sent until the program is finished"),
                    timeout=2,
                )
            )
            return False
        
        if self.attached_image_data is not None and self.attachment_mode.get_visible():
            mode = self.attachment_mode.get_selected()
            rag = self.controller.handlers.rag
            if mode == 1 and not file_matches_patterns(self.attached_image_data, self.model.get_supported_files()):
                self.notification_block.add_toast(Adw.Toast(title=_("The selected model does not support this file type")))
                return False
            if mode == 2 and (rag is None or not file_matches_patterns(self.attached_image_data, rag.get_supported_files_reading())):
                self.notification_block.add_toast(Adw.Toast(title=_("The selected RAG provider does not support this file type")))
                return False

        entry.set_text("")
        
        if text and not text.isspace():
            if self.attached_image_data is not None:
                if self.attached_image_data.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or \
                   self.attached_image_data.startswith("data:image/"):
                    text = "```image\n" + self.attached_image_data + "\n```\n" + text
                elif self.attached_image_data.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
                    text = "```video\n" + self.attached_image_data + "\n```\n" + text
                else:
                    lang = ("file", "file_direct", "file_rag")[self.attachment_mode.get_selected()]
                    text = "```" + lang + "\n" + self.attached_image_data + "\n```\n" + text
                self.delete_attachment(self.attach_button)
            
            self.chat.append({"User": "User", "Message": text})
            self.chat_history.show_message(text, True, id_message=len(self.chat) - 1, is_user=True)

            # Store current profile in chat data
            if self._chat_id in self.controller.chats:
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
            self.tool_call_count = 0
        
        if manual or not hasattr(self, "_audio_cancel_event"):
            self._audio_cancel_event = threading.Event()
        audio_cancel_event = self._audio_cancel_event
        origin_chat_id = self._chat_id
        self.stream_number_variable += 1
        stream_number_variable = self.stream_number_variable
        self.status = False
        self.emit("generation-started")
        GLib.idle_add(self.update_tab_indicator)
        GLib.idle_add(self.chat_history.set_generating, True)
        GLib.idle_add(self.chat_history.begin_streaming_scroll, manual)
        
        # Start creating the message
        self.active_generation_model = self.controller.get_model_for_chat(self.chat)
        if self.active_generation_model.stream_enabled():
            self.streamed_message = ""
            self.curr_label = ""
            self.streaming_label = None
            self.stream_thinking = False
            with self.streaming_lock:
                self.streamed_content = ""
                self._stream_target_content = ""
                self.streaming_pending = False
            GLib.idle_add(
                self.create_streaming_message_label,
                stream_number_variable,
            )
            
        def run_generation():
            for status, data in self.controller.generate_response(
                stream_number_variable, 
                self.update_message,
                chat_id=self._chat_id,
                is_current=lambda: not audio_cancel_event.is_set() and self._chat_id == origin_chat_id,
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
        """Handle completion of one model turn in the generation chain."""
        self._cancel_stream_reveal()
        message_label = data['message']
        prompts = data['prompts']
        response_metadata = data.get('response_metadata')
        response_usage = data.get('usage')
        self.last_generation_time = data['time']
        self.last_token_num = (data['input_tokens'], data['output_tokens'])
        self.last_time_to_first_token = data.get('time_to_first_token')
        self.last_time_to_first_token_no_thinking = data.get('time_to_first_token_no_thinking')
        trim_result = data.get('trim_result')
        if trim_result is not None and hasattr(self, 'context_indicator'):
            self.context_indicator.update_stats(trim_result)
        
        waiting_for_tools = False
        if hasattr(self, "current_streaming_message") and self.current_streaming_message:
            # Streaming was active, finalize the existing widget
            streaming_widget = self.current_streaming_message
            assistant_entry = {
                "User": "Assistant", 
                "Message": message_label, 
                "UUID": streaming_widget.chunk_uuid,
                "Profile": self.controller.newelle_settings.current_profile
            }
            if response_metadata is not None:
                assistant_entry["OpenAIResponse"] = response_metadata
            if response_usage is not None:
                assistant_entry["LLMUsage"] = dict(response_usage)
            self.chat.append(assistant_entry)
            self.chat_history.update_history(self.chat)
            self.add_prompt("\n".join(prompts))
            
            final_message = message_label
            
            def finalize_stream():
                nonlocal waiting_for_tools
                streaming_widget.update_content(final_message, is_streaming=False)
                streaming_widget.finish_streaming()
                waiting_for_tools = streaming_widget.state.get(
                    "has_terminal_command", False
                )
                remove_streaming_row = self.chat_history.finish_compact_message(
                    streaming_widget
                )
                if remove_streaming_row:
                    self.chat_history.remove_message_widget(streaming_widget)
                # Let the final Message render settle, then reconcile this
                # continuation row without rescanning the entire history.
                GLib.idle_add(
                    self.chat_history.prune_compact_message_row,
                    streaming_widget,
                )
                self.chat_history._finalize_message_display(
                    generation_finished=not waiting_for_tools
                )
                self.save_chat()
                
                # Handle deferred tool execution and continuation
                if waiting_for_tools:
                    threads = streaming_widget.state.get("running_threads", [])
                    parallel = self.controller.newelle_settings.parallel_tool_execution
                    current_stream = self.stream_number_variable
                    
                    def wait_and_continue():
                        if not parallel:
                            for t in threads:
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
                            GLib.idle_add(
                                self._finish_generation_chain,
                                message_label,
                                current_stream,
                            )
                    
                    threading.Thread(target=wait_and_continue).start()
                else:
                    GLib.idle_add(self.chat_history.scrolled_chat)
            
            finalize_stream()
            self.current_streaming_message = None
        else:
            # No streaming, standard display
            assistant_index = len(self.chat)
            self.chat_history.show_message(
                message_label,
                False,
                -1,
                False,
                False,
                False,
                "\n".join(prompts),
            )
            if (
                (response_metadata is not None or response_usage is not None)
                and assistant_index < len(self.chat)
                and self.chat[assistant_index].get("User") == "Assistant"
                and self.chat[assistant_index].get("Message") == message_label
            ):
                if response_metadata is not None:
                    self.chat[assistant_index]["OpenAIResponse"] = response_metadata
                if response_usage is not None:
                    self.chat[assistant_index]["LLMUsage"] = dict(response_usage)
                self.save_chat()
        
        if waiting_for_tools:
            return

        self._finish_generation_chain(message_label, stream_number_variable)

    def _finish_generation_chain(self, message_label, stream_number_variable):
        """Mark the full response chain done after all tool continuations."""
        if self.stream_number_variable != stream_number_variable:
            return GLib.SOURCE_REMOVE

        self.chat_history.set_generating(False)
        self.remove_send_button_spinner()
        self.update_tab_indicator()
        self.emit("generation-stopped")

        # Generate suggestions
        self.generate_suggestions()

        # Generate chat name
        if self.controller.newelle_settings.auto_generate_name and len(self.chat) == 2:
            GLib.idle_add(self.generate_chat_name)
        
        # TTS
        tts_thread = None
        if self.tts_enabled:
            message = clean_message_tts(message_label)
            if message.strip() and not message.isspace():
                tts_thread = threading.Thread(
                    target=self.tts.play, args=(message,)
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

        return GLib.SOURCE_REMOVE
            
    def create_streaming_message_label(self, stream_number_variable):
        """Create a label for message streaming."""
        self._cancel_stream_reveal()
        self._stream_reveal_generation = stream_number_variable
        
        next_message_id = len(self.chat)
        tool_group = self.chat_history.get_compact_tool_group(
            next_message_id,
            streaming=True,
        )
        self.current_streaming_message = Message(
            "",
            is_user=False,
            parent_window=self,
            id_message=next_message_id,
            tool_group=tool_group,
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

        with self.streaming_lock:
            has_pending_text = bool(self._stream_target_content)
        if has_pending_text:
            self._start_stream_reveal(stream_number_variable)
        
    def update_message(self, message, stream_number_variable, *args):
        """Update message label when streaming (thread-safe)."""
        if self.stream_number_variable != stream_number_variable:
            return

        with self.streaming_lock:
            self._stream_target_content = message
            if self.streaming_pending:
                return
            self.streaming_pending = True
        GLib.idle_add(self._queue_stream_reveal, stream_number_variable)

    def _queue_stream_reveal(self, stream_number_variable):
        """Coalesce producer updates and start the main-thread reveal loop."""
        if self.stream_number_variable != stream_number_variable:
            with self.streaming_lock:
                self.streaming_pending = False
            return GLib.SOURCE_REMOVE

        self._start_stream_reveal(stream_number_variable)
        return GLib.SOURCE_REMOVE

    def _start_stream_reveal(self, stream_number_variable):
        if (
            self._stream_reveal_source_id is not None
            and self._stream_reveal_generation == stream_number_variable
        ):
            return

        self._cancel_stream_reveal()
        self._stream_reveal_generation = stream_number_variable
        with self.streaming_lock:
            self.streaming_pending = True
        self._stream_reveal_source_id = GLib.timeout_add(
            _STREAM_REVEAL_INTERVAL_MS,
            self._reveal_streaming_text,
            stream_number_variable,
        )

    def _reveal_streaming_text(self, stream_number_variable):
        """Reveal a small, adaptive slice of the latest streamed response."""
        if self.stream_number_variable != stream_number_variable:
            with self.streaming_lock:
                self.streaming_pending = False
            self._stream_reveal_source_id = None
            return GLib.SOURCE_REMOVE

        if self.current_streaming_message is None:
            return GLib.SOURCE_CONTINUE

        with self.streaming_lock:
            target_content = self._stream_target_content
            visible_content = self.streamed_content
            if target_content == visible_content:
                self.streaming_pending = False
                self._stream_reveal_source_id = None
                return GLib.SOURCE_REMOVE

        settings = Gtk.Settings.get_default()
        animations_enabled = (
            settings is None
            or settings.get_property("gtk-enable-animations")
        )

        if not animations_enabled or not self.get_mapped():
            next_content = target_content
        elif target_content.startswith(visible_content):
            remaining = len(target_content) - len(visible_content)
            reveal_count = max(
                1,
                min(
                    _STREAM_REVEAL_MAX_CHARS,
                    (remaining + _STREAM_REVEAL_TARGET_FRAMES - 1)
                    // _STREAM_REVEAL_TARGET_FRAMES,
                ),
            )
            next_content = target_content[:len(visible_content) + reveal_count]
        else:
            # Some handlers revise earlier output instead of only appending.
            # Apply those corrections atomically so the displayed text stays valid.
            next_content = target_content

        with self.streaming_lock:
            self.streamed_content = next_content

        if self.current_streaming_message is not None:
            self.current_streaming_message.update_content(
                next_content,
                is_streaming=True,
            )
            self.chat_history.scrolled_chat()

        return GLib.SOURCE_CONTINUE

    def _cancel_stream_reveal(self):
        if self._stream_reveal_source_id is not None:
            GLib.source_remove(self._stream_reveal_source_id)
            self._stream_reveal_source_id = None
        self._stream_reveal_generation = None
        with self.streaming_lock:
            self.streaming_pending = False
    
    def add_reading_widget(self, documents):
        """Add document reading widget during streaming."""
        d = [doc.replace("file:", "") for doc in documents if doc.startswith("file:")]
        documents = d
        model = getattr(self, "active_generation_model", self.model)
        if model.stream_enabled() and hasattr(self, "current_streaming_message"):
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
        if self.last_time_to_first_token is not None:
            self.chat[-1]["TimeToFirstToken"] = self.last_time_to_first_token
        if self.last_time_to_first_token_no_thinking is not None:
            self.chat[-1]["TimeToFirstTokenNoThinking"] = self.last_time_to_first_token_no_thinking
        
    def reload_message(self, message_id: int):
        """Reload a message in the chat history."""
        if message_id < 0 or message_id >= len(self.chat):
            return
        if self.chat[message_id]["User"] == "Console":
            return

        message_box_index = message_id + 1
        if message_box_index < 0 or message_box_index >= len(self.chat_history.messages_box):
            return

        message_box = self.chat_history.messages_box[message_box_index]
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
        if hasattr(self, "_audio_cancel_event"):
            self._audio_cancel_event.set()
        getattr(self, "active_generation_model", self.model).stop()
        for tool_result in self.active_tool_results:
            tool_result.cancel()
        self.active_tool_results = []
        self.status = True
        self.stream_number_variable += 1

        # Persist the incomplete streamed message to chat so that
        # tool outputs (Console messages) aren't orphaned when the user
        # regenerates after stopping.
        if hasattr(self, 'current_streaming_message') and self.current_streaming_message is not None:
            streamed_text = self.current_streaming_message.message
            if streamed_text and streamed_text.strip():
                self.chat.append({
                    "User": "Assistant",
                    "Message": streamed_text,
                    "UUID": self.current_streaming_message.chunk_uuid,
                    "Profile": self.controller.newelle_settings.current_profile
                })
            self.current_streaming_message = None

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
                name = remove_markdown(name)
                if self._chat_id in self.controller.chats:
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
        self.attachment_mode.set_visible(False)
        self.attachment_mode.set_selected(0)
        self.screen_record_button.set_visible(self.vision_model.supports_video_vision())
        
    def add_file(self, file_path=None, file_data=None):
        """Add a file attachment and update the UI, also generates thumbnail for videos

        Args:
            file_path (): file path for the file
            file_data (): file data for the file
        """
        self.attachment_mode.set_visible(False)
        self.attachment_mode.set_selected(0)
        if file_path is not None:
            if file_path.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
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
                    try:
                        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(frame_data))
                        self.attached_image.set_from_paintable(texture)
                    except Exception:
                        try:
                            loader = GdkPixbuf.PixbufLoader()
                            loader.write(frame_data)
                            loader.close()
                            self.attached_image.set_from_pixbuf(loader.get_pixbuf())
                        except Exception:
                            self.attached_image.set_from_icon_name("video-x-generic")
                else:
                    self.attached_image.set_from_icon_name("video-x-generic")
            elif file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.attached_image.set_from_file(file_path)
            else:
                self.attached_image.set_from_icon_name("text-x-generic")
                self.attachment_mode.set_visible(True)

            self.attached_image_data = file_path
            self.attached_image.set_visible(True)
        elif file_data is not None:
            base64_image = base64.b64encode(file_data).decode("utf-8")
            mime_type = "image/png" if file_data[:8] == b'\x89PNG\r\n\x1a\n' else "image/jpeg"
            self.attached_image_data = f"data:{mime_type};base64,{base64_image}"
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(file_data))
                self.attached_image.set_from_paintable(texture)
            except Exception:
                try:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(file_data)
                    loader.close()
                    self.attached_image.set_from_pixbuf(loader.get_pixbuf())
                except Exception:
                    pass
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
        self.window.start_recording(button, self)

    def set_mic_warning(self):
        """Set mic button to warning state (yellow) when speech is detected."""
        self.mic_button.add_css_class("warning")

    def set_mic_transcribing(self):
        """Set mic button to transcribing state (spinner)."""
        self.mic_button.remove_css_class("warning")
        spinner = Gtk.Spinner(spinning=True)
        self.mic_button.set_child(spinner)

    def set_mic_normal(self):
        """Reset mic button to normal state."""
        self.mic_button.remove_css_class("warning")
        self.mic_button.set_child(None)
        self.mic_button.set_icon_name("audio-input-microphone-symbolic")
        
    def start_screen_recording(self, button):
        """Start screen recording."""
        self.window.start_screen_recording(button, self)
        
    # Bot response (for suggestions)
    def send_bot_response(self, button):
        """Send a bot response suggestion."""
        self.send_button_start_spinner()
        text = button.get_child().get_label()
        self.chat.append({"User": "User", "Message": text})
        self.chat_history.show_message(text, id_message=len(self.chat) - 1, is_user=True)
        
        # Store current profile in chat data
        if self._chat_id in self.controller.chats:
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

    @property 
    def main_path(self):
        return self.window.main_path
