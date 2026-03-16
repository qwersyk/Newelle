import os
from gi.repository import Gtk, GtkSource, Gio, GLib, GObject, Pango, Gdk


class GrepWidget(Gtk.Box):
    """
    A widget for displaying grep/search results.
    Shows matching lines with file path, line number, and content.
    """

    __gsignals__ = {
        'file-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
    }

    def __init__(
        self,
        pattern: str,
        search_path: str,
        matches: list[tuple[str, int, str]],
        color_scheme: str = "Adwaita-dark",
        open_in_editor_callback=None
    ):
        """
        Initialize the grep widget.

        Args:
            pattern: The regex pattern that was searched
            search_path: The path (file or directory) that was searched
            matches: List of (file_path, line_number, line_content) tuples
            color_scheme: GtkSourceView color scheme name
            open_in_editor_callback: Optional callable(path, line?) to open file in internal editor
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

        self._build_header()
        self._build_content_view()
        self._build_status()

    def _build_header(self):
        """Build the header row with icon, title, and action buttons."""
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        search_icon = Gtk.Image.new_from_icon_name("edit-find-symbolic")
        search_icon.set_pixel_size(16)
        search_icon.add_css_class("dim-label")
        header_box.append(search_icon)

        title_label = Gtk.Label(
            label=f"Grep: {self.pattern[:50]}{'…' if len(self.pattern) > 50 else ''}",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            tooltip_text=self.pattern
        )
        header_box.append(title_label)

        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy results to clipboard")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        header_box.append(self.copy_button)

        if self.matches and self.open_in_editor_callback:
            open_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
            open_button.set_icon_name("document-open-symbolic")
            open_button.set_tooltip_text("Open first match in editor")
            open_button.connect("clicked", self._on_open_clicked)
            header_box.append(open_button)

        self.append(header_box)

    def _build_content_view(self):
        """Build the content view with grep results."""
        if not self.matches:
            no_matches_label = Gtk.Label(
                label="No matches found",
                halign=Gtk.Align.CENTER,
                hexpand=True,
                vexpand=True,
                css_classes=["dim-label"]
            )
            self.append(no_matches_label)
            return

        # Build display text in grep format: file:line:content
        lines = []
        for file_path, line_num, content in self.matches:
            try:
                rel_path = os.path.relpath(file_path, self.search_path)
            except ValueError:
                rel_path = file_path
            lines.append(f"{rel_path}:{line_num}:{content.rstrip()}")

        display_text = "\n".join(lines)

        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(display_text)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        if style_scheme:
            self.buffer.set_style_scheme(style_scheme)

        self.sourceview = GtkSource.View(monospace=True)
        self.sourceview.set_hexpand(True)
        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_editable(False)
        self.sourceview.set_cursor_visible(False)
        self.sourceview.set_show_line_numbers(True)
        self.sourceview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.sourceview.set_top_margin(6)
        self.sourceview.set_bottom_margin(6)
        self.sourceview.set_left_margin(6)
        self.sourceview.set_right_margin(6)

        scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            propagate_natural_height=True,
            max_content_height=300,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True
        )
        scroll.set_child(self.sourceview)
        self.append(scroll)

    def _build_status(self):
        """Build status label."""
        match_count = len(self.matches)
        file_count = len({m[0] for m in self.matches}) if self.matches else 0

        if match_count == 0:
            status_text = "No matches"
        elif match_count == 1:
            status_text = "1 match"
        else:
            status_text = f"{match_count} matches"
        if file_count > 0:
            status_text += f" in {file_count} file{'s' if file_count > 1 else ''}"
        status_text += f" in {self.search_path}"

        self.status_label = Gtk.Label(
            label=status_text,
            halign=Gtk.Align.START,
            css_classes=["dim-label"],
            margin_top=5
        )
        self.append(self.status_label)

    def _on_open_clicked(self, button):
        """Open first matching file in internal editor."""
        if self.matches and self.open_in_editor_callback:
            file_path, _, _ = self.matches[0]
            self.open_in_editor_callback(file_path)

    def _on_copy_clicked(self, button):
        """Copy results to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return

        lines = []
        for file_path, line_num, content in self.matches:
            try:
                rel_path = os.path.relpath(file_path, self.search_path)
            except ValueError:
                rel_path = file_path
            lines.append(f"{rel_path}:{line_num}:{content.rstrip()}")

        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value("\n".join(lines)))

        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def get_matches(self) -> list[tuple[str, int, str]]:
        """Get the list of (file_path, line_number, line_content) tuples."""
        return self.matches

    def get_pattern(self) -> str:
        """Get the search pattern used."""
        return self.pattern

    def get_search_path(self) -> str:
        """Get the search path used."""
        return self.search_path
