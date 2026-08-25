import threading
import uuid
import inspect
import base64
import json
import os
import re
import tempfile
import socket
from gi.repository import Gtk, GLib, Pango, GdkPixbuf, Gio, Gdk

from ...utility.message_chunk import get_message_chunks, MessageChunk
from ...utility.source_attribution import CitationSource, extract_source_section
from ...utility.strings import markwon_to_pango, remove_thinking_blocks, simple_markdown_to_pango, quote_string
from pylatexenc.latex2text import LatexNodes2Text

from .copybox import CopyBox
from .thinking import ThinkingWidget
from .latex import DisplayLatex, InlineLatex


def _display_latex_base_size(zoom: int) -> int:
    return max(10, int(16 * zoom / 100))


def _inline_latex_size(zoom: int) -> int:
    return int(5 + (zoom / 100 * 4))
from .barchart import BarChartBox
from .markuptextview import MarkupTextView
from .tool import ToolWidget, ToolCallSlot, ToolCallsGroupWidget
from .sources import SourceChip, SourcesButton
from ...tools import ToolResult
from ...ui import apply_css_to_widget, load_image_with_callback


_STREAM_FADE_DURATION_US = 140_000
_STREAM_FADE_START_ALPHA = 0.32
_CITATION_MARKER_PATTERN = re.compile(r'\[(\d+)\](?!\s*\()')
_CITATION_PROTECTED_MARKDOWN_PATTERN = re.compile(r'(`[^`\n]*`|\[[^\]]+\]\([^)]+\))')


class Message(Gtk.Box):
    def __init__(self, message: str, is_user: bool, parent_window, id_message: int = -1, chunk_uuid = None, restore=False, tool_group=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.message = message
        self.is_user = is_user
        self.parent_window = parent_window
        self.controller = parent_window.controller
        self.id_message = id_message
        self.chunk_uuid = chunk_uuid if chunk_uuid else (uuid.uuid4().int if not restore else 0)
        self.restore = restore
        self.thinking_widget = None
        self.pending_execution_copyboxes = set()
        self.citation_sources = []
        self.citation_sources_by_number = {}
        self.sources_button = None
        self._copybox_auto_send_sent = False
        self._tracked_copyboxes_seen = 0
        self.compact_mode = bool(
            getattr(self.controller.newelle_settings, "compact_mode", False)
        )
        self.tool_calls_group = tool_group
        self._compact_moved_widgets = {}
        if self.tool_calls_group is not None:
            if getattr(self.tool_calls_group, "owner_message", None) is None:
                self.tool_calls_group.owner_message = self
        # State tracking
        self.widgets_map = [] # List of tuples (chunk_type, widget, chunk_data)
        self.streaming = False
        self.state = {
            "codeblock_id": -1,
            "id_message": id_message,
            "original_id": id_message,
            "editable": True,
            "has_terminal_command": False,
            "running_threads": [],
            "tool_call_counter": 0,
            "should_continue": False,
        }
        
        # Styling: inner spacing is provided by the .bubble padding in chat_history;
        # keep margins at zero to avoid double-spacing with the bubble padding.
        self.set_margin_top(0)
        self.set_margin_start(0)
        self.set_margin_bottom(0)
        self.set_margin_end(0)
        # Fill the available width so assistant text wraps to the full row width.
        self.set_hexpand(True)

        if is_user:
            self.add_css_class("user-message")
        else:
            self.add_css_class("assistant-message")
            
        # Initial render
        self.update_content(message)

    def update_content(self, message: str, is_streaming: bool = False):
        """Update the message content safely from any thread."""
        self.message = message
        self.streaming = is_streaming
        self._render_serial = getattr(self, '_render_serial', 0) + 1
        GLib.idle_add(self._ui_sync_content, message, self._render_serial)

    def _ui_sync_content(self, message: str, serial: int = -1):
        """Internal method to synchronize UI (Main Thread only)."""
        if serial != getattr(self, '_render_serial', 0):
            return False

        # Chat histories are populated before their tab is necessarily rooted
        # in a display. GTK widgets can safely build their child hierarchy in
        # that state; dropping this render would otherwise leave the message
        # empty until a full chat reload schedules another one.

        render_message = message
        sources = []
        if not self.is_user and not self.streaming:
            render_message, sources = extract_source_section(message)
        self.citation_sources = sources
        self.citation_sources_by_number = {source.number: source for source in sources}
        if not sources:
            self.sources_button = None

        chunks = get_message_chunks(render_message, allow_latex=self.controller.newelle_settings.display_latex)
        if sources:
            signature = "\n".join(f"{source.number}:{source.raw}" for source in sources)
            chunks.append(MessageChunk(type="sources", text=signature))
        current_widget_idx = 0
        temp_state = self.state.copy()
        temp_state["codeblock_id"] = -1 
        
        for chunk in chunks:
            if current_widget_idx < len(self.widgets_map):
                w_type, widget, w_data = self.widgets_map[current_widget_idx]
                if self._can_update_widget(w_type, widget, chunk):
                    self._update_widget(widget, w_type, chunk)
                    self.widgets_map[current_widget_idx] = (chunk.type, widget, chunk)
                    self._simulate_state_update(chunk, temp_state)
                    current_widget_idx += 1
                    continue
                else:
                    self._remove_widgets_from(current_widget_idx)
            
            self._process_chunk(chunk, self, temp_state, self.restore, self.is_user, self.chunk_uuid)
            current_widget_idx = len(self.widgets_map)
        
        self.state = temp_state
        if current_widget_idx < len(self.widgets_map):
             self._remove_widgets_from(current_widget_idx)
        shared_chain = (
            self.tool_calls_group is not None
            and getattr(self.tool_calls_group, "owner_message", None) is not self
        )
        tool_slots = self._tool_slots_in_order()
        if self.compact_mode and tool_slots:
            self._move_intermediate_widgets_to_group()
        elif self.compact_mode and self.streaming and shared_chain:
            # Keep streamed continuation text with the active iteration, but
            # reasoning only belongs there once this message calls a tool.
            self._restore_moved_thinking()
            self._move_intermediate_widgets_to_group(include_thinking=False)
        elif not tool_slots:
            self._restore_intermediate_widgets()
        return False

    def append(self, widget):
        super().append(widget)

    def _walk_widgets(self, widget):
        child = widget.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            yield child
            yield from self._walk_widgets(child)
            child = nxt

    def apply_zoom(self, zoom: int) -> None:
        """Re-render only the LaTeX widgets at the new application zoom.

        Walks the live widget tree so it stays correct across streaming
        re-renders without bookkeeping in the chunk processors.
        """
        if not self.controller.newelle_settings.display_latex:
            return
        inline_size = _inline_latex_size(zoom)
        for w in self._walk_widgets(self):
            if isinstance(w, DisplayLatex):
                w.update_zoom(zoom)
            elif isinstance(w, InlineLatex):
                w.update_zoom(inline_size)
                spacer = getattr(w, "_spacer", None)
                if spacer is not None and w.dims:
                    spacer.set_size_request(w.dims[0], w.dims[1] + 1)

    def _get_tool_group(self):
        if self.tool_calls_group is None:
            self.tool_calls_group = ToolCallsGroupWidget()
            self.tool_calls_group.owner_message = self
            if self.compact_mode:
                chat_history = self._get_chat_history()
                if chat_history is not None and hasattr(chat_history, "register_compact_tool_group"):
                    chat_history.register_compact_tool_group(
                        self.tool_calls_group, self.id_message
                    )
        return self.tool_calls_group

    def attach_tool_group(self, group):
        """Use a shared group, moving existing slots without re-executing them."""
        if group is None or group is self.tool_calls_group:
            return
        old_group = self.tool_calls_group
        slots = self._tool_slots_in_order()
        self.tool_calls_group = group
        if getattr(group, "owner_message", None) is None:
            group.owner_message = self

        if old_group is not None and old_group is not group:
            if old_group.get_parent() is self:
                self.remove(old_group)
            for slot in slots:
                group.adopt_slot(slot)
            if not group.slots:
                return

        if self.compact_mode:
            self._attach_compact_tool_group()
        else:
            for slot in slots:
                parent = slot.get_parent()
                if parent is not self:
                    if parent is not None:
                        parent.remove(slot)
                    anchor = self._find_previous_root_widget(
                        next(
                            index for index, (_type, widget, _chunk) in enumerate(self.widgets_map)
                            if widget is slot
                        )
                    )
                    self.insert_child_after(slot, anchor)

    def _tool_slots_in_order(self):
        return [
            widget
            for chunk_type, widget, _chunk in self.widgets_map
            if chunk_type == "tool_call" and isinstance(widget, ToolCallSlot)
        ]

    def _move_intermediate_widgets_to_group(self, include_thinking=True):
        """Put reasoning and text accompanying tool calls in the expander."""
        group = self.tool_calls_group
        if group is None:
            return
        for index, (chunk_type, widget, _chunk) in enumerate(self.widgets_map):
            is_intermediate = chunk_type == "text" or (
                include_thinking and isinstance(widget, ThinkingWidget)
            )
            if not is_intermediate or not isinstance(widget, Gtk.Widget):
                continue
            self._compact_moved_widgets.setdefault(widget, index)
            group.append_auxiliary_widget(
                widget,
                (self.id_message, index),
            )

    def _restore_intermediate_widgets(self):
        """Return moved reasoning and text to their original positions."""
        group = self.tool_calls_group
        if group is None:
            return
        for widget, index in list(self._compact_moved_widgets.items()):
            if widget.get_parent() is group.content_box:
                group.remove_auxiliary_widget(widget)
            anchor = self._find_previous_root_widget(index)
            self.insert_child_after(widget, anchor)
        self._compact_moved_widgets.clear()

    def _restore_moved_thinking(self):
        """Keep reasoning outside a shared group until this message calls a tool."""
        group = self.tool_calls_group
        if group is None:
            return
        for widget, index in list(self._compact_moved_widgets.items()):
            if not isinstance(widget, ThinkingWidget):
                continue
            if widget.get_parent() is group.content_box:
                group.remove_auxiliary_widget(widget)
            anchor = self._find_previous_root_widget(index)
            self.insert_child_after(widget, anchor)
            self._compact_moved_widgets.pop(widget, None)

    def _has_content_outside_tool_group(self):
        for chunk_type, widget, _chunk in self.widgets_map:
            if chunk_type == "tool_call":
                continue
            candidates = widget if isinstance(widget, list) else [widget]
            if any(candidate.get_parent() is self for candidate in candidates):
                return True
        return False

    def _find_previous_root_widget(self, index):
        """Return the closest earlier mapped widget still under this Message."""
        for _chunk_type, widget, _chunk in reversed(self.widgets_map[:index]):
            candidates = widget if isinstance(widget, list) else [widget]
            for candidate in reversed(candidates):
                if candidate.get_parent() is self:
                    return candidate
        return None

    def _attach_compact_tool_group(self):
        group = self.tool_calls_group
        slots = self._tool_slots_in_order()
        if group is None or not slots:
            return
        owner = getattr(group, "owner_message", None)
        if owner is not None and owner is not self:
            # The expander belongs to the first message in the chain. A
            # later continuation only contributes its slots.
            for slot in group.slots:
                group.append_slot(slot)
            self._move_intermediate_widgets_to_group()
            return
        if group.get_parent() is not self:
            first_slot = slots[0]
            if first_slot.get_parent() is self:
                anchor = first_slot.get_prev_sibling()
            else:
                first_index = next(
                    index
                    for index, (_type, widget, _chunk) in enumerate(self.widgets_map)
                    if widget is first_slot
                )
                anchor = self._find_previous_root_widget(first_index)
            if anchor is group:
                anchor = None
            self.insert_child_after(group, anchor)
        for slot in slots:
            group.append_slot(slot)
        self._move_intermediate_widgets_to_group()

    def _detach_compact_tool_group(self):
        group = self.tool_calls_group
        if group is None:
            return
        self._restore_intermediate_widgets()
        if group.get_parent() is self:
            self.remove(group)

        for index, (_type, widget, _chunk) in enumerate(self.widgets_map):
            if not isinstance(widget, ToolCallSlot):
                continue
            parent = widget.get_parent()
            if parent is not None:
                parent.remove(widget)
            anchor = self._find_previous_root_widget(index)
            self.insert_child_after(widget, anchor)

    def set_compact_mode(self, enabled: bool):
        """Move existing tool slots without rebuilding or executing them."""
        enabled = bool(enabled)
        if self.compact_mode == enabled:
            return
        self.compact_mode = enabled
        if enabled:
            self._attach_compact_tool_group()
        else:
            self._detach_compact_tool_group()

    def _remove_tool_slot(self, slot):
        group = slot.group
        if group is not None:
            self._unregister_execution_copybox(slot.widget)
            group.remove_slot(slot)
            if not group.slots and group.get_parent() is self:
                self.remove(group)
            return
        parent = slot.get_parent()
        if parent is not None:
            parent.remove(slot)

    @staticmethod
    def _set_tool_slot_state(slot, status):
        if slot is not None and slot.group is not None:
            slot.group.set_slot_state(slot, status)
        return GLib.SOURCE_REMOVE

    def _remove_widgets_from(self, start_index):
        while len(self.widgets_map) > start_index:
            w_type, widget_or_list, _ = self.widgets_map.pop()
            if w_type == "tool_call" and isinstance(widget_or_list, ToolCallSlot):
                self._remove_tool_slot(widget_or_list)
                continue
            if isinstance(widget_or_list, list):
                for w in widget_or_list:
                    self._unregister_execution_copybox(w)
                    parent = w.get_parent()
                    if parent is not None:
                        parent.remove(w)
            else:
                if w_type == "text" and isinstance(widget_or_list, Gtk.Label):
                    self._stop_stream_fade(widget_or_list)
                if widget_or_list in self._compact_moved_widgets:
                    group = self.tool_calls_group
                    if group is not None:
                        group.remove_auxiliary_widget(widget_or_list)
                    self._compact_moved_widgets.pop(widget_or_list, None)
                self._unregister_execution_copybox(widget_or_list)
                parent = widget_or_list.get_parent()
                if parent is not None:
                    parent.remove(widget_or_list)

    def _register_execution_copybox(self, widget):
        if not isinstance(widget, CopyBox):
            return

        track_copybox = False
        if widget.execution_request:
            track_copybox = True
        elif hasattr(widget, "run_button") and widget.run_callback is not None:
            track_copybox = True

        if not track_copybox:
            return
        if widget in self.pending_execution_copyboxes:
            return

        self._tracked_copyboxes_seen += 1

        if widget.execution_request and widget.is_responded():
            self._try_auto_send_after_copyboxes()
            return

        self.pending_execution_copyboxes.add(widget)
        widget.connect("command-complete", self._on_execution_copybox_done)
        if widget.execution_request:
            widget.connect("skip-clicked", self._on_execution_copybox_skipped)

    def _unregister_execution_copybox(self, widget):
        if widget in self.pending_execution_copyboxes:
            self.pending_execution_copyboxes.discard(widget)

    def _trigger_auto_send_message(self):
        chat_tab = self._get_chat_tab()
        chat_history = self._get_chat_history()
        if not chat_tab.status:
            return

        chat_tab.status = False
        if chat_history is not None:
            chat_history.update_button_text()
            chat_history.scrolled_chat()
        threading.Thread(target=chat_tab.send_message).start()

    def _try_auto_send_after_copyboxes(self):
        if self._copybox_auto_send_sent:
            return
        if self.restore:
            return
        if self._tracked_copyboxes_seen == 0:
            return
        if len(self.pending_execution_copyboxes) != 0:
            return
        self._copybox_auto_send_sent = True
        self._trigger_auto_send_message()

    def _mark_execution_copybox_done(self, copybox):
        self.pending_execution_copyboxes.discard(copybox)
        self._try_auto_send_after_copyboxes()

    def _on_execution_copybox_done(self, copybox, output):
        self._mark_execution_copybox_done(copybox)

    def _on_execution_copybox_skipped(self, copybox):
        self._mark_execution_copybox_done(copybox)

    def _can_update_widget(self, w_type, widget_or_list, new_chunk):
        if w_type == "complex": return False
        
        widget = widget_or_list
        if w_type != new_chunk.type: return False
        if w_type == "text":
            needs_citation_widget = self._has_renderable_citation(new_chunk.text)
            if needs_citation_widget:
                return False
            return isinstance(widget, Gtk.Label)
        if w_type in ("latex", "latex_inline"):
            # A completed equation does not change when later streaming tokens
            # are appended. Keep its rendered canvas instead of replacing it
            # (and briefly showing its asynchronous placeholder) on every
            # update after the equation.
            return (
                isinstance(widget, DisplayLatex)
                and widget.latex == new_chunk.text
            )
        if w_type == "inline_chunks":
            # Inline equations live inside a TextView child anchor. The
            # surrounding markup can be updated while reusing those equation
            # widgets, so their rendered canvases remain visible.
            return (
                isinstance(widget, Gtk.Overlay)
                and hasattr(widget, "_inline_textview")
            )
        if w_type == "divider": return True
        if w_type == "sources": return False
        if w_type == "codeblock":
            if isinstance(widget, CopyBox):
                codeblocks = {**self.controller.extensionloader.codeblocks, **self.controller.integrationsloader.codeblocks}
                if not self.streaming and new_chunk.lang in codeblocks:
                    return False
                if new_chunk.lang in ["video", "image", "chart", "file", "folder"]: return False
                return True
            return True
        if w_type == "thinking": return True
        if w_type == "tool_call":
            return (
                isinstance(widget, ToolCallSlot)
                and isinstance(new_chunk, MessageChunk)
                and new_chunk.type == "tool_call"
                and widget.active
                and widget.tool_name == new_chunk.tool_name
            )
        return False

    def _update_widget(self, widget, w_type, new_chunk):
        if w_type == "text":
            previous_text = widget.get_text()
            if widget.get_label() != new_chunk.text:
                widget.set_markup(markwon_to_pango(new_chunk.text, validate=not self.streaming))
                self._fade_streamed_text(widget, previous_text)
        elif w_type == "codeblock":
            if isinstance(widget, CopyBox):
                widget.update_code(new_chunk.text)
                widget.set_language(new_chunk.lang)
        elif w_type == "thinking":
            widget.set_thinking(new_chunk.text)
        elif w_type == "inline_chunks":
            self._update_inline_chunks_widget(widget, new_chunk)
        elif w_type == "tool_call" and isinstance(widget, ToolCallSlot):
            widget.update_chunk(new_chunk)
            if widget.group is not None:
                widget.group.update_slot(widget, new_chunk)

    def _simulate_state_update(self, chunk, state):
        if chunk.type == "codeblock":
            state["codeblock_id"] += 1

    def _process_chunk(self, chunk, box, state, restore, is_user, msg_uuid):
        if chunk.type == "tool_call":
            slot = self._process_tool_call(chunk, box, state, restore, msg_uuid)
            if slot is not None:
                self.widgets_map.append(("tool_call", slot, chunk))
            return

        start_children = self.observe_children()
        
        # Real logic
        if chunk.type == "codeblock":
            self._process_codeblock(chunk, box, state, restore, is_user, msg_uuid)
        elif chunk.type == "table":
            self._process_table(chunk, box)
        elif chunk.type == "inline_chunks":
            self._process_inline_chunks(chunk, box)
        elif chunk.type in ("latex", "latex_inline"):
            self._process_latex(chunk, box)
        elif chunk.type == "thinking":
            think = ThinkingWidget(expanded=self.controller.settings.get_boolean("expand-reasoning"))
            self.thinking_widget = think
            think.start_thinking(chunk.text)
            box.append(think)
            self._queue_execution(lambda: GLib.idle_add(think.stop_thinking))
        elif chunk.type == "text":
            self._process_text(chunk, box)
        elif chunk.type == "divider":
            self._process_divider(box)
        elif chunk.type == "sources":
            self.sources_button = SourcesButton(self.citation_sources, self._open_citation_source)
            box.append(self.sources_button)
            
        # Capture added widgets
        end_children = self.observe_children()
        new_widgets = [c for c in end_children if c not in start_children]
        
        if len(new_widgets) == 1:
             self.widgets_map.append((chunk.type, new_widgets[0], chunk))
        elif len(new_widgets) > 1:
            self.widgets_map.append(("complex", new_widgets, chunk))
        else:
            pass # Nothing added
        
    def observe_children(self):
        children = []
        child = self.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        return children

    # --- Process Methods (Copied & Adapted from window.py) ---

    def _process_text(self, chunk, box):
        text = re.sub(r'\n{2,}', '\n', chunk.text)
        if self._has_renderable_citation(text):
            widgets = {}
            markdown = self._inject_source_widgets(text, widgets)
            box.append(self._build_markup_overlay(markdown, widgets, text))
            return

        label_kwargs = dict(
            label=markwon_to_pango(text, validate=not self.streaming),
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            width_chars=1,
            selectable=True,
            use_markup=True,
            css_classes=["message-text"],
        )
        if self.is_user:
            # User bubbles stay compact.
            label_kwargs["halign"] = Gtk.Align.START
            label_kwargs["max_width_chars"] = 72
        else:
            # Assistant text fills the available row width (left-aligned).
            label_kwargs["halign"] = Gtk.Align.FILL
            label_kwargs["xalign"] = 0
        label = Gtk.Label(**label_kwargs)
        box.append(label)
        self._fade_streamed_text(label, "")

    def _has_renderable_citation(self, text: str) -> bool:
        return any(
            int(match.group(1)) in self.citation_sources_by_number
            for match in _CITATION_MARKER_PATTERN.finditer(text)
        )

    def _inject_source_widgets(self, markdown: str, widgets: dict, start_index: int = 0) -> str:
        widget_index = start_index

        def replace_segment(segment: str) -> str:
            nonlocal widget_index

            def replace_marker(match):
                nonlocal widget_index
                source = self.citation_sources_by_number.get(int(match.group(1)))
                if source is None:
                    return match.group(0)
                widget_id = str(widget_index)
                widget_index += 1
                widgets[widget_id] = SourceChip(source, self._open_citation_source)
                return f"WZIDZW{widget_id}WZIDZW"

            return _CITATION_MARKER_PATTERN.sub(replace_marker, segment)

        parts = _CITATION_PROTECTED_MARKDOWN_PATTERN.split(markdown)
        for index in range(0, len(parts), 2):
            parts[index] = replace_segment(parts[index])
        return "".join(parts)

    def _build_markup_overlay(self, markdown: str, widgets: dict, measure_text: str) -> Gtk.Widget:
        overlay = Gtk.Overlay(hexpand=True)
        measure = Gtk.Label(
            label=measure_text,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            width_chars=1,
            hexpand=True,
            xalign=0,
        )
        measure.set_opacity(0)
        overlay.set_child(measure)

        textview = MarkupTextView(hexpand=True, valign=Gtk.Align.START)
        overlay.add_overlay(textview)
        overlay.set_measure_overlay(textview, True)

        markup = markwon_to_pango(markdown, validate=not self.streaming)
        markup = re.sub(r'WZIDZW(\d+)WZIDZW', r'<widget id="\1"/>', markup)
        textview.add_markup_text(textview.get_buffer().get_start_iter(), markup, widgets=widgets)
        return overlay

    def _open_citation_source(self, source: CitationSource):
        if not source.target:
            if self.sources_button is not None:
                self.sources_button.popup()
            return
        try:
            if source.kind == "file":
                path = os.path.abspath(os.path.expanduser(source.target))
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
                return

            main_window = self._get_main_window()
            ui_controller = getattr(main_window, "ui_controller", None)
            if ui_controller is not None:
                use_integrated = not self.controller.settings.get_boolean("external-browser")
                ui_controller.open_link(source.target, False, use_integrated)
            else:
                Gio.AppInfo.launch_default_for_uri(source.target, None)
        except Exception as error:
            print(f"Failed to open citation source: {error}")

    def _fade_streamed_text(self, label, previous_text):
        """Fade in only the visible text appended by the latest stream update."""
        self._stop_stream_fade(label)
        if not self.streaming or self.is_user:
            return

        settings = Gtk.Settings.get_default()
        if settings is not None and not settings.get_property("gtk-enable-animations"):
            return

        current_text = label.get_text()
        if not current_text.startswith(previous_text):
            return

        appended_text = current_text[len(previous_text):]
        if not appended_text.strip():
            return

        start_index = len(previous_text.encode("utf-8"))
        end_index = len(current_text.encode("utf-8"))
        started_at = GLib.get_monotonic_time()

        def set_suffix_alpha(alpha):
            attributes = Pango.AttrList.new()
            fade = Pango.attr_foreground_alpha_new(round(65535 * alpha))
            fade.start_index = start_index
            fade.end_index = end_index
            attributes.insert(fade)
            label.set_attributes(attributes)

        set_suffix_alpha(_STREAM_FADE_START_ALPHA)

        def on_tick(widget, _frame_clock):
            elapsed = GLib.get_monotonic_time() - started_at
            progress = min(1.0, elapsed / _STREAM_FADE_DURATION_US)
            eased_progress = 1.0 - pow(1.0 - progress, 3)
            alpha = _STREAM_FADE_START_ALPHA + (
                (1.0 - _STREAM_FADE_START_ALPHA) * eased_progress
            )

            if progress >= 1.0 or not self.streaming:
                widget.set_attributes(Pango.AttrList.new())
                widget._stream_fade_tick_id = None
                return GLib.SOURCE_REMOVE

            set_suffix_alpha(alpha)
            return GLib.SOURCE_CONTINUE

        label._stream_fade_tick_id = label.add_tick_callback(on_tick)

    @staticmethod
    def _stop_stream_fade(label):
        tick_id = getattr(label, "_stream_fade_tick_id", None)
        if tick_id is not None:
            label.remove_tick_callback(tick_id)
            label._stream_fade_tick_id = None
        label.set_attributes(Pango.AttrList.new())

    def _process_divider(self, box):
        box.append(Gtk.Separator(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_top=8,
            margin_bottom=8,
        ))

    def _get_chat_tab(self):
        if hasattr(self.parent_window, "chat_history") and hasattr(self.parent_window, "chat_id"):
            return self.parent_window
        if hasattr(self.parent_window, "window") and hasattr(self.parent_window.window, "chat_id"):
            return self.parent_window.window
        return self.parent_window

    def _get_chat_history(self):
        if hasattr(self.parent_window, "get_file_button") and hasattr(self.parent_window, "scrolled_chat"):
            return self.parent_window
        if hasattr(self.parent_window, "chat_history"):
            return self.parent_window.chat_history
        return None

    def _get_main_window(self):
        chat_tab = self._get_chat_tab()
        if hasattr(chat_tab, "window"):
            return chat_tab.window
        return chat_tab

    def _create_copybox(self, text, lang, state=None, codeblock_id=-1, allow_edit=False, enable_run_callback=False):
        id_message = state["id_message"] if state is not None else -1
        copybox = CopyBox(
            text,
            lang,
            id_message=id_message,
            id_codeblock=codeblock_id,
            allow_edit=allow_edit,
            color_scheme=self.controller.newelle_settings.editor_color_scheme,
        )
        copybox.connect("edit-clicked", self._on_copybox_edit_clicked)
        copybox.connect("terminal-clicked", self._on_copybox_terminal_clicked)
        if enable_run_callback:
            copybox.set_run_callback(lambda command, cb=copybox: self._on_copybox_run_requested(cb, command))
        self._register_execution_copybox(copybox)
        return copybox

    def _on_copybox_edit_clicked(self, copybox, id_message, id_codeblock, text, lang):
        self._get_main_window().add_editor_tab_inline(id_message, id_codeblock, text, lang)

    def _on_copybox_terminal_clicked(self, copybox, command, execution_request_mode):
        from .terminal_dialog import TerminalDialog

        shell_command = "cd " + quote_string(os.getcwd()) + "; " + command + "; exec bash"

        if not self.controller.newelle_settings.virtualization:
            shell_command = add_S_to_sudo(shell_command)
            terminal_command = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;alias sudo=\"sudo -S\";" + shell_command]
        else:
            terminal_command = ["bash", "-c", "export TERM=xterm-256color;" + shell_command]

        terminal = TerminalDialog(parent_window=self._get_main_window())

        def save_output(save):
            if save is None:
                return
            copybox.complete_execution(save)
            self._persist_console_output(copybox, save)
            self._mark_execution_copybox_done(copybox)

        terminal.load_terminal(terminal_command)
        terminal.save_output_func(save_output)
        terminal.present()

    def _persist_console_output(self, copybox, output):
        if output is None:
            return

        chat = self._get_chat_tab().chat
        id_message = copybox.id_message

        if id_message < len(chat) and chat[id_message]["User"] == "Console":
            chat[id_message]["Message"] = output
        else:
            chat.append({"User": "Console", "Message": " " + output})

    def _run_web_preview_code(self, copybox):
        codeblocks = [
            chunk
            for chunk in get_message_chunks(self.parent_window.chat[copybox.id_message]["Message"])
            if chunk.type == "codeblock"
        ]
        files = {"html": "", "css": "", "js": ""}

        for codeblock in codeblocks:
            codeblock_lang = codeblock.lang.lower()
            if codeblock_lang == "html":
                files["html"] = codeblock.text
            elif codeblock_lang == "css":
                files["css"] = codeblock.text
            elif codeblock_lang in ["js", "javascript"]:
                files["js"] = codeblock.text

        lang = copybox.get_language().lower()
        if lang == "html":
            files["html"] = copybox.get_code()
        elif lang == "css":
            files["css"] = copybox.get_code()
        elif lang in ["js", "javascript"]:
            files["js"] = copybox.get_code()

        temp_dir = tempfile.mkdtemp(dir=self.controller.cache_dir)
        with open(os.path.join(temp_dir, "index.html"), "w") as html_file:
            html_file.write(files["html"])
        with open(os.path.join(temp_dir, "style.css"), "w") as css_file:
            css_file.write(files["css"])
        with open(os.path.join(temp_dir, "script.js"), "w") as js_file:
            js_file.write(files["js"])

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("", 0))
            _, port = sock.getsockname()

        main_window = self._get_main_window()

        def open_browser_later():
            main_window.ui_controller.new_browser_tab(f"http://localhost:{port}", new=False)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, open_browser_later)
        return main_window.execute_terminal_command(
            "cd {} && python3 -m http.server {}".format(quote_string(temp_dir), port)
        )

    def _on_copybox_run_requested(self, copybox, command):
        lang = copybox.get_language().lower()
        if lang in ["html", "css", "js", "javascript"]:
            code = self._run_web_preview_code(copybox)
        else:
            code = self._get_main_window().execute_terminal_command(command)

        output = code[1]
        self._persist_console_output(copybox, output)
        return output

    def _process_codeblock(self, chunk, box, state, restore, is_user, msg_uuid):
        state["codeblock_id"] += 1
        codeblock_id = state["codeblock_id"]
        lang = chunk.lang
        text = chunk.text
        
        codeblocks = {**self.controller.extensionloader.codeblocks, **self.controller.integrationsloader.codeblocks}

        # While streaming, keep extension codeblocks as plain code and render
        # extension widgets only after the final pass.
        if self.streaming and lang in codeblocks:
            box.append(self._create_copybox(text, lang, state=state, codeblock_id=codeblock_id, allow_edit=state["editable"], enable_run_callback=True))
            return
        
        if lang in codeblocks:
            self._process_extension_codeblock(chunk, box, state, restore, msg_uuid, codeblocks[lang])
        elif lang == "think":
            think = ThinkingWidget(expanded=self.controller.settings.get_boolean("expand-reasoning"))
            think.set_thinking(text)
            box.append(think)
        elif lang == "image":
            self._process_image_codeblock(text, box)
        elif lang == "video":
            self._process_video_codeblock(text, box)
        elif lang == "console" and not is_user:
            self._process_console_codeblock(chunk, box, state, restore)
        elif lang in ("file", "folder"):
            chat_history = self._get_chat_history()
            for obj in text.split("\n"):
                if obj.strip():
                     if chat_history is not None:
                         box.append(chat_history.get_file_button(obj))
        elif lang == "chart" and not is_user:
            self._process_chart_codeblock(chunk, box)
        elif lang == "latex":
            try:
                base = _display_latex_base_size(self.controller.newelle_settings.zoom)
                box.append(DisplayLatex(text, base, self.controller.cache_dir))
            except Exception:
                box.append(self._create_copybox(text, lang, state=state, codeblock_id=codeblock_id, allow_edit=state["editable"], enable_run_callback=True))
        else:
            box.append(self._create_copybox(text, lang, state=state, codeblock_id=codeblock_id, allow_edit=state["editable"], enable_run_callback=True))

    def _process_image_codeblock(self, text, box):
        for line in text.split("\n"):
            if not line.strip(): continue
            image = Gtk.Image(css_classes=["image"])
            if line.startswith("data:image/"):
                try:
                    header_end = line.index(",")
                    data = line[header_end + 1:]
                    raw_data = base64.b64decode(data)
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(raw_data))
                    image.set_from_paintable(texture)
                    box.append(image)
                except Exception:
                    try:
                        header_end = line.index(",")
                        data = line[header_end + 1:]
                        raw_data = base64.b64decode(data)
                        loader = GdkPixbuf.PixbufLoader()
                        loader.write(raw_data)
                        loader.close()
                        image.set_from_pixbuf(loader.get_pixbuf())
                        box.append(image)
                    except Exception:
                        pass
            elif line.startswith(("https://", "http://")):
                img = image
                load_image_with_callback(line, lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf()))
                box.append(image)
            else:
                image.set_from_file(line)
                box.append(image)

    def _process_video_codeblock(self, text, box):
        for line in text.split("\n"):
            if not line.strip(): continue
            video = Gtk.Video(css_classes=["video"], vexpand=True, hexpand=True)
            video.set_size_request(-1, 400)
            video.set_file(Gio.File.new_for_path(line))
            box.append(video)

    def _process_chart_codeblock(self, chunk, box):
        result = {}
        percentages = False
        for line in chunk.text.split("\n"):
            parts = line.split("-")
            if len(parts) != 2:
                box.append(self._create_copybox(chunk.text, "chart"))
                return
            key = parts[0].strip()
            percentages = "%" in parts[1]
            value_str = "".join(c for c in parts[1] if c.isdigit() or c == ".")
            try: result[key] = float(value_str)
            except ValueError: result[key] = 0
        if result:
            box.append(BarChartBox(result, percentages))

    def _process_latex(self, chunk, box):
        try:
            base = _display_latex_base_size(self.controller.newelle_settings.zoom)
            box.append(DisplayLatex(chunk.text, base, self.controller.cache_dir))
        except Exception:
            box.append(self._create_copybox(chunk.text, "latex"))

    def _process_inline_chunks(self, chunk, box):
        if not chunk.subchunks: return
        overlay = Gtk.Overlay()
        label = Gtk.Label(wrap=True)
        label.set_opacity(0)
        overlay.set_child(label)
        textview = MarkupTextView()
        textview.set_valign(Gtk.Align.START)
        textview.set_hexpand(True)
        overlay.add_overlay(textview)
        overlay.set_measure_overlay(textview, True)
        overlay._inline_measure = label
        overlay._inline_textview = textview
        overlay._inline_latex_widgets = {}
        overlay._inline_processed_markup = None

        self._update_inline_chunks_widget(overlay, chunk)
        box.append(overlay)

    def _update_inline_chunks_widget(self, overlay, chunk):
        """Update mixed inline markup without rebuilding rendered equations."""
        subchunks = chunk.subchunks or []
        overlay._inline_measure.set_label(" ".join(ch.text for ch in subchunks))

        full_markdown = ""
        widgets_dict = {}
        previous_latex_widgets = overlay._inline_latex_widgets
        current_latex_widgets = {}
        latex_index = 0

        for subchunk in subchunks:
            if subchunk.type == "text":
                full_markdown += subchunk.text
            elif subchunk.type == "latex_inline":
                widget_id = str(latex_index)
                placeholder = f"WZIDZW{widget_id}WZIDZW"
                full_markdown += placeholder

                try:
                    previous = previous_latex_widgets.get(latex_index)
                    if previous is not None and previous[0] == subchunk.text:
                        latex_overlay = previous[1]
                    else:
                        font_size = _inline_latex_size(
                            self.controller.newelle_settings.zoom
                        )
                        latex = InlineLatex(subchunk.text, font_size)
                        latex_overlay = Gtk.Overlay()
                        latex_overlay.set_hexpand(False)
                        latex_overlay.add_overlay(latex)
                        spacer = Gtk.Box()
                        spacer.set_size_request(latex.dims[0], latex.dims[1] + 1)
                        latex_overlay.set_child(spacer)
                        latex._spacer = spacer
                        latex.set_margin_top(5)
                    widgets_dict[widget_id] = latex_overlay
                    current_latex_widgets[latex_index] = (
                        subchunk.text,
                        latex_overlay,
                    )
                except Exception:
                    # Fallback if latex fails: use the text representation
                    # We remove the placeholder and just add the text representation
                    full_markdown = full_markdown[:-len(placeholder)]
                    full_markdown += LatexNodes2Text().latex_to_text(subchunk.text)
                latex_index += 1

        full_markdown = self._inject_source_widgets(
            full_markdown,
            widgets_dict,
            start_index=latex_index,
        )

        full_markup = markwon_to_pango(full_markdown, validate=not self.streaming)

        # Replace placeholders with <widget> tags in the pango markup
        # Note: we use regex to find placeholders because they might be inside tags
        processed_markup = re.sub(
            r'WZIDZW(\d+)WZIDZW',
            r'<widget id="\1"/>',
            full_markup,
        )

        textview = overlay._inline_textview
        previous_markup = overlay._inline_processed_markup
        latex_widgets_unchanged = (
            len(previous_latex_widgets) == len(current_latex_widgets)
            and all(
                index in previous_latex_widgets
                and previous_latex_widgets[index][1] is latex_widget[1]
                for index, latex_widget in current_latex_widgets.items()
            )
        )
        if (
            latex_widgets_unchanged
            and previous_markup is not None
            and processed_markup.startswith(previous_markup)
        ):
            # The usual streaming update only appends plain markup after the
            # final equation. Insert that suffix directly so GTK never
            # unanchors (and unmaps) the already-rendered inline widgets.
            markup_suffix = processed_markup[len(previous_markup):]
            if markup_suffix:
                buffer = textview.get_buffer()
                textview.add_markup_text(
                    buffer.get_end_iter(),
                    markup_suffix,
                    widgets=widgets_dict,
                )
        else:
            # Structural Markdown changes require reparsing the line. Exact
            # equation widgets are still reused when the anchors are rebuilt.
            buffer = textview.get_buffer()
            buffer.set_text("")
            textview.add_markup_text(
                buffer.get_start_iter(),
                processed_markup,
                widgets=widgets_dict,
            )
        overlay._inline_latex_widgets = current_latex_widgets
        overlay._inline_processed_markup = processed_markup

    def _process_extension_codeblock(self, chunk, box, state, restore, msg_uuid, extension):
        lang = chunk.lang
        value = chunk.text
        try:
            sig = inspect.signature(extension.get_gtk_widget)
            supports_uuid = len(sig.parameters) == 3
            if restore:
                widget = (extension.restore_gtk_widget(value, lang, msg_uuid) if supports_uuid else extension.restore_gtk_widget(value, lang))
            else:
                widget = (extension.get_gtk_widget(value, lang, msg_uuid) if supports_uuid else extension.get_gtk_widget(value, lang))
            
            if widget: box.append(widget)
            
            if widget is None or extension.provides_both_widget_and_answer(value, lang):
                 self._setup_extension_async_response(chunk, box, state, restore, extension, widget)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(e)
            box.append(self._create_copybox(chunk.text, lang, state=state, codeblock_id=state["codeblock_id"], allow_edit=state["editable"], enable_run_callback=True))

    def _process_console_codeblock(self, chunk, box, state, restore):
        from ...utility.command_permissions import CommandPermissionManager, CommandAction

        state["id_message"] += 1
        command = chunk.text
        chat_tab = self._get_chat_tab()

        perm_manager = CommandPermissionManager.get_instance(self.controller.settings)
        working_dir = self.controller.settings.get_string("path")
        action, reason = perm_manager.check_command(command, working_dir)

        can_auto_run = (
            action == CommandAction.ALLOW and
            self.controller.newelle_settings.auto_run and
            chat_tab.auto_run_times < self.controller.newelle_settings.max_run_times
        )
        
        if can_auto_run:
            state["has_terminal_command"] = True
            text_expander = Gtk.Expander(label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10, margin_end=10)
            text_expander.set_expanded(False)
            box.append(text_expander)
            
            reply_from_console = self.controller.get_console_reply(chat_tab.chat_id, state["id_message"])
            
            # Logic for deferred execution
            self._queue_execution(lambda: self._run_console_command(command, restore, reply_from_console, text_expander, state))
            
            if not restore:
                chat_tab.auto_run_times += 1
        else:
            if action == CommandAction.BLOCK:
                if not restore:
                    self.controller.append_chat_message(
                        chat_tab.chat_id,
                        {"User": "Console", "Message": f"Command blocked: {reason}"},
                    )
            else:
                if not restore:
                    self.controller.append_chat_message(
                        chat_tab.chat_id,
                        {"User": "Console", "Message": "None"},
                    )
            copybox = self._create_copybox(command, "console", state=state, codeblock_id=state["codeblock_id"], allow_edit=state["editable"], enable_run_callback=True)
            if action == CommandAction.BLOCK:
                copybox.complete_execution(None)
            box.append(copybox)

    def _process_tool_call(self, chunk, box, state, restore, msg_uuid):
        tool_name = chunk.tool_name
        tool = self.controller.tools.get_tool(tool_name)
        state["id_message"] += 1
        if not restore: self.controller.msgid = state["id_message"]

        group = self._get_tool_group()
        if tool is None:
            placeholder = self._create_copybox(chunk.text, "tool_call")
            slot = group.register_call(
                tool_name,
                tool_name,
                chunk,
                placeholder,
                tool_icon_name="tools-symbolic",
            )
            slot.message_id = self.id_message
            slot._compact_order = (self.id_message, slot.entry_id)
            self._place_tool_slot(slot, box)
            group.set_slot_state(slot, "error")
            return slot

        tool_call_id = state.get("tool_call_counter", 0)
        state["tool_call_counter"] = tool_call_id + 1

        provider_tool_id = getattr(chunk, "tool_id", "")
        if isinstance(provider_tool_id, str) and provider_tool_id.strip():
            tool_uuid = provider_tool_id.strip()
        elif not restore:
            tool_uuid = str(uuid.uuid4())[:8]
        else:
            tool_uuid = self.controller.get_tool_call_uuid(self._get_chat_tab().chat_id, state["id_message"], tool_name, tool_call_id)

        state["has_terminal_command"] = True
        self.controller.current_tool_uuid = tool_uuid

        slot = None
        try:
            placeholder = ToolWidget(tool.name, chunk.text)
            slot = group.register_call(
                tool.name,
                tool.title,
                chunk,
                placeholder,
                tool_icon_name=tool.icon_name,
            )
            slot.message_id = self.id_message
            slot._compact_order = (self.id_message, slot.entry_id)
            self._place_tool_slot(slot, box)

            # The slot owns the latest streamed chunk, so deferred execution
            # uses final arguments even when JSON arrives over several updates.
            self._queue_execution(
                lambda slot=slot: self._run_tool_call_with_placeholder(
                    tool, tool_uuid, state, restore, slot, msg_uuid
                )
            )
            return slot
        except Exception as e:
            print(f"Tool error: {e}")
            if slot is not None:
                group.set_slot_state(slot, "error")
            return slot

    def _place_tool_slot(self, slot, box):
        if self.compact_mode:
            group = self.tool_calls_group
            owner = getattr(group, "owner_message", None)
            if owner is not None and owner is not self:
                if group.get_parent() is not None and self.id_message >= 0 and getattr(owner, "id_message", -1) > self.id_message:
                    # A lazily loaded older message can become the true first
                    # position of this chain. Move the existing expander there.
                    old_parent = group.get_parent()
                    if old_parent is not None:
                        old_parent.remove(group)
                    group.owner_message = self
                    anchor = self._find_previous_root_widget(len(self.widgets_map))
                    self.insert_child_after(group, anchor)
                # Continuation messages contribute slots to the original
                # expander; never move that expander onto this row.
                group.append_slot(slot)
                return
            if group.get_parent() is not self:
                anchor = self._find_previous_root_widget(len(self.widgets_map))
                self.insert_child_after(group, anchor)
                for existing_slot in self._tool_slots_in_order():
                    group.append_slot(existing_slot)
            group.append_slot(slot)
            return
        parent = slot.get_parent()
        if parent is not box:
            if parent is not None:
                parent.remove(slot)
            box.append(slot)

    def _run_tool_call_with_placeholder(self, tool, tool_uuid, state, restore, slot, msg_uuid):
        if slot is not None and not slot.active:
            return

        if not restore and slot is not None:
            provider_tool_id = getattr(slot.chunk, "tool_id", "")
            if isinstance(provider_tool_id, str) and provider_tool_id.strip():
                tool_uuid = provider_tool_id.strip()
                self.controller.current_tool_uuid = tool_uuid

        chat_tab = self._get_chat_tab()
        chat_id = chat_tab.chat_id
        placeholder = slot.widget
        group = slot.group if slot is not None else None
        state["has_terminal_command"] = True
        self.controller.msgid = state["id_message"]

        def current_group():
            return slot.group if slot is not None else group

        def run_tool():
            try:
                if slot is not None and not slot.active:
                    return
                if not restore:
                    max_tool_calls = getattr(
                        self.controller.newelle_settings, "max_tool_calls", 10
                    )
                    if chat_tab.tool_call_count >= max_tool_calls:
                        error_text = _(
                            "Maximum tool call limit reached ({0})"
                        ).format(max_tool_calls)
                        placeholder.set_result(False, error_text)
                        if current_group() is not None:
                            current_group().set_slot_state(slot, "error")
                        return
                    chat_tab.tool_call_count += 1
                chunk = slot.chunk if slot is not None else None
                args = chunk.tool_args if chunk is not None else {}
                active_group = current_group()
                if active_group is not None:
                    active_group.set_slot_state(slot, "running")

                tool_failed = False
                if restore:
                    try:
                        result = tool.restore(
                            msg_uuid=msg_uuid,
                            tool_uuid=tool_uuid,
                            chat_id=chat_id,
                            **args,
                        )
                    except Exception as e:
                        tool_failed = True
                        result = ToolResult()
                        result.set_output(f"Error: {e}")
                else:
                    # Lazy loading guard: if a lazy tool is called before its
                    # schema was fetched, hand back the schema instead of running
                    # it with guessed arguments. The main GUI path rebuilds the
                    # tools prompt each turn, so marking the tool expanded here is
                    # enough for the next turn to expose its full parameters.
                    redirect = self.controller.tools.maybe_redirect_lazy_tool(
                        tool.name,
                        self.controller.newelle_settings.tools_settings_dict,
                        self.controller.expanded_tools,
                    )
                    if redirect is not None:
                        result = redirect
                    else:
                        try:
                            result = tool.execute(
                                msg_uuid=msg_uuid,
                                tool_uuid=tool_uuid,
                                chat_id=chat_id,
                                **args,
                            )
                        except Exception as e:
                            tool_failed = True
                            result = ToolResult()
                            result.set_output(f"Error: {e}")

                if not isinstance(result, ToolResult):
                    wrapped_result = ToolResult()
                    wrapped_result.set_output(result)
                    result = wrapped_result
                
                if not restore:
                    # Append result to active tool results in main thread if needed
                    chat_tab.active_tool_results.append(result)
                    
                    if getattr(result, "requires_interaction", False):
                        def _notify_if_unfocused():
                            try:
                                window = self._get_main_window()
                                if window and not window.is_active():
                                    app = Gio.Application.get_default()
                                    if app:
                                        notification = Gio.Notification.new("Action Required")
                                        notification.set_body(f"The tool '{tool.name}' requires your interaction.")
                                        app.send_notification("tool-interaction", notification)
                            except Exception as e:
                                print(f"Failed to send notification: {e}")
                        GLib.idle_add(_notify_if_unfocused)

                active_group = current_group()
                if active_group is not None and getattr(result, "requires_interaction", False):
                    GLib.idle_add(active_group.expand_for_interaction)

                widget = result.widget
                if widget:
                    # Tool wants custom widget. Placeholder was ToolWidget.
                    def swap_widget():
                        active_group = current_group()
                        if slot is not None and active_group is not None:
                            active_group.replace_slot_widget(slot, widget)
                        else:
                            parent = placeholder.get_parent()
                            if parent and parent.get_display():
                                parent.remove(placeholder)
                                parent.append(widget)
                        if widget.get_parent() is not None:
                            self._register_execution_copybox(widget)
                    GLib.idle_add(swap_widget)

                    # Handle result closure
                    def on_result(code):
                        active_group = current_group()
                        if active_group is not None:
                            active_group.set_slot_state(
                                slot, "completed" if code[0] else "error"
                            )
                else:
                    # Use placeholder (ToolWidget)
                    def on_result(code):
                        placeholder.set_result(code[0], code[1])
                        active_group = current_group()
                        if active_group is not None:
                            active_group.set_slot_state(
                                slot, "completed" if code[0] else "error"
                            )
                
                reply_from_console = self.controller.get_tool_response(
                    chat_id, state["id_message"], tool.name, tool_uuid
                )
                def get_response(reply_from_console):
                    if not restore:
                        response = result.get_output()
                        context_messages = result.get_context_messages()
                        if not restore:
                            try: chat_tab.active_tool_results.remove(result)
                            except: pass
                        if result.is_cancelled:
                            if current_group() is not None:
                                GLib.idle_add(self._set_tool_slot_state, slot, "cancelled")
                            return
                        if response is None and not context_messages:
                            code = (not tool_failed, None)
                        else:
                            state["should_continue"] = True
                            code = (not tool_failed, response)
                            console_output = response or "Tool returned additional context."
                            formatted = f"[Tool: {tool.name}, ID: {tool_uuid}]\n{console_output}"
                            self.controller.append_chat_message(
                                chat_id,
                                {"User": "Console", "Message": formatted},
                            )
                            for context_message in context_messages:
                                self.controller.append_chat_message(
                                    chat_id,
                                    {
                                        "User": "User",
                                        "Message": context_message,
                                        "ToolContext": True,
                                    },
                                )
                    else:
                        code = (True, reply_from_console)
                    
                    GLib.idle_add(on_result, code)
 
                t = threading.Thread(target=get_response, args=(reply_from_console,))
                state["running_threads"].append(t)
                if self.controller.newelle_settings.parallel_tool_execution or restore:
                    t.start()
            except Exception as e:
                error_text = f"Error: {e}"
                if not restore:
                    state["should_continue"] = True
                    formatted = f"[Tool: {tool.name}, ID: {tool_uuid}]\n{error_text}"
                    self.controller.append_chat_message(
                        chat_id,
                        {"User": "Console", "Message": formatted},
                    )
                if current_group() is not None:
                    GLib.idle_add(self._set_tool_slot_state, slot, "error")
                GLib.idle_add(placeholder.set_result, False, error_text)

        run_tool()

    def _process_table(self, chunk, box):
        try:
            box.append(self.create_table(chunk.text.split("\n")))
        except Exception as e:
            print(e)
            box.append(self._create_copybox(chunk.text, "table"))

    def create_table(self, table):
        data = []
        for row in table:
            cells = row.strip("|").split("|")
            data.append([cell.strip() for cell in cells])
        model = Gtk.ListStore(*[str] * len(data[0]))
        for row in data[1:]:
            if not all(len(element.replace(":", "").replace(" ", "").replace("-", "").strip()) == 0 for element in row):
                # Ensure row matches number of columns
                num_columns = len(data[0])
                if len(row) < num_columns:
                    row.extend([""] * (num_columns - len(row)))
                elif len(row) > num_columns:
                    row = row[:num_columns]
                
                r = []
                for element in row: 
                    r.append(simple_markdown_to_pango(LatexNodes2Text().latex_to_text(element)))
                model.append(r)
        treeview = Gtk.TreeView(model=model, css_classes=["toolbar", "view", "transparent"])

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, markup=i)
            treeview.append_column(column)
        scroll = Gtk.ScrolledWindow(child=treeview, propagate_natural_height=True, propagate_natural_width=True, vscrollbar_policy=Gtk.PolicyType.NEVER)
        return scroll

    # --- Execution Logic ---

    def _queue_execution(self, func):
        if self.streaming:
            # Queue
            self.state.setdefault("pending_executions", []).append(func)
        else:
            # Run immediately (restore or not streaming)
            func()

    def finish_streaming(self):
        """Called when streaming finishes to execute pending side effects."""
        self.streaming = False

        for chunk_type, widget, _chunk in self.widgets_map:
            if chunk_type == "text" and not isinstance(widget, list):
                self._stop_stream_fade(widget)
        
        if self.thinking_widget:
            self.thinking_widget.stop_thinking()
            self.thinking_widget = None
        
        self._render_serial = getattr(self, '_render_serial', 0) + 1
        self._ui_sync_content(self.message, self._render_serial)
        
        if "pending_executions" in self.state:
            for func in self.state["pending_executions"]:
                func()
            self.state["pending_executions"] = []

    def _run_console_command(self, cmd, restore, console_reply, expander, state):
         # Logic from window.py _process_console_codeblock closure
         chat_id = self._get_chat_tab().chat_id
         def run_command():
            if not restore:
                code = self._get_main_window().execute_terminal_command(cmd)
                self.controller.append_chat_message(
                    chat_id,
                    {"User": "Console", "Message": " " + str(code[1])},
                )
            else:
                 code = (True, console_reply)
            
            text = f"[User {self._get_main_window().main_path}]:$ {cmd}\n{code[1]}"
            def apply_result():
                 expander.set_child(Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text[:8000], selectable=True))
                 if not code[0]:
                      # Error
                      pass
            GLib.idle_add(apply_result)
         
         t = threading.Thread(target=run_command)
         if self.controller.newelle_settings.parallel_tool_execution or restore:
             t.start()
         state["running_threads"].append(t)


    def _setup_extension_async_response(self, chunk, box, state, restore, extension, widget):
        lang = chunk.lang
        value = chunk.text
        state["has_terminal_command"] = True
        state["id_message"] += 1
        reply_from_console = self.controller.get_console_reply(self._get_chat_tab().chat_id, state["id_message"])
        
        if widget:
             def on_result(code):
                  if not code[0]: pass # Error
        else:
             text_expander = Gtk.Expander(label=lang, css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10, margin_end=10)
             text_expander.set_expanded(False)
             box.append(text_expander)
             def on_result(code):
                  text_expander.set_child(Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=f"{value}\n{code[1]}", selectable=True))

        def get_response():
             if not restore:
                  response = extension.get_answer(value, lang)
                  if response is None: code = (False, _("Stopped"))
                  else:
                       state["should_continue"] = True
                       code = (True, response)
             else:
                  code = (True, reply_from_console)
             
             if not restore or code[1] is not None:
                  GLib.idle_add(on_result, code)
        
        def run_extension():
            t = threading.Thread(target=get_response)
            t.start()
            state["running_threads"].append(t)

        self._queue_execution(run_extension)
