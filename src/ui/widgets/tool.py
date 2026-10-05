import gettext

from gi.repository import Gtk, Adw, Pango

from ...utility.tool_call_group import ToolCallGroupState
from ...utility.message_chunk import get_message_chunks
from ...ui import append_image_codeblock


_ = gettext.gettext
_n = gettext.ngettext
_DEFAULT_TOOL_ICON = "tools-symbolic"


class ToolCallSlot(Gtk.Box):
    """Stable visual slot for a single tool call.

    Keeping the slot alive while its child changes lets compact mode move the
    same call between the normal message layout and the shared expander.
    """

    def __init__(
        self,
        entry_id,
        tool_name,
        tool_title,
        tool_icon_name,
        chunk,
        widget,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.entry_id = entry_id
        self.tool_name = tool_name
        self.tool_title = tool_title
        self.tool_icon_name = tool_icon_name or _DEFAULT_TOOL_ICON
        self.chunk = chunk
        self.widget = None
        self.active = True
        self.group = None
        self.message_id = None
        self.set_hexpand(True)
        self.set_widget(widget)

    def set_widget(self, widget):
        old_widget = self.widget
        if old_widget is not None and old_widget.get_parent() is self:
            self.remove(old_widget)
        self.widget = widget
        if widget is not None:
            parent = widget.get_parent()
            if parent is not None and parent is not self:
                parent.remove(widget)
            self.append(widget)

    def update_chunk(self, chunk):
        self.chunk = chunk
        if isinstance(self.widget, ToolWidget):
            self.widget.chunk_text = chunk.text


class ToolCallsGroupWidget(Gtk.ListBox):
    """One expandable summary for every tool call in an agent iteration."""

    def __init__(self):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE)
        self.add_css_class("boxed-list")
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)
        self.state = ToolCallGroupState()
        self.slots = []
        self.auxiliary_widgets = []
        # The first Message that owns this group controls where the expander
        # itself is inserted. Slots may subsequently originate in later
        # continuation messages while remaining in this same group.
        self.owner_message = None

        self.expander_row = Adw.ExpanderRow(
            title=_("Tool calls"),
            subtitle=_("No tool calls"),
            expanded=False,
        )
        self.running_spinner = Gtk.Spinner(spinning=False, visible=False)
        self.active_tool_icon = Gtk.Image(icon_name=_DEFAULT_TOOL_ICON)
        self.expander_row.add_prefix(self.running_spinner)
        self.expander_row.add_prefix(self.active_tool_icon)

        self.content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            min_content_height=400,
            max_content_height=800,
            child=self.content_box,
        )
        scrolled.add_css_class("expander-inset-content")
        self.expander_row.add_row(scrolled)
        self.append(self.expander_row)

    def register_call(
        self,
        tool_name,
        tool_title,
        chunk,
        widget,
        tool_icon_name=None,
    ):
        entry_id = self.state.add(tool_title or tool_name)
        slot = ToolCallSlot(
            entry_id,
            tool_name,
            tool_title or tool_name,
            tool_icon_name,
            chunk,
            widget,
        )
        slot.group = self
        self.slots.append(slot)
        self.content_box.append(slot)
        self._update_header()
        return slot

    def append_slot(self, slot):
        parent = slot.get_parent()
        if parent is self.content_box:
            self._reorder_slots()
            return
        if parent is not None:
            parent.remove(slot)
        self.content_box.append(slot)
        self._reorder_slots()

    def append_auxiliary_widget(self, widget, order):
        """Place intermediate assistant text inside the shared tool box.

        These widgets are deliberately kept outside ``state`` so they do not
        affect the tool-call count or status header.
        """
        if widget not in self.auxiliary_widgets:
            self.auxiliary_widgets.append(widget)
        widget._compact_order = order
        parent = widget.get_parent()
        if parent is not self.content_box:
            if parent is not None:
                parent.remove(widget)
            self.content_box.append(widget)
        self._reorder_content()

    def remove_auxiliary_widget(self, widget):
        if widget in self.auxiliary_widgets:
            self.auxiliary_widgets.remove(widget)
        if widget.get_parent() is self.content_box:
            self.content_box.remove(widget)

    def _reorder_content(self):
        children = []
        child = self.content_box.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        if len(children) < 2:
            return
        children.sort(
            key=lambda widget: getattr(
                widget,
                "_compact_order",
                (float("inf"), float("inf")),
            )
        )
        for widget in children:
            self.content_box.remove(widget)
            self.content_box.append(widget)

    def _reorder_slots(self):
        """Keep slots in assistant-message/call order when lazy history loads."""
        if len(self.slots) < 2:
            return
        ordered = sorted(
            enumerate(self.slots),
            key=lambda item: (
                item[1].message_id if item[1].message_id is not None else float("inf"),
                item[0],
            ),
        )
        for _index, slot in ordered:
            self.content_box.remove(slot)
            self.content_box.append(slot)
        self.state.reorder([slot.entry_id for _index, slot in ordered])
        self._reorder_content()
        self._update_header()

    def detach_slot(self, slot):
        """Remove a slot from this group without invalidating the slot."""
        if slot not in self.slots:
            return False
        status = self.state.status_for(slot.entry_id)
        self.slots.remove(slot)
        self.state.remove(slot.entry_id)
        if slot.get_parent() is self.content_box:
            self.content_box.remove(slot)
        slot.group = None
        slot.active = True
        self._update_header()
        return status

    def adopt_slot(self, slot, status=None):
        """Adopt a live slot from another group, preserving its status."""
        if slot in self.slots:
            return True
        if status is None and slot.group is not None:
            status = slot.group.state.status_for(slot.entry_id)
        if status is None:
            status = "pending"
        if slot.group is not None and slot.group is not self:
            slot.group.detach_slot(slot)
        slot.entry_id = self.state.adopt(slot.tool_title or slot.tool_name, status)
        slot._compact_order = (
            slot.message_id if slot.message_id is not None else float("inf"),
            slot.entry_id,
        )
        slot.group = self
        slot.active = True
        self.slots.append(slot)
        self.content_box.append(slot)
        self._reorder_slots()
        self._update_header()
        return True

    def replace_slot_widget(self, slot, widget):
        if slot not in self.slots or not slot.active:
            return False
        slot.set_widget(widget)
        return True

    def update_slot(self, slot, chunk):
        if slot not in self.slots or not slot.active:
            return False
        slot.update_chunk(chunk)
        return True

    def set_slot_state(self, slot, status):
        if slot not in self.slots or not slot.active:
            return False
        changed = self.state.set_status(slot.entry_id, status)
        self._update_header()
        return changed

    def remove_slot(self, slot):
        if slot not in self.slots:
            return False
        slot.active = False
        self.state.remove(slot.entry_id)
        self.slots.remove(slot)
        parent = slot.get_parent()
        if parent is not None:
            parent.remove(slot)
        self._update_header()
        return True

    def expand_for_interaction(self):
        self.expander_row.set_expanded(True)

    def _update_header(self):
        summary = self.state.summary()
        count = summary.count
        active_slot = next(
            (
                slot
                for slot in self.slots
                if slot.entry_id == summary.active_entry_id
            ),
            None,
        )
        if active_slot is not None:
            self.running_spinner.set_visible(True)
            self.running_spinner.start()
            self.active_tool_icon.set_from_icon_name(
                active_slot.tool_icon_name or _DEFAULT_TOOL_ICON
            )
        else:
            self.running_spinner.stop()
            self.running_spinner.set_visible(False)
            self.active_tool_icon.set_from_icon_name(_DEFAULT_TOOL_ICON)

        if count == 0:
            self.expander_row.set_title(_("Tool calls"))
            self.expander_row.set_subtitle(_("No tool calls"))
            return

        if summary.active_tool_name:
            title = _("Running tool {tool}").format(tool=summary.active_tool_name)
        elif summary.failed_count:
            title = _("Tool calls completed with errors")
        elif summary.cancelled_count:
            title = _("Tool calls cancelled")
        else:
            title = _("Tool calls completed")

        subtitle = _n(
            "%(count)d tool call",
            "%(count)d tool calls",
            count,
        ) % {"count": count}
        self.expander_row.set_title(title)
        self.expander_row.set_subtitle(subtitle)

class ToolWidget(Gtk.ListBox):
    def __init__(self, tool_name, chunk_text=""):
        super().__init__()
        self.add_css_class("boxed-list")
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)
        self.expander_row = Adw.ExpanderRow(
            title=tool_name,
            subtitle="Running...",
            icon_name="tools-symbolic",
        )
        self.append(self.expander_row)
        self.chunk_text = chunk_text

    def set_result(self, success, result_text, context_messages=None):
        if not self.get_display():
            return
        self.expander_row.set_subtitle("Completed" if success else "Error")
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        full_text = self.chunk_text + "\n" + str(result_text)
        display_text = full_text[:8000] if len(full_text) > 8000 else full_text
        label = Gtk.Label(
            label=display_text,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            xalign=0,
        )
        content_box.append(label)
        for message in context_messages or []:
            for chunk in get_message_chunks(message, allow_latex=False):
                if chunk.type == "codeblock" and chunk.lang == "image":
                    append_image_codeblock(chunk.text, content_box)
        self.expander_row.add_row(content_box)
