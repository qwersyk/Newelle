import threading
from gettext import gettext as _

from gi.repository import Gtk, Adw, GLib
from .markuptextview import MarkupTextView
from .copybox import CopyBox
from .tool import ToolWidget
from ...utility.message_chunk import get_message_chunks


class SubagentWidget(Gtk.ListBox):
    """Widget displayed in chat showing a subagent's execution progress."""

    def __init__(self, task_summary: str):
        super().__init__()
        self.add_css_class("boxed-list")
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)

        self._task_summary = task_summary
        self._current_message = ""
        self.widgets_map = []
        self._state_lock = threading.Lock()
        self._pending_message = None
        self._message_update_queued = False

        # Expander row header
        self.expander_row = Adw.ExpanderRow(
            title=self._truncate_title(task_summary),
            subtitle=_("Starting…"),
            icon_name="system-run-symbolic",
        )

        # Spinner shown while running
        self.spinner = Gtk.Spinner(spinning=True, visible=True)
        self.expander_row.add_suffix(self.spinner)

        # Finished icon (hidden initially)
        self.finished_icon = Gtk.Image(
            icon_name="object-select-symbolic",
            visible=False,
        )
        self.finished_icon.add_css_class("success")
        self.expander_row.add_suffix(self.finished_icon)

        # Container box inside the expander for rendered chunks
        self.content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )

        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=300,
            max_content_height=600,
            child=self.content_box,
        )
        scrolled.add_css_class("expander-inset-content")
        self._scrolled = scrolled
        self.expander_row.add_row(scrolled)

        self.append(self.expander_row)

    @staticmethod
    def _truncate_title(text: str, max_len: int = 60) -> str:
        text = text.replace("\n", " ").strip()
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text

    def set_status(self, status: str):
        """Update the subtitle status text."""
        self._run_on_main_thread(self._ui_set_status, status)

    def update_message(self, full_text: str):
        """Re-render the message content from the full text using chunk parsing."""
        with self._state_lock:
            self._pending_message = full_text
            if self._message_update_queued:
                return
            self._message_update_queued = True

        self._run_on_main_thread(self._drain_message_updates)

    def add_tool_widget(self, tool_name: str, tool_result):
        """Add a tool widget showing the result of a tool call."""
        self._run_on_main_thread(self._ui_add_tool_widget, tool_name, tool_result)

    def finish(self, success: bool = True, summary: str = ""):
        """Mark the subagent execution as finished."""
        self._run_on_main_thread(self._ui_finish, success, summary)

    # ---- GTK main-thread helpers ----

    def _run_on_main_thread(self, callback, *args):
        if threading.current_thread() is threading.main_thread():
            callback(*args)
            return
        GLib.idle_add(callback, *args)

    def _drain_message_updates(self):
        while True:
            with self._state_lock:
                full_text = self._pending_message
                self._pending_message = None

            if full_text is not None:
                self._current_message = full_text
                self._ui_update_message(full_text)

            with self._state_lock:
                if self._pending_message is None:
                    self._message_update_queued = False
                    break

        return GLib.SOURCE_REMOVE

    def _ui_set_status(self, status: str):
        if not self.get_display():
            return GLib.SOURCE_REMOVE
        self.expander_row.set_subtitle(status)
        return GLib.SOURCE_REMOVE

    def _ui_update_message(self, full_text: str):
        """Parse the full text into chunks and update widgets in place."""
        if not self.get_display():
            return GLib.SOURCE_REMOVE

        chunks = get_message_chunks(full_text, allow_latex=False)
        current_widget_idx = 0

        for chunk in chunks:
            if current_widget_idx < len(self.widgets_map):
                widget_type, widget, widget_chunk = self.widgets_map[current_widget_idx]
                if self._can_update_widget(widget_type, widget, widget_chunk, chunk):
                    self._update_widget(widget, widget_type, chunk)
                    self.widgets_map[current_widget_idx] = (widget_type, widget, chunk)
                    current_widget_idx += 1
                    continue

                self._remove_widgets_from(current_widget_idx)

            self._process_chunk(chunk)
            current_widget_idx = len(self.widgets_map)

        if current_widget_idx < len(self.widgets_map):
            self._remove_widgets_from(current_widget_idx)

        self._scroll_to_end()
        return GLib.SOURCE_REMOVE

    def _can_update_widget(self, widget_type, widget, previous_chunk, chunk):
        if widget_type == "tool_result":
            return chunk.type == "tool_call" and getattr(previous_chunk, "tool_name", None) == getattr(chunk, "tool_name", None)

        if widget_type in ("text", "markdown") and chunk.type in ("text", "markdown") and isinstance(widget, MarkupTextView):
            return True

        if widget_type == "codeblock" and chunk.type == "codeblock" and isinstance(widget, CopyBox):
            return True

        if widget_type == "tool_call" and chunk.type == "tool_call" and isinstance(widget, ToolWidget):
            return getattr(previous_chunk, "tool_name", None) == getattr(chunk, "tool_name", None)

        if widget_type == "thinking" and chunk.type == "thinking":
            from .thinking import ThinkingWidget
            return isinstance(widget, ThinkingWidget)

        if widget_type == "divider" and chunk.type == "divider":
            return True

        return False

    def _update_widget(self, widget, widget_type, chunk):
        if widget_type in ("text", "markdown") and isinstance(widget, MarkupTextView):
            buf = widget.get_buffer()
            updated_text = chunk.text.strip()
            start_iter = buf.get_start_iter()
            end_iter = buf.get_end_iter()
            if buf.get_text(start_iter, end_iter, True) != updated_text:
                buf.set_text(updated_text, -1)
        elif widget_type == "codeblock" and isinstance(widget, CopyBox):
            widget.update_code(chunk.text)
            if chunk.lang and chunk.lang != widget.get_language():
                widget.set_language(chunk.lang)
        elif widget_type == "tool_call" and isinstance(widget, ToolWidget):
            widget.chunk_text = chunk.text
        elif widget_type == "thinking":
            widget.set_thinking(chunk.text)

    def _process_chunk(self, chunk):
        start_children = self._observe_children()
        widget_type = chunk.type

        if chunk.type in ("text", "markdown"):
            text = chunk.text.strip()
            if not text:
                return
            tv = MarkupTextView(
                editable=False,
                cursor_visible=False,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
                hexpand=True,
                pixels_below_lines=3,
                left_margin=6,
                right_margin=6,
                top_margin=4,
                bottom_margin=4,
            )
            tv.get_buffer().set_text(text, -1)
            self.content_box.append(tv)
        elif chunk.type == "codeblock":
            self.content_box.append(CopyBox(chunk.text, chunk.lang or ""))
        elif chunk.type == "tool_call":
            self.content_box.append(ToolWidget(chunk.tool_name, chunk.text))
        elif chunk.type == "thinking":
            from .thinking import ThinkingWidget
            tw = ThinkingWidget()
            tw.set_thinking(chunk.text)
            self.content_box.append(tw)
        elif chunk.type == "divider":
            self.content_box.append(Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL,
                margin_top=6,
                margin_bottom=6,
            ))
        elif chunk.text and chunk.text.strip():
            tv = MarkupTextView(
                editable=False,
                cursor_visible=False,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
                hexpand=True,
                left_margin=6,
                right_margin=6,
                top_margin=4,
                bottom_margin=4,
            )
            tv.get_buffer().set_text(chunk.text, -1)
            self.content_box.append(tv)
            widget_type = "text"
        else:
            return

        end_children = self._observe_children()
        new_widgets = [child for child in end_children if child not in start_children]
        if len(new_widgets) == 1:
            self.widgets_map.append((widget_type, new_widgets[0], chunk))
        elif len(new_widgets) > 1:
            self.widgets_map.append(("complex", new_widgets, chunk))

    def _ui_add_tool_widget(self, tool_name: str, tool_result):
        """Update or replace the existing ToolWidget for a tool call with its result."""
        if not self.get_display():
            return GLib.SOURCE_REMOVE

        widget_idx = self._find_tool_widget_index(tool_name)
        existing_tw = None
        previous_chunk = None
        if widget_idx is not None:
            _, existing_tw, previous_chunk = self.widgets_map[widget_idx]

        if tool_result.widget is not None:
            if existing_tw is not None:
                parent = existing_tw.get_parent()
                if parent:
                    parent.insert_child_after(tool_result.widget, existing_tw)
                    parent.remove(existing_tw)
                    self.widgets_map[widget_idx] = ("tool_result", tool_result.widget, previous_chunk)
            else:
                self.content_box.append(tool_result.widget)
                self.widgets_map.append(("tool_result", tool_result.widget, previous_chunk))
        elif existing_tw is not None:
            output = tool_result.output if tool_result.output else ""
            if isinstance(existing_tw, ToolWidget):
                existing_tw.set_result(True, str(output))
        else:
            tw = ToolWidget(tool_name)
            output = tool_result.output if tool_result.output else ""
            tw.set_result(True, str(output))
            self.content_box.append(tw)
            self.widgets_map.append(("tool_result", tw, previous_chunk))

        self._scroll_to_end()
        return GLib.SOURCE_REMOVE

    def _find_tool_widget_index(self, tool_name: str):
        for index in range(len(self.widgets_map) - 1, -1, -1):
            widget_type, _, chunk = self.widgets_map[index]
            if widget_type not in ("tool_call", "tool_result"):
                continue
            if getattr(chunk, "tool_name", None) == tool_name:
                return index
        return None

    def _observe_children(self):
        children = []
        child = self.content_box.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        return children

    def _remove_widgets_from(self, start_index):
        while len(self.widgets_map) > start_index:
            _, widget_or_widgets, _ = self.widgets_map.pop()
            if isinstance(widget_or_widgets, list):
                for widget in widget_or_widgets:
                    self.content_box.remove(widget)
            else:
                self.content_box.remove(widget_or_widgets)

    def _scroll_to_end(self):
        """Scroll the content area to the bottom."""
        adj = self._scrolled.get_vadjustment()
        if adj:
            adj.set_value(adj.get_upper())

    def _ui_finish(self, success: bool, summary: str):
        if not self.get_display():
            return GLib.SOURCE_REMOVE
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.finished_icon.set_visible(True)
        if not success:
            self.finished_icon.set_from_icon_name("dialog-error-symbolic")
            self.finished_icon.remove_css_class("success")
            self.finished_icon.add_css_class("error")
        status = summary if summary else (_("Completed") if success else _("Failed"))
        self.expander_row.set_subtitle(status)
        return GLib.SOURCE_REMOVE
