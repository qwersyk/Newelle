import os
import difflib
from gi.repository import Gtk, GtkSource, Gio, GLib, Gdk, GObject, Pango


class FileEditWidget(Gtk.Box):
    """
    A widget for displaying file edits with a diff view.
    Shows the changes made to a file with additions highlighted in green and deletions in red.

    Signals:
        open-externally-clicked: Emitted when open externally button is clicked
    """

    __gsignals__ = {
        'open-externally-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, file_path: str, old_content: str, new_content: str, edit_type: str = "edit",
                 color_scheme: str = "Adwaita-dark", open_in_editor_callback=None):
        """
        Initialize the file edit widget.

        Args:
            file_path: Absolute path to the file
            old_content: The original file content (empty for new files)
            new_content: The new file content after edit
            edit_type: Type of edit - "write" (new file), "edit" (modification), or "create"
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

        self.file_path = file_path
        self.old_content = old_content
        self.new_content = new_content
        self.edit_type = edit_type
        self.color_scheme = color_scheme
        self.is_expanded = False

        # Calculate stats
        self.filename = os.path.basename(file_path)
        self.open_in_editor_callback = open_in_editor_callback

        # Generate diff and track line numbers
        self.diff_content, self.line_numbers = self._generate_diff_with_line_numbers()

        # Build the UI
        self._build_header()
        self._build_diff_view()
        self._build_status()

    def _generate_diff_with_line_numbers(self) -> tuple[str, list[tuple[str, str]]]:
        """
        Generate unified diff and track line numbers for each line.

        Returns:
            Tuple of (diff_content, line_numbers) where line_numbers is a list of
            tuples (old_line_num, new_line_num) for each diff line.
            Line numbers are formatted as strings ("" for N/A, formatted for display).
        """
        line_numbers = []

        if self.edit_type == "write" or not self.old_content:
            # For new files, show all content as additions
            lines = self.new_content.split('\n')
            diff_lines = []
            for i, line in enumerate(lines, 1):
                diff_lines.append(f"+{line}")
                line_numbers.append(("", str(i)))
            return '\n'.join(diff_lines), line_numbers

        # Generate unified diff
        old_lines = self.old_content.split('\n')
        new_lines = self.new_content.split('\n')

        diff_generator = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{self.filename}",
            tofile=f"b/{self.filename}",
            lineterm=''
        )

        diff_lines = []
        old_line = 0
        new_line = 0
        old_count = 0
        new_count = 0

        for line in diff_generator:
            # Parse hunk header: @@ -start,count +start,count @@
            if line.startswith('@@'):
                diff_lines.append(line)
                line_numbers.append(("", ""))
                # Parse the line numbers from the hunk header
                # Format: @@ -old_start,old_count +new_start,new_count @@
                parts = line.split(' ')
                # parts[1] = "-old_start,old_count", parts[2] = "+new_start,new_count"
                old_part = parts[1][1:]  # Remove leading "-"
                new_part = parts[2][1:]   # Remove leading "+"

                old_start = int(old_part.split(',')[0]) if ',' in old_part else int(old_part)
                new_start = int(new_part.split(',')[0]) if ',' in new_part else int(new_part)

                old_line = old_start
                new_line = new_start
                continue

            if line.startswith('---') or line.startswith('+++'):
                diff_lines.append(line)
                line_numbers.append(("", ""))
                continue

            if line.startswith('-'):
                diff_lines.append(line)
                line_numbers.append((str(old_line), ""))
                old_line += 1
            elif line.startswith('+'):
                diff_lines.append(line)
                line_numbers.append(("", str(new_line)))
                new_line += 1
            elif line.startswith(' ') or line:
                # Context line (unchanged) or empty line
                diff_lines.append(line)
                line_numbers.append((str(old_line), str(new_line)))
                old_line += 1
                new_line += 1

        return '\n'.join(diff_lines), line_numbers

    def _apply_diff_line_highlighting(self):
        """Apply red background for deletions and green background for additions."""
        # Colors that work on both light and dark themes
        # Additions: green background
        add_tag = self.buffer.create_tag(
            "diff-add",
            background_rgba=Gdk.RGBA(red=0.11, green=0.37, blue=0.13, alpha=0.5)
        )
        # Deletions: red background
        del_tag = self.buffer.create_tag(
            "diff-del",
            background_rgba=Gdk.RGBA(red=0.36, green=0.11, blue=0.11, alpha=0.5)
        )

        lines = self.diff_content.split('\n')
        offset = 0
        for i, line in enumerate(lines):
            is_last = (i == len(lines) - 1)
            line_len = len(line) + (0 if is_last else 1)  # +1 for newline (except last line)
            if line.startswith('+') and not line.startswith('+++'):
                start = self.buffer.get_iter_at_offset(offset)
                end = self.buffer.get_iter_at_offset(offset + line_len)
                self.buffer.apply_tag(add_tag, start, end)
            elif line.startswith('-') and not line.startswith('---'):
                start = self.buffer.get_iter_at_offset(offset)
                end = self.buffer.get_iter_at_offset(offset + line_len)
                self.buffer.apply_tag(del_tag, start, end)
            offset += line_len

    def _build_header(self):
        """Build the header row with icon, title, and action buttons."""
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        # File icon based on edit type
        icon_name = "document-edit-symbolic" if self.edit_type == "edit" else "document-new-symbolic"
        file_icon = Gtk.Image.new_from_icon_name(icon_name)
        file_icon.set_pixel_size(16)
        file_icon.add_css_class("dim-label")
        header_box.append(file_icon)

        # Title (filename with edit indicator)
        title_text = self.filename
        if self.edit_type == "write":
            title_text += " (new file)"
        elif self.edit_type == "edit":
            title_text += " (modified)"

        title_label = Gtk.Label(
            label=title_text,
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
            ellipsize=Pango.EllipsizeMode.MIDDLE
        )
        header_box.append(title_label)

        # Expand/Collapse button
        self.expand_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.expand_button.set_icon_name("window-maximize-symbolic")
        self.expand_button.set_tooltip_text("Expand view")
        self.expand_button.connect("clicked", self._on_expand_clicked)
        header_box.append(self.expand_button)

        # Copy button
        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy diff to clipboard")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        header_box.append(self.copy_button)

        # Open in internal editor button
        if self.open_in_editor_callback is not None:
            edit_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
            edit_button.set_icon_name("document-edit-symbolic")
            edit_button.set_tooltip_text("Open in internal editor")
            edit_button.connect("clicked", self._on_open_in_editor_clicked)
            header_box.append(edit_button)

        # Open externally button
        open_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open in external editor")
        open_button.connect("clicked", self._on_open_externally_clicked)
        header_box.append(open_button)

        self.append(header_box)

    def _build_diff_view(self):
        """Build the diff view with syntax highlighting, line background colors, and line numbers."""
        # Create a horizontal box to hold line numbers and the source view
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content_box.set_hexpand(True)

        # Build custom line number gutter
        line_numbers_box = self._build_line_numbers_widget()
        content_box.append(line_numbers_box)

        # Create source buffer and view
        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(self.diff_content)

        # Apply red/green background highlighting for diff lines
        self._apply_diff_line_highlighting()

        # Setup syntax highlighting for diff
        manager = GtkSource.LanguageManager.get_default()
        diff_language = manager.get_language("diff")
        if diff_language:
            self.buffer.set_language(diff_language)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        if style_scheme:
            self.buffer.set_style_scheme(style_scheme)

        self.buffer.set_highlight_syntax(True)

        # Create source view
        self.sourceview = GtkSource.View(monospace=True)
        self.sourceview.set_hexpand(True)
        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_editable(False)
        self.sourceview.set_cursor_visible(False)
        self.sourceview.set_show_line_numbers(False)  # We use custom line numbers
        self.sourceview.set_wrap_mode(Gtk.WrapMode.NONE)  # Better for diff alignment
        self.sourceview.set_top_margin(6)
        self.sourceview.set_bottom_margin(6)
        self.sourceview.set_left_margin(6)
        self.sourceview.set_right_margin(6)

        content_box.append(self.sourceview)

        # Scrolled window
        self.scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            propagate_natural_height=True,
            max_content_height=300,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True
        )
        self.scroll.set_child(content_box)
        self.append(self.scroll)

    def _build_line_numbers_widget(self) -> Gtk.Box:
        """Build a widget showing old and new file line numbers."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("dim-label")

        # Old file line numbers (right-aligned, for deletions)
        old_lines_label = Gtk.Label(
            label=self._format_line_numbers_column(self.line_numbers, "old"),
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            css_classes=["monospace"],
            margin_start=6,
            margin_end=3,
            margin_top=6
        )
        old_lines_label.add_css_class("dim-label")
        self.old_lines_label = old_lines_label
        box.append(old_lines_label)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_margin_top(6)
        separator.set_margin_bottom(6)
        box.append(separator)

        # New file line numbers (right-aligned, for additions)
        new_lines_label = Gtk.Label(
            label=self._format_line_numbers_column(self.line_numbers, "new"),
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            css_classes=["monospace"],
            margin_start=3,
            margin_end=6,
            margin_top=6
        )
        new_lines_label.add_css_class("dim-label")
        self.new_lines_label = new_lines_label
        box.append(new_lines_label)

        return box

    def _format_line_numbers_column(self, line_numbers: list[tuple[str, str]], column: str) -> str:
        """Format line numbers for display in a column."""
        idx = 0 if column == "old" else 1
        lines = []
        for nums in line_numbers:
            num = nums[idx]
            if num:
                lines.append(num)
            else:
                lines.append("")  # Empty for lines not in this version
        return '\n'.join(lines)

    def _build_status(self):
        """Build status label showing change statistics."""
        self.status_label = Gtk.Label(
            halign=Gtk.Align.START,
            visible=True,
            css_classes=["dim-label"]
        )

        # Calculate change statistics
        if self.edit_type == "write":
            new_lines = len(self.new_content.split('\n')) if self.new_content else 0
            status_text = f"New file: {new_lines} lines"
        else:
            old_lines = self.old_content.split('\n') if self.old_content else []
            new_lines = self.new_content.split('\n') if self.new_content else []

            # Count additions and deletions from diff
            additions = 0
            deletions = 0
            for line in self.diff_content.split('\n'):
                if line.startswith('+') and not line.startswith('+++'):
                    additions += 1
                elif line.startswith('-') and not line.startswith('---'):
                    deletions += 1

            status_text = f"Changes: +{additions} / -{deletions} lines"

        self.status_label.set_label(status_text)
        self.append(self.status_label)

    def _on_expand_clicked(self, button):
        """Toggle expanded/collapsed view."""
        self.is_expanded = not self.is_expanded

        if self.is_expanded:
            # Expand: remove max_content_height limit and set size_request
            # Calculate height based on content line count
            # Each line is roughly 20px tall (font height + spacing)
            line_count = self.diff_content.count('\n') + 1
            content_height = min(max(line_count * 20, 300), 800)  # Between 300 and 800
            self.scroll.set_max_content_height(-1)
            self.scroll.set_size_request(-1, content_height)
            button.set_icon_name("window-restore-symbolic")
            button.set_tooltip_text("Collapse view")
        else:
            # Collapse: restore max_content_height and clear size_request
            self.scroll.set_max_content_height(300)
            self.scroll.set_size_request(-1, -1)
            button.set_icon_name("window-maximize-symbolic")
            button.set_tooltip_text("Expand view")

    def _on_copy_clicked(self, button):
        """Copy diff content to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return

        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.diff_content))

        # Visual feedback
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def _on_open_in_editor_clicked(self, button):
        """Open file in internal editor."""
        if self.open_in_editor_callback is not None:
            self.open_in_editor_callback()

    def _on_open_externally_clicked(self, button):
        """Open file in external editor."""
        try:
            Gio.AppInfo.launch_default_for_uri(f"file://{self.file_path}", None)
        except GLib.Error as e:
            print(f"Error opening file: {e}")

        self.emit('open-externally-clicked')

    def get_diff_content(self) -> str:
        """Get the diff content."""
        return self.diff_content

    def get_new_content(self) -> str:
        """Get the new file content."""
        return self.new_content
