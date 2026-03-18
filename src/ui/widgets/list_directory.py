import os
from gi.repository import Gtk, Gio, GLib, GObject, Pango, Gdk

from ...utility.media import get_file_icon


def _get_folder_icon(folder_name: str) -> str:
    """Get icon for a directory based on its name."""
    special_folders = {
        "Desktop": "user-desktop-symbolic",
        "Documents": "folder-documents-symbolic",
        "Downloads": "folder-download-symbolic",
        "Music": "folder-music-symbolic",
        "Pictures": "folder-pictures-symbolic",
        "Public": "folder-publicshare-symbolic",
        "Templates": "folder-templates-symbolic",
        "Videos": "folder-videos-symbolic",
    }
    return special_folders.get(folder_name, "folder-symbolic")


class ListDirectoryWidget(Gtk.Box):
    """
    A widget for displaying directory contents.
    Shows files and subdirectories in a list with icons.
    """

    __gsignals__ = {
        'file-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        dir_path: str,
        entries: list[str],
        open_in_editor_callback=None
    ):
        """
        Initialize the list directory widget.

        Args:
            dir_path: The directory that was listed
            entries: List of entry names (files and subdirs) in the directory
            open_in_editor_callback: Optional callable(path) to open file in internal editor
        """
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            css_classes=["osd", "toolbar", "code"]
        )

        self.dir_path = dir_path
        self.entries = entries
        self.open_in_editor_callback = open_in_editor_callback

        # Build the UI
        self._build_header()
        self._build_entries_view()
        self._build_status()

    def _build_header(self):
        """Build the header row with icon, title, and action buttons."""
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        # Folder icon
        folder_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        folder_icon.set_pixel_size(16)
        folder_icon.add_css_class("dim-label")
        header_box.append(folder_icon)

        # Title with directory name
        dir_name = os.path.basename(self.dir_path) or self.dir_path
        title_label = Gtk.Label(
            label=dir_name,
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            tooltip_text=self.dir_path
        )
        header_box.append(title_label)

        # Copy button
        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy paths to clipboard")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        header_box.append(self.copy_button)

        self.append(header_box)

    def _build_entries_view(self):
        """Build the list view for displaying directory entries."""
        if not self.entries:
            no_entries_label = Gtk.Label(
                label="Directory is empty",
                halign=Gtk.Align.CENTER,
                hexpand=True,
                vexpand=True,
                css_classes=["dim-label"]
            )
            self.append(no_entries_label)
            return

        self.list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.list_box.set_margin_top(5)
        self.list_box.set_margin_bottom(5)

        for entry_name in sorted(self.entries)[:50]:
            self._add_entry_row(entry_name)

        scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            propagate_natural_height=True,
            max_content_height=300,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        scroll.set_child(self.list_box)
        self.append(scroll)

    def _add_entry_row(self, entry_name: str):
        """Add an entry row to the list box."""
        full_path = os.path.join(self.dir_path, entry_name)
        is_dir = os.path.isdir(full_path)

        row = Gtk.ListBoxRow(
            css_classes=["activatable"],
            selectable=False
        )

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.set_margin_start(10)
        row_box.set_margin_end(10)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)

        # Icon based on type
        if is_dir:
            icon_name = _get_folder_icon(entry_name)
        else:
            icon_name = get_file_icon(entry_name)
        file_icon = Gtk.Image.new_from_icon_name(icon_name)
        file_icon.set_pixel_size(16)
        row_box.append(file_icon)

        # Entry name label
        path_label = Gtk.Label(
            label=entry_name,
            halign=Gtk.Align.START,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            tooltip_text=full_path
        )
        row_box.append(path_label)

        # Open in editor button (files only)
        if not is_dir and self.open_in_editor_callback:
            open_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
            open_button.set_icon_name("document-open-symbolic")
            open_button.set_tooltip_text("Open in editor")
            open_button.connect("clicked", lambda btn, p=full_path: self._on_open_clicked(p))
            row_box.append(open_button)

        # Open externally button
        external_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        external_button.set_icon_name("window-new-symbolic")
        external_button.set_tooltip_text("Open externally")
        external_button.connect("clicked", lambda btn, p=full_path: self._on_open_externally_clicked(p))
        row_box.append(external_button)

        row.set_child(row_box)
        self.list_box.append(row)

    def _build_status(self):
        """Build status label showing directory info."""
        file_count = sum(1 for e in self.entries if os.path.isfile(os.path.join(self.dir_path, e)))
        dir_count = len(self.entries) - file_count
        displayed_count = min(len(self.entries), 50)

        status_parts = []
        if len(self.entries) == 0:
            status_parts.append("Empty")
        else:
            if len(self.entries) > 50:
                status_parts.append(f"{displayed_count} of {len(self.entries)} entries")
            else:
                status_parts.append(f"{len(self.entries)} entries")
        if file_count > 0:
            status_parts.append(f"{file_count} files")
        if dir_count > 0:
            status_parts.append(f"{dir_count} directories")

        status_text = " • ".join(status_parts)
        status_text += f" in {self.dir_path}"

        self.status_label = Gtk.Label(
            label=status_text,
            halign=Gtk.Align.START,
            css_classes=["dim-label"],
            margin_top=5
        )
        self.append(self.status_label)

    def _on_copy_clicked(self, button):
        """Copy entry paths to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return

        full_paths = [os.path.join(self.dir_path, e) for e in sorted(self.entries)]
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value("\n".join(full_paths)))

        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def _on_open_clicked(self, file_path: str):
        """Open file in internal editor."""
        if self.open_in_editor_callback:
            self.open_in_editor_callback(file_path)
        self.emit('file-clicked', file_path)

    def _on_open_externally_clicked(self, file_path: str):
        """Open file/directory in external application."""
        try:
            Gio.AppInfo.launch_default_for_uri(f"file://{file_path}", None)
        except GLib.Error as e:
            print(f"Error opening: {e}")

    def set_entries(self, entries: list[str]):
        """Update the entries and rebuild the entries view and status."""
        self.entries = entries

        # Remove existing content and status
        for child in list(self):
            if isinstance(child, (Gtk.ScrolledWindow, Gtk.Label)):
                self.remove(child)

        # Rebuild content and status
        self._build_entries_view()
        self._build_status()
