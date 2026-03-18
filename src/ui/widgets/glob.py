import os
from gi.repository import Gtk, Gio, GLib, GObject, Pango, Gdk

from ...utility.media import get_file_icon

class GlobWidget(Gtk.Box):
    """
    A widget for displaying glob search results.
    Shows a list of files matching a glob pattern.
    """

    __gsignals__ = {
        'file-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        pattern: str,
        search_path: str,
        matches: list[str],
        color_scheme: str = "Adwaita-dark",
        open_in_editor_callback=None
    ):
        """
        Initialize the glob widget.

        Args:
            pattern: The glob pattern that was used
            search_path: The directory that was searched
            matches: List of matching file paths
            color_scheme: GtkSourceView color scheme name
            open_in_editor_callback: Optional callable to open file in internal editor
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

        self.pattern = pattern
        self.search_path = search_path
        self.matches = matches
        self.color_scheme = color_scheme
        self.open_in_editor_callback = open_in_editor_callback

        self.loading_spinner = None

        # Build the UI
        self._build_header()
        self._build_loading_view()
        self._build_status()

    def _build_header(self):
        """Build the header row with icon, title, and action buttons."""
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        # Search icon
        search_icon = Gtk.Image.new_from_icon_name("folder-saved-search-symbolic")
        search_icon.set_pixel_size(16)
        search_icon.add_css_class("dim-label")
        header_box.append(search_icon)

        # Title with pattern
        title_label = Gtk.Label(
            label=f"Glob: {self.pattern}",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
            ellipsize=Pango.EllipsizeMode.MIDDLE
        )
        header_box.append(title_label)

        # Copy button
        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy matches to clipboard")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        header_box.append(self.copy_button)

        self.append(header_box)

    def _build_loading_view(self):
        """Build the loading view with a spinner."""
        self.loading_spinner = Gtk.Spinner(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
            width_request=40,
            height_request=40
        )
        self.loading_spinner.start()
        self.append(self.loading_spinner)

    def _build_matches_view(self):
        """Build the list view for displaying matching files."""
        if not self.matches:
            # No matches case
            no_matches_label = Gtk.Label(
                label="No files match the pattern",
                halign=Gtk.Align.CENTER,
                hexpand=True,
                vexpand=True,
                css_classes=["dim-label"]
            )
            self.append(no_matches_label)
            return

        # Limit to 50 matches
        display_matches = sorted(self.matches)[:50]

        # Create list box for matches
        self.list_box = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.list_box.set_margin_top(5)
        self.list_box.set_margin_bottom(5)

        # Add each match as a row
        for file_path in display_matches:
            self._add_file_row(file_path)

        # Scrolled window for the list
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

    def _add_file_row(self, file_path: str):
        """Add a file row to the list box."""
        # Determine if it's a file or directory
        is_dir = os.path.isdir(file_path)

        # Get display name (relative to search path if possible)
        try:
            rel_path = os.path.relpath(file_path, self.search_path)
            display_path = rel_path
        except ValueError:
            display_path = file_path

        # Create row
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
        icon_name = get_file_icon(file_path)
        file_icon = Gtk.Image.new_from_icon_name(icon_name)
        file_icon.set_pixel_size(16)
        row_box.append(file_icon)

        # File path label
        path_label = Gtk.Label(
            label=display_path,
            halign=Gtk.Align.START,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            tooltip_text=file_path
        )
        row_box.append(path_label)

        # Open button (if it's a file)
        if not is_dir and self.open_in_editor_callback:
            open_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
            open_button.set_icon_name("document-open-symbolic")
            open_button.set_tooltip_text("Open in editor")
            open_button.connect("clicked", lambda btn, path=file_path: self._on_open_clicked(path))
            row_box.append(open_button)

        # Open externally button
        external_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        external_button.set_icon_name("window-new-symbolic")
        external_button.set_tooltip_text("Open externally")
        external_button.connect("clicked", lambda btn, path=file_path: self._on_open_externally_clicked(path))
        row_box.append(external_button)

        row.set_child(row_box)
        self.list_box.append(row)

    def _build_status(self):
        """Build status label showing search info."""
        match_count = len(self.matches)
        file_count = sum(1 for m in self.matches if os.path.isfile(m))
        dir_count = match_count - file_count
        displayed_count = min(match_count, 50)

        status_parts = []
        if match_count == 0:
            status_parts.append("No matches")
        elif match_count == 1:
            status_parts.append("1 match")
        else:
            status_text = f"{displayed_count} of {match_count} matches" if match_count > 50 else f"{match_count} matches"
            status_parts.append(status_text)

        if file_count > 0:
            status_parts.append(f"{file_count} files")
        if dir_count > 0:
            status_parts.append(f"{dir_count} directories")

        status_text = " • ".join(status_parts)
        if self.search_path:
            status_text += f" in {self.search_path}"

        self.status_label = Gtk.Label(
            label=status_text,
            halign=Gtk.Align.START,
            css_classes=["dim-label"],
            margin_top=5
        )
        self.append(self.status_label)

    def _on_copy_clicked(self, button):
        """Copy matches list to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return

        clipboard = display.get_clipboard()
        matches_text = "\n".join(sorted(self.matches))
        clipboard.set_content(Gdk.ContentProvider.new_for_value(matches_text))

        # Visual feedback
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def _on_open_clicked(self, file_path: str):
        """Open file in internal editor."""
        if self.open_in_editor_callback:
            self.open_in_editor_callback(file_path)
        self.emit('file-clicked', file_path)

    def _on_open_externally_clicked(self, file_path: str):
        """Open file in external application."""
        try:
            Gio.AppInfo.launch_default_for_uri(f"file://{file_path}", None)
        except GLib.Error as e:
            print(f"Error opening file: {e}")

    def get_matches(self) -> list[str]:
        """Get the list of matching file paths."""
        return self.matches

    def get_pattern(self) -> str:
        """Get the glob pattern used."""
        return self.pattern

    def get_search_path(self) -> str:
        """Get the search path used."""
        return self.search_path

    def set_matches(self, matches: list[str]):
        """Update the matches and rebuild the matches view and status."""
        self.matches = matches

        # Remove loading spinner, existing content and status
        for child in list(self):
            if isinstance(child, (Gtk.Spinner, Gtk.ScrolledWindow, Gtk.Label)):
                self.remove(child)

        # Rebuild content and status
        self._build_matches_view()
        self._build_status()
