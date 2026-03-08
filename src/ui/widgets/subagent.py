from gi.repository import Gtk, Adw, Pango, GLib
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
        self._rendered_chunks_count = 0

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
        GLib.idle_add(self._ui_set_status, status)

    def update_message(self, full_text: str):
        """Re-render the message content from the full text using chunk parsing."""
        self._current_message = full_text
        GLib.idle_add(self._ui_update_message, full_text)

    def add_tool_widget(self, tool_name: str, tool_result):
        """Add a tool widget showing the result of a tool call."""
        GLib.idle_add(self._ui_add_tool_widget, tool_name, tool_result)

    def finish(self, success: bool = True, summary: str = ""):
        """Mark the subagent execution as finished."""
        GLib.idle_add(self._ui_finish, success, summary)

    # ---- GTK main-thread helpers ----

    def _ui_set_status(self, status: str):
        if not self.get_display():
            return GLib.SOURCE_REMOVE
        self.expander_row.set_subtitle(status)
        return GLib.SOURCE_REMOVE

    def _ui_update_message(self, full_text: str):
        """Parse the full text into chunks and render them."""
        if not self.get_display():
            return GLib.SOURCE_REMOVE

        chunks = get_message_chunks(full_text, allow_latex=False)

        # Update existing widgets where possible, append new ones
        child_widgets = []
        child = self.content_box.get_first_child()
        while child is not None:
            child_widgets.append(child)
            child = child.get_next_sibling()

        widget_idx = 0
        for chunk in chunks:
            if widget_idx < len(child_widgets):
                existing = child_widgets[widget_idx]
                if self._can_update_existing(existing, chunk):
                    self._update_existing_widget(existing, chunk)
                    widget_idx += 1
                    continue
                # Mismatch: remove remaining widgets and rebuild from here
                for w in child_widgets[widget_idx:]:
                    self.content_box.remove(w)
                child_widgets = child_widgets[:widget_idx]

            # Create new widget for this chunk
            new_widget = self._create_chunk_widget(chunk)
            if new_widget is not None:
                self.content_box.append(new_widget)
                child_widgets.append(new_widget)
            widget_idx = len(child_widgets)

        # Remove trailing old widgets
        for w in child_widgets[widget_idx:]:
            self.content_box.remove(w)

        # Scroll to bottom
        self._scroll_to_end()
        return GLib.SOURCE_REMOVE

    def _can_update_existing(self, widget, chunk):
        """Check if an existing widget can be updated in-place for the new chunk."""
        if chunk.type in ("text", "markdown") and isinstance(widget, MarkupTextView):
            return True
        if chunk.type == "codeblock" and isinstance(widget, CopyBox):
            return True
        return False

    def _update_existing_widget(self, widget, chunk):
        """Update an existing widget with new chunk content."""
        if isinstance(widget, MarkupTextView):
            buf = widget.get_buffer()
            buf.set_text(chunk.text, -1)
        elif isinstance(widget, CopyBox):
            widget.update_code(chunk.text)
            if chunk.lang and chunk.lang != widget.get_language():
                widget.set_language(chunk.lang)

    def _create_chunk_widget(self, chunk):
        """Create a GTK widget for a message chunk."""
        if chunk.type in ("text", "markdown"):
            text = chunk.text.strip()
            if not text:
                return None
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
            return tv
        elif chunk.type == "codeblock":
            return CopyBox(chunk.text, chunk.lang or "")
        elif chunk.type == "tool_call":
            tw = ToolWidget(chunk.tool_name, chunk.text)
            return tw
        elif chunk.type == "thinking":
            from .thinking import ThinkingWidget
            tw = ThinkingWidget()
            tw.set_thinking(chunk.text)
            return tw
        # For unknown types, render as plain text
        if chunk.text and chunk.text.strip():
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
            return tv
        return None

    def _ui_add_tool_widget(self, tool_name: str, tool_result):
        """Update or replace the existing ToolWidget for a tool call with its result."""
        if not self.get_display():
            return GLib.SOURCE_REMOVE

        # Find the last ToolWidget matching this tool name
        existing_tw = None
        child = self.content_box.get_first_child()
        while child is not None:
            if isinstance(child, ToolWidget):
                existing_tw = child
            child = child.get_next_sibling()

        if tool_result.widget is not None:
            # Tool provides its own widget — swap out the existing ToolWidget
            if existing_tw is not None:
                parent = existing_tw.get_parent()
                if parent:
                    # Insert the custom widget right before removing the old one
                    parent.insert_child_after(tool_result.widget, existing_tw)
                    parent.remove(existing_tw)
            else:
                self.content_box.append(tool_result.widget)
        elif existing_tw is not None:
            # Update the existing ToolWidget in-place with the result
            output = tool_result.output if tool_result.output else ""
            existing_tw.set_result(True, str(output))
        else:
            # No existing widget found — create a new one
            tw = ToolWidget(tool_name)
            output = tool_result.output if tool_result.output else ""
            tw.set_result(True, str(output))
            self.content_box.append(tw)

        self._scroll_to_end()
        return GLib.SOURCE_REMOVE

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
