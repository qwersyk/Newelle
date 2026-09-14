from gi.repository import Gtk, GObject, Pango, Gio, GLib, Adw, Gdk
import threading
import time
import uuid
import os
import subprocess
import gettext
from ...utility.system import open_website
from ..widgets import TipsCarousel, SkillWidget
from ...utility.strings import markwon_to_pango
from ...ui.widgets import Message, MultilineEntry, ToolCallsGroupWidget
from ...utility.message_chunk import get_message_chunks

_ = gettext.gettext
SCHEMA_ID = "io.github.qwersyk.Newelle"


class ChatHistory(Gtk.Box):
    __gsignals__ = {
        "focus-input": (GObject.SignalFlags.RUN_LAST, None, ()),
        "branch-requested": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_INT,)),
        "clear-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "continue-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "regenerate-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "stop-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "files-dropped": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, window, chat, chat_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["background", "view"], vexpand=True)
        self.status = True
        self.window = window
        self.chat_id = chat_id
        self.controller = window.controller
        
        # Lazy loading state
        self.lazy_load_enabled = True
        self.lazy_load_batch_size = 10  # Number of messages to load initially and per batch
        self.lazy_load_threshold = 0.1  # Load more when within 10% of top/bottom
        self.lazy_loaded_start = 0  # First loaded message index
        self.lazy_loaded_end = 0  # Last loaded message index (exclusive)
        self.lazy_loading_in_progress = False
        self._initial_load_generation = 0
        self._initial_load_source_id = None
        self.scroll_handler_id = None  # Store scroll handler ID to disconnect when needed
        self.scroll_bounds_handler_id = None
        self._follow_new_content = True
        self._programmatic_scroll = False
        self._last_scroll_value = None
        self._scroll_to_bottom_source_id = None
        self._preamble_row_count = 0  # Number of warning/disclaimer rows at the top

        self.messages_box = []
        self.edit_entries = {}
        self.last_error_box = None
        self._compact_tool_groups = {}
        self._active_compact_tool_group = None
        self._compact_hidden_rows = set()
        # Suggestions vars
        self.message_suggestion_buttons_array = []
        self.message_suggestion_buttons_array_placeholder = []
        # Show history/placeholder
        self.history_block = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_DOWN, transition_duration=300)
        self._add_drag_and_drop()

        # Add history
        self.chat_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._user_scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        self._user_scroll_controller.connect("scroll", self._on_user_scroll)
        self.chat_scroll.add_controller(self._user_scroll_controller)
        self.history_block.add_named(self.chat_scroll, "history")
        
        self.chat_scroll_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chat_list_block = Gtk.ListBox(
            css_classes=["background", "view"]
        )
        self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_scroll_box.append(self.chat_list_block)
        
        # Offers
        self.offers_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.FILL,
            vexpand=True,
            margin_bottom=6,
        )
        self.chat_scroll_box.append(self.offers_entry_block)
        self.chat_scroll.set_child(self.chat_scroll_box)
        
        # Add placeholder
        self.build_placeholder()
        self.history_block.add_named(self.empty_chat_placeholder, "placeholder")
        self.history_block.set_visible_child_name("history" if len(self.chat) > 0 else "placeholder") 
        self.append(self.history_block)
        # Chat controls
        self.chat_controls_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_top=6,
            margin_bottom=6,
        )
        self.append(self.chat_controls_entry_block)

        # Add buttons
        self._build_buttons()

        self.offers = self.controller.newelle_settings.offers
        self.build_offers()

    def show_placeholder(self):
        self.history_block.set_visible_child_name("placeholder")
        self.tips_section.shuffle_tips()

    def hide_placeholder(self):
        self.history_block.set_visible_child_name("history")

    def focus_input(self):
        self.emit("focus-input")

    def set_generating(self, generating: bool):
        self.status = not generating
        self.update_button_text()

    def populate_chat(self):
        self._initial_load_generation += 1
        self._compact_tool_groups = {}
        self._active_compact_tool_group = None
        self._compact_hidden_rows = set()
        self._preamble_row_count = 0
        if not self.controller.newelle_settings.hide_warning:
            if not self.controller.newelle_settings.virtualization:
                self.add_message("WarningNoVirtual")
            else:
                self.add_message("Disclaimer")
            self._preamble_row_count = 1
        total_messages = len(self.chat)
        if self.scroll_handler_id is not None:
            adjustment = self.chat_scroll.get_vadjustment()
            adjustment.disconnect(self.scroll_handler_id)
        if self.scroll_bounds_handler_id is not None:
            adjustment = self.chat_scroll.get_vadjustment()
            adjustment.disconnect(self.scroll_bounds_handler_id)
        adjustment = self.chat_scroll.get_vadjustment()
        self.scroll_handler_id = adjustment.connect("value-changed", self._on_scroll_changed)
        self.scroll_bounds_handler_id = adjustment.connect(
            "notify::upper",
            self._on_scroll_bounds_changed,
        )
        # Lazy load if
        if self.lazy_load_enabled and total_messages > self.lazy_load_batch_size:
            # Load only the last batch_size messages initially
            # Messages are indexed from 0 (oldest) to len-1 (newest)
            nominal_start = max(0, total_messages - self.lazy_load_batch_size)
            start_idx = nominal_start
            if self.controller.newelle_settings.compact_mode:
                # A long tool run can contain more Console/protocol records
                # than the raw lazy-load batch. Include the latest real user
                # turn so its first assistant message and the compact group's
                # true owner are present immediately when switching chats.
                for index in range(total_messages - 1, -1, -1):
                    entry = self.chat[index]
                    if entry.get("User") == "User" and not entry.get(
                        "ToolContext"
                    ):
                        start_idx = min(start_idx, index)
                        break
            self.lazy_loaded_start = start_idx
            self.lazy_loaded_end = total_messages
            if start_idx < nominal_start:
                self._load_message_range_incrementally(start_idx, total_messages)
            else:
                self._load_message_range(start_idx, total_messages)
        else:
            self.lazy_loaded_start = 0
            self.lazy_loaded_end = total_messages
            for i in range(len(self.chat)):
                if self.chat[i]["User"] == "User":
                    self.show_message(self.chat[i]["Message"], True, id_message=i, is_user=True)
                elif self.chat[i]["User"] == "Assistant":
                    self.show_message(self.chat[i]["Message"], True, id_message=i)
                elif self.chat[i]["User"] == "Console" and self.chat[i].get("skill_name"):
                    self._add_skill_message(i)
                elif self.chat[i]["User"] == "Command":
                    text = self.chat[i]["Message"]
                    if text.startswith("/"):
                        parts = text[1:].split(" ", 1)
                        if parts:
                            cmd_name = parts[0].lower()
                            args_str = parts[1] if len(parts) > 1 else ""
                            cmd = self.controller.get_command(cmd_name)
                            if cmd:
                                kwargs = {}
                                if args_str and "properties" in cmd.schema:
                                    for param_name in cmd.schema.get("properties", {}):
                                        if cmd.schema["properties"][param_name]["type"] == "string":
                                            kwargs[param_name] = args_str.strip()
                                            break
                                kwargs["msg_uuid"] = self.chat[i].get("UUID")
                                kwargs["chat_id"] = self.chat_id
                                result = cmd.restore(**kwargs)
                                if result and result.widget is not None:
                                    self.add_message("Command", result.widget, id_message=i, editable=True)
                elif self.chat[i]["User"] in ["File", "Folder"]:
                    self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]))
        GLib.timeout_add(200, self.scrolled_chat)
        GLib.idle_add(self.update_button_text)

    def _load_message_range_incrementally(self, start_idx: int, end_idx: int):
        """Restore an expanded compact turn without blocking a tab switch.

        Restoring a tool message may construct a custom result widget on the
        GTK thread. Loading one visible record per frame keeps long tool runs
        responsive while still reconstructing the complete shared group.
        """
        generation = self._initial_load_generation
        next_index = start_idx
        self.lazy_loading_in_progress = True

        def load_next():
            nonlocal next_index
            if (
                generation != self._initial_load_generation
                or self.get_parent() is None
            ):
                self.lazy_loading_in_progress = False
                self._initial_load_source_id = None
                return GLib.SOURCE_REMOVE

            while next_index < end_idx:
                index = next_index
                next_index += 1
                entry = self.chat[index]
                self._load_message_range(index, index + 1)
                if (
                    entry.get("User")
                    in ("User", "Assistant", "Command", "File", "Folder")
                    or (
                        entry.get("User") == "Console"
                        and entry.get("skill_name")
                    )
                ):
                    break

            if next_index >= end_idx:
                self.lazy_loading_in_progress = False
                self._initial_load_source_id = None
                GLib.idle_add(self.prune_compact_empty_rows)
                GLib.idle_add(self.scrolled_chat)
                return GLib.SOURCE_REMOVE

            self._initial_load_source_id = GLib.timeout_add(16, load_next)
            return GLib.SOURCE_REMOVE

        self._initial_load_source_id = GLib.idle_add(load_next)

    def _assistant_has_tool_calls(self, index):
        if index < 0 or index >= len(self.chat):
            return False
        entry = self.chat[index]
        if entry.get("User") != "Assistant":
            return False
        try:
            return any(
                chunk.type == "tool_call"
                for chunk in get_message_chunks(
                    entry.get("Message", ""), allow_latex=False
                )
            )
        except Exception:
            return False

    def _previous_real_message_index(self, index):
        """Find the prior conversational entry before *index*.

        Console results and synthetic tool-context user entries are execution
        bookkeeping, not boundaries between assistant tool iterations.
        """
        for candidate in range(min(index - 1, len(self.chat) - 1), -1, -1):
            entry = self.chat[candidate]
            if entry.get("User") == "Console":
                continue
            if entry.get("User") == "User" and entry.get("ToolContext"):
                continue
            return candidate
        return None

    def _tool_chain_start(self, index):
        start = index
        previous = self._previous_real_message_index(start)
        while previous is not None and self._assistant_has_tool_calls(previous):
            start = previous
            previous = self._previous_real_message_index(previous)
        return start

    def _message_has_tool_calls(self, message):
        try:
            return any(
                chunk.type == "tool_call"
                for chunk in get_message_chunks(message or "", allow_latex=False)
            )
        except Exception:
            return False

    def get_compact_tool_group(self, id_message, message="", is_user=False, streaming=False):
        """Return the shared group for an assistant tool-call chain."""
        if is_user:
            self._active_compact_tool_group = None
            return None
        if not self.controller.newelle_settings.compact_mode:
            return None
        if streaming and not message:
            return self._active_compact_tool_group
        if not self._message_has_tool_calls(message):
            self._active_compact_tool_group = None
            return None

        chain_start = self._tool_chain_start(id_message)
        group = self._compact_tool_groups.get(chain_start)
        if group is None:
            group = ToolCallsGroupWidget()
            self._compact_tool_groups[chain_start] = group
        self._active_compact_tool_group = group
        return group

    def register_compact_tool_group(self, group, chain_start):
        """Register a group created lazily when a stream first yields a tool."""
        if not self.controller.newelle_settings.compact_mode:
            return
        existing = self._compact_tool_groups.get(chain_start)
        if existing is not None and existing is not group:
            return
        self._compact_tool_groups[chain_start] = group
        self._active_compact_tool_group = group

    def refresh_compact_groups(self):
        """Merge already-rendered continuation messages after a mode toggle."""
        if not self.controller.newelle_settings.compact_mode:
            return
        groups = {}
        messages = sorted(
            self._message_widgets(),
            key=lambda message: (
                message.id_message if message.id_message >= 0 else float("inf")
            ),
        )
        for message in messages:
            if not message._tool_slots_in_order():
                continue
            chain_start = self._tool_chain_start(message.id_message)
            group = groups.get(chain_start)
            if group is None:
                group = message.tool_calls_group or ToolCallsGroupWidget()
                if getattr(group, "owner_message", None) is None:
                    group.owner_message = message
                groups[chain_start] = group
            message.attach_tool_group(group)
        self._compact_tool_groups.update(groups)
        if groups:
            self._active_compact_tool_group = next(reversed(groups.values()))

    def finish_compact_message(self, message):
        """Close a pre-attached chain and report whether its row is disposable."""
        if not self.controller.newelle_settings.compact_mode:
            return False
        group = message.tool_calls_group
        # The live parent is authoritative. Ownership can change while older
        # messages are lazy-loaded or existing rows are regrouped, but a row
        # that currently contains the expander must never be hidden.
        if group is not None and group.get_parent() is message:
            self._active_compact_tool_group = group
            return False
        slots = message._tool_slots_in_order()
        has_outside_content = message._has_content_outside_tool_group()
        if slots:
            self._active_compact_tool_group = group
            return not has_outside_content
        if self._active_compact_tool_group is group:
            self._active_compact_tool_group = None
        return not has_outside_content

    def remove_message_widget(self, message):
        """Remove an empty streaming row while retaining any shared group."""
        row = message.get_ancestor(Gtk.ListBoxRow)
        if row is not None:
            row.set_visible(False)
            self._compact_hidden_rows.add(row)

    def restore_message_widget(self, message):
        """Re-show a row if deferred rendering added visible content to it."""
        row = message.get_ancestor(Gtk.ListBoxRow)
        if row is not None and row in self._compact_hidden_rows:
            row.set_visible(True)
            self._compact_hidden_rows.discard(row)

    def prune_compact_message_row(self, message):
        """Hide one protocol-only assistant row after its final render.

        A streamed continuation can finish before its last ``Message`` idle
        render has moved tool slots/text into the shared group.  In that
        window ``finish_compact_message`` cannot reliably classify the row,
        leaving an empty ListBox row beneath the expander.  Re-check the live
        widgets once the UI queue has drained and hide only rows with no
        content outside the group.  The row containing the live expander is
        always retained.
        """
        if not self.controller.newelle_settings.compact_mode:
            return GLib.SOURCE_REMOVE

        if message.is_user or message.streaming:
            return GLib.SOURCE_REMOVE
        if message._has_content_outside_tool_group():
            self.restore_message_widget(message)
            return GLib.SOURCE_REMOVE

        group = getattr(message, "tool_calls_group", None)
        group_child = (
            group
            if group is not None and group.get_parent() is message
            else None
        )
        if group_child is not None:
            # This is the row that visibly owns the expander, regardless
            # of whether cached ownership has caught up yet.
            self.restore_message_widget(message)
            return GLib.SOURCE_REMOVE
        child = message.get_first_child()
        while child is not None:
            if child.get_visible():
                self.restore_message_widget(message)
                return GLib.SOURCE_REMOVE
            child = child.get_next_sibling()

        slots = message._tool_slots_in_order()
        if not ((group is not None and group.slots) or slots) and str(
            getattr(message, "message", "") or ""
        ).strip():
            self.restore_message_widget(message)
            return GLib.SOURCE_REMOVE

        self.remove_message_widget(message)
        return GLib.SOURCE_REMOVE

    def prune_compact_empty_rows(self):
        """Reconcile all compact rows after regrouping or incremental load."""
        if not self.controller.newelle_settings.compact_mode:
            return GLib.SOURCE_REMOVE
        for message in self._message_widgets():
            self.prune_compact_message_row(message)
        return GLib.SOURCE_REMOVE

    def restore_hidden_compact_rows(self):
        for row in list(self._compact_hidden_rows):
            row.set_visible(True)
        self._compact_hidden_rows.clear()

    def _message_widgets(self):
        roots = list(self.messages_box)
        # The active streaming row is intentionally removed from
        # ``messages_box`` bookkeeping until generation finishes, but it is
        # still a live child of the ListBox and must participate in toggles.
        row = self.chat_list_block.get_first_child()
        while row is not None:
            roots.append(row)
            row = row.get_next_sibling()
        seen = set()
        for root in roots:
            stack = [root]
            while stack:
                widget = stack.pop()
                if isinstance(widget, Message):
                    marker = id(widget)
                    if marker not in seen:
                        seen.add(marker)
                        yield widget
                    continue
                child = widget.get_first_child() if widget is not None else None
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()

    def begin_streaming_scroll(self, force_follow=False):
        """Start a stream, preserving a paused scroll during automatic continuations."""
        adjustment = self.chat_scroll.get_vadjustment()
        if force_follow or self._is_at_bottom(adjustment):
            self._follow_new_content = True
        self.scrolled_chat()
        return GLib.SOURCE_REMOVE

    def scrolled_chat(self):
        """Follow new content only while the user has not scrolled away."""
        if not self._follow_new_content:
            return GLib.SOURCE_REMOVE

        self.chat_scroll.queue_resize()
        if self._scroll_to_bottom_source_id is None:
            self._scroll_to_bottom_source_id = GLib.idle_add(self._do_scroll)
        return GLib.SOURCE_REMOVE

    def _do_scroll(self):
        """Move to the exact bottom without treating it as user input."""
        self._scroll_to_bottom_source_id = None
        if not self._follow_new_content:
            return GLib.SOURCE_REMOVE

        adjustment = self.chat_scroll.get_vadjustment()
        bottom = max(
            adjustment.get_lower(),
            adjustment.get_upper() - adjustment.get_page_size(),
        )
        self._programmatic_scroll = True
        adjustment.set_value(bottom)
        self._last_scroll_value = adjustment.get_value()
        self._programmatic_scroll = False
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _is_at_bottom(adjustment, tolerance=2.0):
        bottom = max(
            adjustment.get_lower(),
            adjustment.get_upper() - adjustment.get_page_size(),
        )
        return bottom - adjustment.get_value() <= tolerance

    def _on_scroll_bounds_changed(self, adjustment, _pspec):
        """Keep following as streamed content increases the scrollable height."""
        if self._follow_new_content:
            self.scrolled_chat()

    def _on_user_scroll(self, _controller, _dx, dy):
        """Pause following as soon as the user wheels or swipes upward."""
        if dy < 0:
            self._pause_auto_scroll()
        return False

    def _pause_auto_scroll(self):
        self._follow_new_content = False
        if self._scroll_to_bottom_source_id is not None:
            GLib.source_remove(self._scroll_to_bottom_source_id)
            self._scroll_to_bottom_source_id = None

    def update_button_text(self):
        """Update clear chat, regenerate message and continue buttons, add offers"""
        for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
        self.chat_stop_button.set_visible(False)
        if self.status:
            if self.chat != []:
                self.button_clear.set_visible(True)
                if (
                    self.chat[-1]["User"] in ["Assistant", "Console"]
                    or self.last_error_box is not None
                ):
                    self.regenerate_message_button.set_visible(True)
                elif self.chat[-1]["User"] in ["Assistant", "Console", "User"]:
                    self.button_continue.set_visible(True)
        else:
            for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            self.chat_stop_button.set_visible(True)
        GLib.idle_add(self.scrolled_chat)

    def _add_drag_and_drop(self):
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.history_block.add_controller(drop_target)
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.history_block.add_controller(drop_target)

    def _on_drop(self, drop_target, value, x, y):
        """Handle drop event and emit files-dropped signal for the window to process."""
        self.emit("files-dropped", value)
        return True

    def _get_parent_window(self):
        """Get the parent Gtk.Window for transient dialogs."""
        if isinstance(self.window, Gtk.Window):
            return self.window
        if hasattr(self.window, 'window'):
            return self.window.window
        return None

    def _on_hide_warning_clicked(self, gesture, n_press, x, y, box):
        """Show a dialog asking the user if they want to hide the chat warning."""
        dialog = Adw.MessageDialog(
            transient_for=self._get_parent_window(),
            heading=_("Hide warning?"),
            body=_("Do you want to hide the warning at the top of the chat? You can re-enable it from the settings."),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("hide", _("Hide"))
        dialog.set_response_appearance("hide", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_hide_warning_response, box)
        dialog.present()

    def _on_hide_warning_response(self, dialog, response, box):
        """Handle the response to the hide warning dialog."""
        if response == "hide":
            self.controller.settings.set_boolean("hide-warning", True)
            self.controller.newelle_settings.hide_warning = True
            # Reload the chat to reflect the new hide_warning setting
            self.show_chat()
        dialog.destroy()

    def send_bot_response(self, button):
        self.window.send_bot_response(button)

    def build_offers(self):
        """Build offers buttons, called by update_settings to update the number of buttons"""
        for text in range(self.offers):
            def create_button():
                button = Gtk.Button(css_classes=["flat"], margin_start=6, margin_end=6)
                label = Gtk.Label(label=str(text), wrap=True, wrap_mode=Pango.WrapMode.CHAR, ellipsize=Pango.EllipsizeMode.END)
                button.set_child(label)
                button.connect("clicked", self.send_bot_response)
                button.set_visible(False)
                return button
            button = create_button()
            button_placeholder = create_button()
            self.offers_entry_block.append(button)
            self.message_suggestion_buttons_array.append(button)
            self.offers_entry_block_placeholder.append(button_placeholder)
            self.message_suggestion_buttons_array_placeholder.append(button_placeholder)
    
    def _build_buttons(self):
        # Stop chat button
        self.chat_stop_button = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Stop"))
        box.append(label)
        self.chat_stop_button.set_child(box)
        self.chat_stop_button.connect("clicked", lambda btn: self.emit("stop-requested"))
        self.chat_stop_button.set_visible(False)

        self.chat_controls_entry_block.append(self.chat_stop_button)
        self.status = True
        # Clear chat button
        self.button_clear = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-clear-all-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Clear"))
        box.append(label)
        self.button_clear.set_child(box)
        self.button_clear.connect("clicked", lambda btn: self.emit("clear-requested"))
        self.button_clear.set_visible(False)
        self.chat_controls_entry_block.append(self.button_clear)

        # Continue button
        self.button_continue = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(
            Gio.ThemedIcon(name="media-seek-forward-symbolic")
        )
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Continue"))
        box.append(label)
        self.button_continue.set_child(box)
        self.button_continue.connect("clicked", lambda btn: self.emit("continue-requested"))
        self.button_continue.set_visible(False)
        self.chat_controls_entry_block.append(self.button_continue)

        # Regenerate message button
        self.regenerate_message_button = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Regenerate"))
        box.append(label)
        self.regenerate_message_button.set_child(box)
        self.regenerate_message_button.connect("clicked", lambda btn: self.emit("regenerate-requested"))
        self.regenerate_message_button.set_visible(False)
        self.chat_controls_entry_block.append(self.regenerate_message_button)

    def build_placeholder(self):
        tips = [
            {"title": _("Ask about a website"), "subtitle": _("Write #https://website.com in chat to ask information about a website"), "on_click": lambda : self.send_bot_response(Gtk.Button(label="#https://github.com/qwersyk/Newelle\nWhat is Newelle?"))},
            {"title": _("Check out our Extensions!"), "subtitle": _("We have a lot of extensions for different things. Check it out!"), "on_click": lambda: self.app.extension_action()},
            {"title": _("Chat with documents!"), "subtitle": _("Add your documents to your documents folder and chat using the information contained in them!"), "on_click": lambda : self.app.settings_action_paged("Memory")},
            {"title": _("Surf the web!"), "subtitle": _("Enable web search to allow the LLM to surf the web and provide up to date answers"), "on_click": lambda : self.app.settings_action_paged("Memory")},
            {"title": _("Mini Window"), "subtitle": _("Ask questions on the fly using the mini window mode"), "on_click": lambda : open_website("https://github.com/qwersyk/Newelle/?tab=readme-ov-file#mini-window-mode")},
            {"title": _("Text to Speech"), "subtitle": _("Newelle supports text-to-speech! Enable it in the settings"), "on_click": lambda : self.app.settings_action_paged("Voice")},
            {"title": _("Keyboard Shortcuts"), "subtitle": _("Control Newelle using Keyboard Shortcuts"), "on_click": lambda : self.app.on_shortcuts_action()},
            {"title": _("Prompt Control"), "subtitle": _("Newelle gives you 100% prompt control. Tune your prompts for your use."), "on_click": lambda : self.app.settings_action_paged("Prompts")},
            {"title": _("Thread Editing"), "subtitle": _("Check the programs and processes you run from Newelle"), "on_click": lambda : self.app.thread_editing_action()},
            {"title": _("Programmable Prompts"), "subtitle": _("You can add dynamic prompts to Newelle, with conditions and probabilities"), "on_click": lambda : open_website("https://github.com/qwersyk/Newelle/wiki/Prompt-variables")},
        ]
        self.empty_chat_placeholder = Gtk.Box(hexpand=True, vexpand=True, orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spacing=20, vexpand=True)    
        application_logo = Gtk.Image(icon_name=SCHEMA_ID)
        application_logo.set_pixel_size(128)
        box.append(application_logo)
        title_label = Gtk.Label(label=_("New Chat"), css_classes=["title-1"])
        box.append(title_label)
        self.tips_section = TipsCarousel(tips, 5)
        box.append(self.tips_section)
        
        # Offers for placeholder
        self.offers_entry_block_placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_bottom=12,
        )
        self.offers_entry_block_placeholder.set_size_request(-1, 40 * self.controller.newelle_settings.offers)
        
        # Use a scrolled window for the placeholder to ensure everything is reachable
        self.empty_chat_placeholder = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        placeholder_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        placeholder_layout.append(box)
        placeholder_layout.append(self.offers_entry_block_placeholder)
        self.empty_chat_placeholder.set_child(placeholder_layout)

    def _finalize_message_display(self, generation_finished=True):
        """Update UI state after message display.

        A model turn can finish rendering while its tool calls are still
        running.  In that case the overall generation is still active and the
        stop controls must remain visible until the tool chain has completed.
        """
        GLib.idle_add(self.update_button_text)
        if generation_finished:
            self.status = True
            self.chat_stop_button.set_visible(False)
    
    # Message display functions 
    def show_message(
        self,
        message_label,
        restore=False,
        id_message=-1,
        is_user=False,
        return_widget=False,
        newelle_error=False,
        prompt: str | None = None,
    ):
        """Show a message in the chat."""
        if id_message == -1:
            id_message = len(self.chat)
        self.hide_placeholder()
        # Handle empty/whitespace messages
        if message_label == " " * len(message_label) and not is_user:
            if not restore:
                self.chat.append({"User": "Assistant", "Message": message_label, "Profile": self.controller.newelle_settings.current_profile})
                self.add_prompt(prompt)
                self._finalize_message_display()
            GLib.idle_add(self.scrolled_chat)
            self.controller.save_chats()
            return None

        # Handle error messages
        if newelle_error:
            if not restore:
                self._finalize_message_display()
            self.last_error_box = self.add_message(
                "Error",
                Gtk.Label(
                    label=markwon_to_pango(message_label),
                    use_markup=True,
                    wrap=True,
                    margin_top=10,
                    margin_end=10,
                    margin_bottom=10,
                    margin_start=10,
                    selectable=True
                ),
            )
            GLib.idle_add(self.scrolled_chat)
            self.controller.save_chats()
            return None

        # Initialize message UUID for assistant messages
        msg_uuid = 0
        if not is_user:
            if not restore:
                msg_uuid = int(uuid.uuid4())
                self.chat.append({"User": "Assistant", "Message": message_label, "UUID": msg_uuid, "Profile": self.controller.newelle_settings.current_profile})
                self.add_prompt(prompt)
            else:
                msg_uuid = self.chat[id_message].get("UUID", 0)

        tool_group = self.get_compact_tool_group(
            id_message,
            message_label,
            is_user=is_user,
        )

        # Create Message widget
        # Note: Message widget acts as the 'box' that was previously built manually
        message_widget = Message(
            message_label, 
            is_user, 
            self, 
            id_message=id_message, 
            chunk_uuid=msg_uuid, 
            restore=restore,
            tool_group=tool_group,
        )

        if not is_user and self.controller.newelle_settings.compact_mode:
            # Queue after Message's render idle so continuation rows can be
            # pruned even when this widget is returned for lazy insertion.
            GLib.idle_add(self.prune_compact_message_row, message_widget)

        if return_widget:
            return message_widget
            
        profile = self.chat[id_message].get("Profile", self.controller.newelle_settings.current_profile) if not is_user else None
        self.add_message("User" if is_user else "Assistant", message_widget, id_message=id_message, editable=True, profile_name=profile)

        if not restore:
            self._finalize_message_display()
            self.controller.save_chats()
            
        return None

    def _add_skill_message(self, id_message):
        """Display a skill activation as an Assistant message with SkillWidget."""
        skill_name = self.chat[id_message].get("skill_name", "")
        skill = None
        if hasattr(self.controller, "skill_manager"):
            skill = self.controller.skill_manager.skills.get(skill_name)
        if skill is not None:
            resource_count = len(self.controller.skill_manager._list_resources(skill.base_dir))
            widget = SkillWidget(skill.name, skill.description, resource_count)
        else:
            widget = SkillWidget(skill_name, "", 0)
        profile = self.chat[id_message].get("Profile", self.controller.newelle_settings.current_profile)
        self.add_message("Assistant", widget, id_message=id_message, editable=True, profile_name=profile)

    def _make_avatar(self, profile_name=None, size=36):
        """Build the assistant's profile avatar (profile picture, initials fallback)."""
        name = profile_name or self.controller.newelle_settings.current_profile
        profile_settings = self.controller.newelle_settings.profile_settings or {}
        picture = profile_settings.get(name, {}).get("picture") if isinstance(profile_settings, dict) else None
        if picture and isinstance(picture, str) and os.path.exists(picture):
            try:
                return Adw.Avatar(
                    custom_image=Gdk.Texture.new_from_filename(picture),
                    text=name,
                    show_initials=True,
                    size=size,
                )
            except Exception:
                pass
        return Adw.Avatar(text=name, show_initials=True, size=size)

    def _is_continuation(self, user_type, id_message, profile_name=None):
        """True when the previous real chat entry is the same sender, so consecutive
        messages can be grouped (avatar/name hidden)."""
        if id_message is None or id_message <= 0 or id_message > len(self.chat):
            return False

        def side(u):
            if u in ("Assistant", "Command"):
                return "assistant"
            if u in ("User", "File", "Folder"):
                return "user"
            return None

        cur_side = side(user_type)
        if cur_side is None:
            return False
        # Walk back past Console/tool-output entries and hidden empty assistant
        # protocol rows. Empty rows are retained in stored history for tool
        # context, but must not suppress the first visible assistant header.
        j = id_message - 1
        while j >= 0:
            entry = self.chat[j]
            if entry.get("User") == "Console":
                j -= 1
                continue
            if entry.get("User") == "Assistant" and not str(
                entry.get("Message", "")
            ).strip():
                j -= 1
                continue
            break
        if j < 0:
            return False
        prev = self.chat[j]
        if side(prev.get("User")) != cur_side:
            return False
        if cur_side == "assistant":
            cur_p = profile_name
            prev_p = prev.get("Profile")
            if cur_p and prev_p and cur_p != prev_p:
                return False
        return True

    def _set_tray_visible(self, toolbar, visible: bool):
        """Set the visibility of the action toolbar/tray while keeping it allocated."""
        if visible:
            toolbar.set_opacity(1.0)
            toolbar.set_can_target(True)
        else:
            toolbar.set_opacity(0.0)
            toolbar.set_can_target(False)

    def _wire_row_hover(self, row, toolbar):
        """Reveal the action toolbar on hover; keep it visible while editing."""
        ev = Gtk.EventControllerMotion.new()

        def _on_enter(_x, _y, _d):
            self._set_tray_visible(toolbar, True)

        def _on_leave(_d):
            if toolbar.get_visible_child_name() != "apply":
                self._set_tray_visible(toolbar, False)

        ev.connect("enter", _on_enter)
        ev.connect("leave", _on_leave)
        row.add_controller(ev)

    def _arrange_message(self, user_type, bubble, profile_name=None):
        """Lay a bubble out as a chat row."""
        toolbar = getattr(bubble, "action_toolbar", None)
        continuation = getattr(bubble, "is_continuation", False)

        # Assistant side: avatar + name + plain bubble (hidden on continuation)
        if user_type in ("Assistant", "Command"):
            inner = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                margin_top=2 if continuation else 8,
                margin_bottom=8,
                margin_start=8,
                margin_end=8,
                halign=Gtk.Align.FILL,
            )
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, halign=Gtk.Align.FILL)
            if continuation:
                # Reserve the avatar column so the text aligns under the previous msg
                spacer = Gtk.Box()
                spacer.set_size_request(36, 1)
                inner.append(spacer)
                col.append(bubble)
            else:
                avatar = self._make_avatar(profile_name)
                avatar.set_valign(Gtk.Align.START)
                name = profile_name or self.controller.newelle_settings.current_profile
                col.append(Gtk.Label(
                    label=name, halign=Gtk.Align.START, xalign=0, css_classes=["bubble-sender"],
                ))
                col.append(bubble)
                inner.append(avatar)
            inner.append(col)
            # Overlay so the action toolbar floats without affecting layout
            row = Gtk.Overlay()
            row.set_child(inner)
            if toolbar is not None:
                toolbar.set_halign(Gtk.Align.END)
                toolbar.set_valign(Gtk.Align.START)
                toolbar.set_margin_top(6)
                toolbar.set_margin_end(8)
                row.add_overlay(toolbar)
                self._wire_row_hover(row, toolbar)
            return row

        if user_type in ("User", "File", "Folder"):
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                margin_top=8,
                margin_bottom=8,
                margin_start=8,
                margin_end=8,
                halign=Gtk.Align.FILL,
            )
            group = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=4,
                halign=Gtk.Align.END,
            )
            bubble.set_halign(Gtk.Align.END)
            if toolbar is not None:
                toolbar.set_valign(Gtk.Align.CENTER)
                group.append(toolbar)
                self._wire_row_hover(row, toolbar)
            group.append(bubble)
            row.append(group)
            return row

        # Status (Done/Error): left-aligned bubble, no avatar
        bubble.set_halign(Gtk.Align.START)
        bubble.set_margin_start(8)
        bubble.set_margin_end(8)
        return bubble

    def add_message(self, user, message=None, id_message=0, editable=False, profile_name=None):
        """Add a message to the chat and return the box

        Args:
            user (): if the message is send by a user
            message (): message label
            id_message (): id of the message
            editable (): if the message is editable
        Returns:
           message box
        """
        box = Gtk.Box(
            css_classes=["bubble"],
            margin_top=6,
            margin_start=0,
            margin_bottom=6,
            margin_end=0,
            halign=Gtk.Align.FILL,
        )
        self.messages_box.append(box)
        # Group consecutive messages from the same sender (hide avatar/name)
        box.is_continuation = self._is_continuation(user, id_message, profile_name)

        # Update lazy_loaded_end when a message is displayed beyond the current range
        if self.lazy_load_enabled:
            if id_message >= self.lazy_loaded_end:
                self.lazy_loaded_end = id_message + 1

        # Create overlay for branch button positioning
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        box.append(overlay)

        # Create content box to hold message content (horizontal layout)
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        overlay.set_child(content_box)

        # Create edit controls
        if editable:
            apply_edit_stack = self.build_edit_box(box, str(id_message), user == "Assistant")
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            box.add_controller(evk)

            self._set_tray_visible(apply_edit_stack, False)
            apply_edit_stack.add_css_class("message-actions")
            # Placed in the row (and hover-wired) by _arrange_message.
            box.action_toolbar = apply_edit_stack

        if user == "User":
            box.set_css_classes(["bubble", "user"])
        if user == "Assistant" or user=="Command":
            # Assistant messages are plain text (no bubble)
            box.set_css_classes([])
        if user == "Done":
            box.set_css_classes(["bubble", "done"])
        if user == "Error":
            box.set_css_classes(["bubble", "failed"])
        if user == "File":
            box.set_css_classes(["bubble", "file"])
        if user == "Folder":
            box.set_css_classes(["bubble", "folder"])
        if user == "WarningNoVirtual":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-warning"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            icon.set_properties(
                margin_top=10, margin_start=20, margin_bottom=10, margin_end=10
            )
            box_warning = Gtk.Box(
                halign=Gtk.Align.CENTER,
                orientation=Gtk.Orientation.HORIZONTAL,
                css_classes=["warning", "heading"],
                tooltip_text=_("Click to hide this warning"),
            )
            box_warning.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
            box_warning.append(icon)

            label = Gtk.Label(
                label=_(
                    "The neural network has access to your computer and any data in this chat and can run commands, be careful, we are not responsible for the neural network. Do not share any sensitive information."
                ),
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
            box_warning.append(label)

            click_gesture = Gtk.GestureClick.new()
            click_gesture.connect("pressed", self._on_hide_warning_clicked, box)
            box_warning.add_controller(click_gesture)

            content_box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["bubble", "message-warning"])
            box.set_margin_start(40)
            box.set_margin_end(40)
        elif user == "Disclaimer":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-info-symbolic"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            icon.set_properties(
                margin_top=10, margin_start=20, margin_bottom=10, margin_end=10
            )
            box_warning = Gtk.Box(
                halign=Gtk.Align.CENTER,
                orientation=Gtk.Orientation.HORIZONTAL,
                css_classes=["heading"],
                tooltip_text=_("Click to hide this warning"),
            )
            box_warning.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
            box_warning.append(icon)

            label = Gtk.Label(
                label=_(
                    "The neural network has access to any data in this chat, be careful, we are not responsible for the neural network. Do not share any sensitive information."
                ),
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
            box_warning.append(label)

            click_gesture = Gtk.GestureClick.new()
            click_gesture.connect("pressed", self._on_hide_warning_clicked, box)
            box_warning.add_controller(click_gesture)

            content_box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["bubble"])
            box.set_margin_start(40)
            box.set_margin_end(40)
        elif message is not None:
            content_box.append(message)
        # Lay the bubble out as a chat row (warnings stay as centered bare bubbles)
        if user in ("User", "Assistant", "Command", "Done", "Error", "File", "Folder"):
            self.chat_list_block.append(self._arrange_message(user, box, profile_name))
        else:
            self.chat_list_block.append(box)
        return box

    def build_edit_box(self, box, id, has_prompt: bool = True):
        """Build the floating action toolbar for a message.

        Returns a Gtk.Stack showing the action buttons ("edit") normally and
        apply/cancel ("apply") while the message is being edited.
        """
        apply_edit_stack = Gtk.Stack()
        # Size to the visible child so the toolbar shrinks to the apply/cancel
        # buttons while editing instead of keeping the full action-row width.
        apply_edit_stack.set_hhomogeneous(False)
        apply_edit_stack.set_vhomogeneous(False)

        # Apply / cancel (shown while editing)
        apply_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        apply_button = Gtk.Button(
            icon_name="check-plain-symbolic",
            css_classes=["flat", "success"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        apply_button.set_tooltip_text(_("Apply"))
        apply_button.connect("clicked", self.apply_edit_message, box, apply_edit_stack)
        cancel_button = Gtk.Button(
            icon_name="circle-crossed-symbolic",
            css_classes=["flat", "destructive-action"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        cancel_button.set_tooltip_text(_("Cancel"))
        cancel_button.connect("clicked", self.cancel_edit_message, box, apply_edit_stack)
        apply_box.append(apply_button)
        apply_box.append(cancel_button)

        # Action buttons (shown on hover) - single horizontal row
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        edit_button = Gtk.Button(
            icon_name="document-edit-symbolic",
            css_classes=["flat", "success"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        edit_button.set_tooltip_text(_("Edit"))
        edit_button.connect("clicked", self.edit_message, None, None, None, box, apply_edit_stack)
        copy_button = Gtk.Button(
            icon_name="edit-copy-symbolic",
            css_classes=["flat", "accent"],
            valign=Gtk.Align.CENTER,
        )
        copy_button.set_tooltip_text(_("Copy"))
        copy_button.connect("clicked", self.copy_message, int(id))
        actions.append(edit_button)
        actions.append(copy_button)
        if has_prompt:
            info_button = Gtk.Button(
                icon_name="question-round-outline-symbolic",
                css_classes=["flat"],
                valign=Gtk.Align.CENTER,
            )
            info_button.set_tooltip_text(_("Show prompt"))
            info_button.connect("clicked", self.show_prompt, int(id))
            actions.append(info_button)
        branch_button = Gtk.Button(
            icon_name="branch-symbolic",
            css_classes=["flat", "warning"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        branch_button.set_tooltip_text(_("Branch chat"))
        branch_button.connect("clicked", lambda btn: self.emit("branch-requested", int(id)))
        actions.append(branch_button)
        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        remove_button.set_tooltip_text(_("Delete"))
        remove_button.connect("clicked", self.delete_message, box)
        actions.append(remove_button)

        apply_edit_stack.add_named(apply_box, "apply")
        apply_edit_stack.add_named(actions, "edit")
        apply_edit_stack.set_visible_child_name("edit")
        return apply_edit_stack



    def apply_edit_message(self, gesture, box: Gtk.Box, apply_edit_stack: Gtk.Stack):
        """Apply edit for a message

        Args:
            gesture (): widget with the id of the message to edit as name
            box: box of the message
            apply_edit_stack: stack with the edit controls
        """
        entry = self.edit_entries[int(gesture.get_name())]
        self.focus_input()
        # Delete message
        if entry.get_text() == "":
            self.delete_message(gesture, box)
            return

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        apply_edit_stack.set_visible_child_name("edit")
        self.chat[int(gesture.get_name())]["Message"] = entry.get_text()
        self.controller.save_chats()
        content_box.remove(entry)
        content_box.append(
            self.show_message(
                entry.get_text(),
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )
        del self.edit_entries[int(gesture.get_name())]


    def cancel_edit_message(self, gesture, box: Gtk.Box, apply_edit_stack: Gtk.Stack):
        """Restore the old message

        Args:
            gesture (): widget with the id of the message to edit as name
            box: box of the message
            apply_edit_stack: stack with the edit controls
        """
        entry = self.edit_entries[int(gesture.get_name())]
        self.focus_input()

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        apply_edit_stack.set_visible_child_name("edit")
        content_box.remove(entry)
        content_box.append(
            self.show_message(
                self.chat[int(gesture.get_name())]["Message"],
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )
        del self.edit_entries[int(gesture.get_name())]

    def delete_message(self, gesture, box):
        """Delete a message from the chat

        Args:
            gesture (): widget with the id of the message to edit as name
            box (): box of the message
        """
        idx = int(gesture.get_name())
        if idx < len(self.chat):
            del self.chat[idx]
        
        # Also delete subsequent Console messages
        while idx < len(self.chat) and self.chat[idx].get("User") == "Console":
            del self.chat[idx]

        try:
            # Bubbles are wrapped in an avatar row inside the ListBoxRow
            self.chat_list_block.remove(box.get_ancestor(Gtk.ListBoxRow))
            self.messages_box.remove(box)
        except Exception:
            pass
        self.controller.save_chats()
        self.show_chat()

    def add_prompt(self, prompt):
        """Store prompt text on the most recently appended chat entry."""
        if prompt is not None and self.chat:
            self.chat[-1]["Prompt"] = prompt

    def show_prompt(self, button, id):
        """Show a prompt

        Args:
            id (): id of the prompt to show
        """
        # Retrieve prompt data
        prompt_data = self.chat[id]
        prompt_text = prompt_data.get("Prompt", "")
        usage = prompt_data.get("LLMUsage") or {}
        input_tokens = usage.get("input_tokens", prompt_data.get("InputTokens", 0))
        output_tokens = usage.get("output_tokens", prompt_data.get("OutputTokens", 0))
        elapsed = prompt_data.get("enlapsed", 0.0)
        time_to_first_token = prompt_data.get("TimeToFirstToken")
        time_to_first_token_no_thinking = prompt_data.get("TimeToFirstTokenNoThinking")

        speed = 0.0
        if elapsed > 0:
            speed = output_tokens / elapsed

        dialog = Adw.Dialog(can_close=True)
        dialog.set_title(_("Prompt Details"))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(
            Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=True)
        )

        scroll = Gtk.ScrolledWindow(propagate_natural_width=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        
        clamp = Adw.Clamp(maximum_size=600, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        scroll.set_child(clamp)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        clamp.set_child(inner_box)

        # Statistics
        stats_group = Adw.PreferencesGroup(title=_("Statistics"))
        inner_box.append(stats_group)

        row_input = Adw.ActionRow(title=_("Input Tokens"), subtitle=str(input_tokens))
        stats_group.add(row_input)

        row_output = Adw.ActionRow(title=_("Output Tokens"), subtitle=str(output_tokens))
        stats_group.add(row_output)

        for key, title in (
            ("total_tokens", _("Total Tokens")),
            ("cache_read_tokens", _("Cached Input Tokens")),
            ("cache_write_tokens", _("Cache Write Tokens")),
            ("reasoning_tokens", _("Reasoning Tokens")),
            ("input_audio_tokens", _("Input Audio Tokens")),
            ("output_audio_tokens", _("Output Audio Tokens")),
            ("tool_input_tokens", _("Tool Input Tokens")),
            ("accepted_prediction_tokens", _("Accepted Prediction Tokens")),
            ("rejected_prediction_tokens", _("Rejected Prediction Tokens")),
        ):
            if usage.get(key) is not None:
                stats_group.add(Adw.ActionRow(title=title, subtitle=str(usage[key])))

        if time_to_first_token is not None:
            row_ttft = Adw.ActionRow(
                title=_("Time to First Token"),
                subtitle=f"{time_to_first_token:.2f} s",
            )
            stats_group.add(row_ttft)

        if time_to_first_token_no_thinking is not None:
            row_ttft_no_thinking = Adw.ActionRow(
                title=_("Time to First Token (without thinking)"),
                subtitle=f"{time_to_first_token_no_thinking:.2f} s",
            )
            stats_group.add(row_ttft_no_thinking)

        row_speed = Adw.ActionRow(title=_("Generation Speed"), subtitle=f"{speed:.2f} tokens/s")
        stats_group.add(row_speed)

        # Prompt
        prompt_group = Adw.PreferencesGroup(title=_("Prompt"))
        inner_box.append(prompt_group)

        prompt_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        prompt_card.add_css_class("card")
        
        label = Gtk.Label(
            label=prompt_text,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD,
            selectable=True,
            halign=Gtk.Align.START,
            xalign=0,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12
        )
        prompt_card.append(label)
        prompt_group.add(prompt_card)

        content.append(scroll)
        dialog.set_child(content)
        dialog.set_content_width(500)
        dialog.set_content_height(600)
        dialog.present()

    def copy_message(self, button, id):
        """Copy a message

        Args:
            id (): id of the message to copy
        """
        display = Gdk.Display.get_default()
        if display is None or len(self.chat) <= id:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(
            Gdk.ContentProvider.new_for_value(self.chat[id]["Message"])
        )
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def edit_message(
        self, gesture, data, x, y, box: Gtk.Box, apply_edit_stack: Gtk.Stack
    ):
        """Edit message on right click or button click

        Args:
            gesture (): widget with the id of the message to edit as name
            data (): ignored
            x (): ignored
            y (): ignored
            box: box of the message
            apply_edit_stack: stack with the edit controls

        Returns:

        """
        if not self.status:
            self.window.notification_block.add_toast(
                Adw.Toast(
                    title=_("You can't edit a message while the program is running."),
                    timeout=2,
                )
            )
            return False

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        old_message = content_box.get_last_child()
        if old_message is None:
            return

        entry = MultilineEntry(not self.controller.newelle_settings.send_on_enter)
        self.edit_entries[int(gesture.get_name())] = entry
        # Infer size from the size of the old message
        wmax = old_message.get_size(Gtk.Orientation.HORIZONTAL)
        hmax = old_message.get_size(Gtk.Orientation.VERTICAL)
        # Create the entry to edit the message
        entry.set_text(self.chat[int(gesture.get_name())]["Message"])
        entry.set_margin_end(10)
        entry.set_margin_top(10)
        entry.set_margin_start(10)
        entry.set_margin_bottom(10)
        # Size the editor to the old message, minus the entry's own margins
        # (10px each side) so it fits the original area without overflowing.
        # Enforce a minimum so short messages still give a usable editor.
        entry.set_size_request(max(400, wmax - 20), max(60, hmax - 20))
        # Change the stack to edit controls and reveal the floating toolbar
        apply_edit_stack.set_visible_child_name("apply")
        self._set_tray_visible(apply_edit_stack, True)
        entry.set_on_enter(
            lambda entry: self.apply_edit_message(gesture, box, apply_edit_stack)
        )
        content_box.remove(old_message)
        content_box.append(entry)

    def _wrap_message_box(self, user_type: str, content_box, id_message: int, editable: bool, profile_name=None):
        """Wrap a content box in the message wrapper (same logic as add_message)"""
        wrapper_box = Gtk.Box(
            css_classes=["bubble"],
            margin_top=6,
            margin_start=0,
            margin_bottom=6,
            margin_end=0,
            halign=Gtk.Align.FILL,
        )
        # Group consecutive messages from the same sender (hide avatar/name)
        wrapper_box.is_continuation = self._is_continuation(user_type, id_message, profile_name)

        # Create overlay for branch button positioning
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        wrapper_box.append(overlay)

        # Create content box to hold message content (horizontal layout)
        inner_content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        overlay.set_child(inner_content_box)

        # Create edit controls if editable
        if editable:
            apply_edit_stack = self.build_edit_box(wrapper_box, str(id_message))
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, wrapper_box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            wrapper_box.add_controller(evk)

            self._set_tray_visible(apply_edit_stack, False)
            apply_edit_stack.add_css_class("message-actions")
            # Placed in the row (and hover-wired) by _arrange_message.
            wrapper_box.action_toolbar = apply_edit_stack

        # Sender name removed; bubbles are identified by the avatar added below.
        if user_type == "User":
            wrapper_box.set_css_classes(["bubble", "user"])
        elif user_type == "Assistant":
            # Assistant messages are plain text (no bubble)
            wrapper_box.set_css_classes([])
        elif user_type == "File":
            wrapper_box.set_css_classes(["bubble", "file"])
        elif user_type == "Folder":
            wrapper_box.set_css_classes(["bubble", "folder"])

        # Add content
        inner_content_box.append(content_box)

        return self._arrange_message(user_type, wrapper_box, profile_name)
    def _load_message_range(self, start_idx: int, end_idx: int):
        """Load messages in the specified range (start_idx inclusive, end_idx exclusive)"""
        for i in range(start_idx, end_idx):
            if self.chat[i]["User"] == "User":
                self.show_message(
                    self.chat[i]["Message"], True, id_message=i, is_user=True
                )
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"], True, id_message=i)
            elif self.chat[i]["User"] == "Console" and self.chat[i].get("skill_name"):
                self._add_skill_message(i)
            elif self.chat[i]["User"] == "Command":
                text = self.chat[i]["Message"]
                if text.startswith("/"):
                    parts = text[1:].split(" ", 1)
                    if parts:
                        cmd_name = parts[0].lower()
                        args_str = parts[1] if len(parts) > 1 else ""
                        cmd = self.controller.get_command(cmd_name)
                        if cmd:
                            kwargs = {}
                            if args_str and "properties" in cmd.schema:
                                for param_name in cmd.schema.get("properties", {}):
                                    if cmd.schema["properties"][param_name]["type"] == "string":
                                        kwargs[param_name] = args_str.strip()
                                        break
                            kwargs["msg_uuid"] = self.chat[i].get("UUID")
                            kwargs["chat_id"] = self.chat_id
                            result = cmd.restore(**kwargs)
                            if result and result.widget is not None:
                                self.add_message("Command", result.widget, id_message=i, editable=True)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(
                    self.chat[i]["User"],
                    self.get_file_button(
                        self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]
                    ),
                )
    
    def _on_scroll_changed(self, adjustment):
        """Track user scroll intent and trigger lazy loading of messages."""
        value = adjustment.get_value()
        at_bottom = self._is_at_bottom(adjustment)
        moved_up = (
            self._last_scroll_value is not None
            and value < self._last_scroll_value - 1.0
        )
        if self._programmatic_scroll:
            self._last_scroll_value = value
        elif moved_up and (not at_bottom or not self._follow_new_content):
            self._pause_auto_scroll()
        elif at_bottom:
            self._follow_new_content = True
        self._last_scroll_value = value

        if not self.lazy_load_enabled or self.lazy_loading_in_progress:
            return
        
        if len(self.chat) <= self.lazy_load_batch_size:
            return  # No lazy loading needed for short chats
        
        lower = adjustment.get_lower()
        upper = adjustment.get_upper()
        page_size = adjustment.get_page_size()
        
        # Calculate scroll position (0 = top, 1 = bottom)
        if upper - lower - page_size <= 0:
            return
        
        scroll_position = (value - lower) / (upper - lower - page_size)
        
        # Load older messages when scrolling near the top
        if scroll_position < self.lazy_load_threshold and self.lazy_loaded_start > 0:
            self._load_older_messages()
        
        # Load newer messages when scrolling near the bottom (shouldn't happen often since we start at bottom)
        if scroll_position > (1 - self.lazy_load_threshold) and self.lazy_loaded_end < len(self.chat):
            self._load_newer_messages()

    def _load_older_messages(self):
        """Load older messages (lower indices) when user scrolls up"""
        if self.lazy_loading_in_progress or self.lazy_loaded_start <= 0:
            return
        
        self.lazy_loading_in_progress = True
        
        # Calculate how many messages to load
        load_count = min(self.lazy_load_batch_size, self.lazy_loaded_start)
        new_start = max(0, self.lazy_loaded_start - load_count)
        
        # Store current scroll position to restore it after loading
        adjustment = self.chat_scroll.get_vadjustment()
        current_value = adjustment.get_value()
        current_upper = adjustment.get_upper()
        
        # Insert after any preamble rows (warning/disclaimer) that were added
        insert_position = self._preamble_row_count
        
        # Load messages and create widgets
        new_messages_box_items = []
        new_rows = []
        
        for i in range(new_start, self.lazy_loaded_start):
            # Create message content box using show_message with return_widget=True
            if self.chat[i]["User"] == "User":
                content_box = self.show_message(
                    self.chat[i]["Message"], True, id_message=i, is_user=True, return_widget=True
                )
            elif self.chat[i]["User"] == "Assistant":
                content_box = self.show_message(
                    self.chat[i]["Message"], True, id_message=i, return_widget=True
                )
            elif self.chat[i]["User"] == "Console" and self.chat[i].get("skill_name"):
                skill_name = self.chat[i].get("skill_name", "")
                skill = None
                if hasattr(self.controller, "skill_manager"):
                    skill = self.controller.skill_manager.skills.get(skill_name)
                if skill is not None:
                    resource_count = len(self.controller.skill_manager._list_resources(skill.base_dir))
                    content_box = SkillWidget(skill.name, skill.description, resource_count)
                else:
                    content_box = SkillWidget(skill_name, "", 0)
                profile = self.chat[i].get("Profile", self.controller.newelle_settings.current_profile)
                wrapper_box = self._wrap_message_box("Assistant", content_box, i, editable=True, profile_name=profile)
                new_messages_box_items.append(wrapper_box)
                row = Gtk.ListBoxRow()
                row.set_child(wrapper_box)
                new_rows.append(row)
                continue
            elif self.chat[i]["User"] in ["File", "Folder"]:
                # For file/folder messages, create the wrapper box manually
                content_box = self._create_file_message_wrapper(i)
            else:
                continue
            
            if content_box is None:
                continue
            
            # Wrap in the message box (same as add_message does)
            profile = self.chat[i].get("Profile") if self.chat[i]["User"] == "Assistant" else None
            wrapper_box = self._wrap_message_box(
                self.chat[i]["User"], content_box, i, editable=True, profile_name=profile
            )
            
            new_messages_box_items.append(wrapper_box)
            row = Gtk.ListBoxRow()
            row.set_child(wrapper_box)
            new_rows.append(row)
        
        # Insert rows at the correct position
        for idx, row in enumerate(new_rows):
            self.chat_list_block.insert(row, insert_position + idx)
        
        # Prepend to messages_box to maintain order
        for box in reversed(new_messages_box_items):
            self.messages_box.insert(0, box)
        
        self.lazy_loaded_start = new_start
        
        # Restore scroll position (adjust for new content height)
        GLib.idle_add(lambda: self._restore_scroll_position(current_value, current_upper))
        self.lazy_loading_in_progress = False
    
    def _load_newer_messages(self):
        """Load newer messages (higher indices) when user scrolls down"""
        if self.lazy_loading_in_progress or self.lazy_loaded_end >= len(self.chat):
            return
        
        self.lazy_loading_in_progress = True
        
        # Calculate how many messages to load
        load_count = min(self.lazy_load_batch_size, len(self.chat) - self.lazy_loaded_end)
        new_end = min(len(self.chat), self.lazy_loaded_end + load_count)
        
        # Load messages and append to list
        self._load_message_range(self.lazy_loaded_end, new_end)
        
        self.lazy_loaded_end = new_end
        self.lazy_loading_in_progress = False 
    # File button
    def get_file_button(self, path):
        """Get the button for the file

        Args:
            path (): path of the file

        Returns:
           the button for the file
        """
        if path[0:2] == "./":
            path = self.window.main_path + path[1 : len(path)]
        path = os.path.expanduser(os.path.normpath(path))
        button = Gtk.Button(
            css_classes=["flat"],
            margin_top=5,
            margin_start=5,
            margin_bottom=5,
            margin_end=5,
        )
        button.connect("clicked", self.run_file_on_button_click)
        button.set_name(path)
        box = Gtk.Box()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        file_name = path.split("/")[-1]
        if os.path.exists(path):
            if os.path.isdir(path):
                name = "folder"
            else:
                if file_name[len(file_name) - 4 : len(file_name)] in [".png", ".jpg"]:
                    name = "image-x-generic"
                else:
                    name = "text-x-generic"
        else:
            name = "image-missing"
        icon = Gtk.Image(icon_name=name)
        icon.set_css_classes(["large"])
        box.append(icon)
        box.append(vbox)
        vbox.set_size_request(250, -1)
        vbox.append(
            Gtk.Label(
                label=path.split("/")[-1],
                css_classes=["title-3"],
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
        )
        vbox.append(
            Gtk.Label(
                label="/".join(path.split("/")[0:-1]),
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
        )
        button.set_child(box)
        return button

    def run_file_on_button_click(self, button, *a):
        """Opens the file when the file button is clicked

        Args:
            button ():
            *a:
        """
        file_path = os.path.expanduser(button.get_name())
        if not os.path.isabs(file_path):
            file_path = os.path.join(os.path.expanduser(self.window.main_path), file_path)
        file_path = os.path.normpath(file_path)

        if os.path.exists(file_path):
            if os.path.isdir(file_path):
                self.window.window.ui_controller.new_explorer_tab(file_path, False)
            else:
                subprocess.run(["xdg-open", file_path])
        else:
            self.window.notification_block.add_toast(
                Adw.Toast(title=_("File not found"), timeout=2)
            )

    def _restore_scroll_position(self, old_value: float, old_upper: float):
        """Restore scroll position after loading older messages"""
        adjustment = self.chat_scroll.get_vadjustment()
        new_upper = adjustment.get_upper()
        new_lower = adjustment.get_lower()
        page_size = adjustment.get_page_size()
        
        # Calculate the difference in content height
        height_diff = new_upper - old_upper
        
        # Adjust scroll position to maintain visual position
        new_value = old_value + height_diff
        new_value = max(new_lower, min(new_value, new_upper - page_size))
        
        adjustment.set_value(new_value)

    def _create_file_message_wrapper(self, message_idx: int):
        """Create a file/folder message wrapper box"""
        return self.get_file_button(
            self.chat[message_idx]["Message"][1 : len(self.chat[message_idx]["Message"])]
        )

    def show_chat(self):
        """Reload and display all messages from the chat"""
        self._initial_load_generation += 1
        # Clear existing messages from UI
        self.chat_list_block.remove_all()
        self.messages_box.clear()
        self.last_error_box = None
        # Groups contain live Message/slot widgets, so never reuse them across
        # a full rebuild (for example after stopping or restoring a chat).
        self._compact_tool_groups = {}
        self._active_compact_tool_group = None
        self._compact_hidden_rows = set()
        if len(self.chat) == 0:
            self.show_placeholder()
        else:
            self.hide_placeholder()
        # Add warning or disclaimer first (matching populate_chat behavior)
        self._preamble_row_count = 0
        if not self.controller.newelle_settings.hide_warning:
            if not self.controller.newelle_settings.virtualization:
                self.add_message("WarningNoVirtual")
            else:
                self.add_message("Disclaimer")
            self._preamble_row_count = 1

        # Re-populate the chat with all messages
        for i in range(len(self.chat)):
            if self.chat[i]["User"] == "User":
                self.show_message(self.chat[i]["Message"], True, id_message=i, is_user=True)
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"], True, id_message=i)
            elif self.chat[i]["User"] == "Console" and self.chat[i].get("skill_name"):
                self._add_skill_message(i)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]))
            elif self.chat[i]["User"] == "Command":
                cmd_name = self.chat[i]["Message"]
                cmd = self.controller.get_command(cmd_name)
                if cmd is not None:
                    if cmd.restore is not None:
                        r = cmd.restore()
                        if r.widget is not None:
                            self.add_message("Command", r.widget)
                        
        # Reset lazy loading state
        total_messages = len(self.chat)
        if self.lazy_load_enabled and total_messages > self.lazy_load_batch_size:
            self.lazy_loaded_start = max(0, total_messages - self.lazy_load_batch_size)
            self.lazy_loaded_end = total_messages
        else:
            self.lazy_loaded_start = 0
            self.lazy_loaded_end = total_messages

        # Update UI state
        GLib.idle_add(self.scrolled_chat)
        GLib.idle_add(self.update_button_text)

    def update_history(self, chat):
        self.chat = chat

    def populate_suggestions(self, suggestions):
        """Update the UI with the generated suggestions"""
        i = 0
        offers_count = self.controller.newelle_settings.offers
        # Convert to tuple to remove duplicates
        for suggestion in tuple(suggestions):
            if i + 1 > offers_count:
                break
            else:
                message = suggestion.replace("\n", "")
                if i < len(self.message_suggestion_buttons_array):
                    btn = self.message_suggestion_buttons_array[i]
                    btn.get_child().set_label(message)
                    btn.set_visible(True)
                if i < len(self.message_suggestion_buttons_array_placeholder):
                    btn_placeholder = self.message_suggestion_buttons_array_placeholder[i]
                    btn_placeholder.get_child().set_label(message)
                    btn_placeholder.set_visible(True)
                GLib.idle_add(self.scrolled_chat)
            i += 1
        self.chat_stop_button.set_visible(False)

    def has_suggestions(self):
        """Check if any suggestions are currently visible"""
        for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
            if btn.get_visible():
                return True
        return False
        GLib.idle_add(self.scrolled_chat)

    def update_chat(self, chat, chat_id):
        """Update the chat history to display a different chat.
        
        Args:
            chat: The new chat data
            chat_id: The new chat ID
        """
        self.chat_id = chat_id
        # Clear existing messages
        self._clear_messages()
        # Repopulate with new chat
        self.populate_chat()
        # Update the stack to show history or placeholder
        self.history_block.set_visible_child_name("history" if len(chat) > 0 else "placeholder")
    
    def _clear_messages(self):
        """Clear all message widgets from the chat list."""
        # Remove all children except first (disclaimer/warning)
        while True:
            child = self.chat_list_block.get_last_child()
            if child is None:
                break
            self.chat_list_block.remove(child)
        self.messages_box = []
        self.edit_entries = {}
        self._compact_hidden_rows = set()
        self.lazy_loaded_start = 0
        self.lazy_loaded_end = 0

    @property
    def chat(self):
        return self.window.chat

    @chat.setter
    def chat(self, value):
        self.window.chat = value
    
    @property
    def app(self):
        """Get the application instance, works with both MainWindow and ChatTab parent"""
        # If window is ChatTab, go through window.window to get MainWindow
        if hasattr(self.window, 'window'):
            return self.window.window.app
        return self.window.app
