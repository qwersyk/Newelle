"""Folder row widget for the chat sidebar"""
import gettext
from gi.repository import Gtk, Gdk, GLib, GObject, Pango

_ = gettext.gettext


class FolderRow(Gtk.ListBoxRow):
    """A collapsible folder row that accepts chat drops"""

    def __init__(self, folder_id: int, folder_name: str, folder_color: str,
                 folder_icon: str = "folder-symbolic", expanded: bool = True):
        super().__init__()
        self.folder_id = folder_id
        self.folder_name = folder_name
        self.folder_color = folder_color
        self.folder_icon = folder_icon
        self.is_expanded = expanded
        self.add_css_class("folder-row")
        self.set_selectable(False)
        self.set_activatable(True)

        self.main_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=8,
            margin_end=6,
        )
        self.set_child(self.main_box)

        # Expand/collapse chevron
        chevron_icon = "pan-down-symbolic" if expanded else "pan-end-symbolic"
        self.chevron = Gtk.Image.new_from_icon_name(chevron_icon)
        self.chevron.add_css_class("dim-label")
        self.main_box.append(self.chevron)

        # Colored folder icon
        self.icon_widget = Gtk.Image.new_from_icon_name(folder_icon)
        self.icon_widget.set_pixel_size(16)
        self._apply_icon_color()
        self.main_box.append(self.icon_widget)

        # Folder name
        self.name_label = Gtk.Label(
            label=folder_name,
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=25,
        )
        self.name_label.add_css_class("heading")
        self.main_box.append(self.name_label)

        # Actions revealer (shown on hover)
        self.actions_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=150,
            reveal_child=False,
        )
        self.main_box.append(self.actions_revealer)

        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self.actions_revealer.set_child(actions_box)

        self.edit_button = Gtk.Button(
            icon_name="document-edit-symbolic",
            css_classes=["flat", "circular", "success"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Edit folder"),
        )
        actions_box.append(self.edit_button)

        self.delete_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "circular", "error"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Delete folder"),
        )
        actions_box.append(self.delete_button)

        # Hover controller
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._on_hover_enter)
        hover.connect("leave", self._on_hover_leave)
        self.add_controller(hover)

        # Drop target for receiving dragged chats
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("enter", self._on_drop_enter)
        drop_target.connect("leave", self._on_drop_leave)
        drop_target.connect("drop", self._on_drop)
        self.add_controller(drop_target)

        self._on_drop_callback = None

    def _apply_icon_color(self):
        """Tint the folder icon with the folder's color via a per-instance CSS class."""
        css_class = f"folder-icon-{self.folder_id}"
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            f".{css_class} {{ color: {self.folder_color}; }}".encode()
        )
        self.icon_widget.add_css_class(css_class)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )

    def set_expanded(self, expanded: bool):
        self.is_expanded = expanded
        icon = "pan-down-symbolic" if expanded else "pan-end-symbolic"
        self.chevron.set_from_icon_name(icon)

    def connect_signals(self, on_edit, on_delete, on_drop_chat):
        self.edit_button.connect("clicked", on_edit)
        self.delete_button.connect("clicked", on_delete)
        self._on_drop_callback = on_drop_chat

    def _on_hover_enter(self, controller, x, y):
        self.actions_revealer.set_reveal_child(True)

    def _on_hover_leave(self, controller):
        self.actions_revealer.set_reveal_child(False)

    def _on_drop_enter(self, drop_target, x, y):
        self.add_css_class("folder-row-drop-hover")
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, drop_target):
        self.remove_css_class("folder-row-drop-hover")

    def _on_drop(self, drop_target, value, x, y):
        self.remove_css_class("folder-row-drop-hover")
        if self._on_drop_callback and value:
            try:
                chat_id = int(value)
                self._on_drop_callback(chat_id, self.folder_id)
                return True
            except (ValueError, TypeError):
                pass
        return False
