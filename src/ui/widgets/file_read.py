import os
from gi.repository import Gtk, GtkSource, Gio, GLib, Gdk, GObject, Pango


class ReadFileWidget(Gtk.Box):
    """
    A widget for displaying file contents with syntax highlighting.
    Design matches CopyBox execution request mode.
    
    Signals:
        open-externally-clicked: Emitted when open externally button is clicked
    """
    
    __gsignals__ = {
        'open-externally-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    
    def __init__(self, file_path: str, content: str, offset: int = 0, max_content_lines: int = 1000, color_scheme: str = "Adwaita-dark", open_in_editor_callback=None):
        """
        Initialize the file read widget.
        
        Args:
            file_path: Absolute path to the file
            content: The file content to display
            offset: Line offset (0-based) for paginated reading
            max_content_lines: Maximum number of lines to display
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
        self.full_content = content
        self.offset = offset
        self.max_content_lines = max_content_lines
        self.color_scheme = color_scheme
        
        # Calculate file stats
        self.file_size = len(content.encode('utf-8'))
        self.total_lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        self.display_lines = min(self.total_lines - offset, max_content_lines) if self.total_lines > offset else 0
        
        # Get filename
        self.filename = os.path.basename(file_path)
        self.open_in_editor_callback = open_in_editor_callback
        
        # Build the UI
        self._build_header()
        self._build_content_view()
        self._build_status()
    
    def _build_header(self):
        """Build the header row with icon, title, and action buttons (like CopyBox execution request)."""
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        
        # File icon
        file_icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        file_icon.set_pixel_size(16)
        file_icon.add_css_class("dim-label")
        header_box.append(file_icon)
        
        # Title (filename)
        title_label = Gtk.Label(
            label=self.filename,
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
            ellipsize=Pango.EllipsizeMode.MIDDLE
        )
        header_box.append(title_label)
        
        # Copy button
        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy to clipboard")
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
    
    def _build_content_view(self):
        """Build the content view with syntax highlighting (like CopyBox)."""
        display_content = self._get_display_content()
        
        # Create source buffer and view
        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(display_content)
        
        # Setup syntax highlighting
        language = self._guess_language(self.file_path, display_content)
        if language:
            self.buffer.set_language(language)
        
        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        if style_scheme:
            self.buffer.set_style_scheme(style_scheme)
        
        self.buffer.set_highlight_syntax(True)
        
        # Create source view (matches CopyBox execution request)
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
        
        # Scrolled window (matches CopyBox: max_content_height=150)
        scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            propagate_natural_height=True,
            max_content_height=150,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True
        )
        scroll.set_child(self.sourceview)
        self.append(scroll)
    
    def _build_status(self):
        """Build status label (optional, like CopyBox status_label)."""
        self.status_label = Gtk.Label(
            halign=Gtk.Align.START,
            visible=False,
            css_classes=["dim-label"]
        )
        self.append(self.status_label)
        
        # Show file info if truncated
        if len(self.full_content) > len(self._get_display_content()):
            size_str = self._format_file_size(self.file_size)
            lines_str = f"Lines {self.offset + 1}-{min(self.offset + self.display_lines, self.total_lines)} of {self.total_lines}"
            self.status_label.set_label(f"{size_str} • {lines_str} • Content truncated")
            self.status_label.set_visible(True)
    
    def _get_display_content(self) -> str:
        """Get the content to display based on offset and limit."""
        lines = self.full_content.split('\n')
        
        # Apply offset
        if self.offset > 0:
            lines = lines[self.offset:]
        
        # Apply limit
        if len(lines) > self.max_content_lines:
            lines = lines[:self.max_content_lines]
        
        return '\n'.join(lines)
    
    def _guess_language(self, file_path: str, content: str = None):
        """Guess the programming language from file path."""
        manager = GtkSource.LanguageManager.get_default()
        
        # Try by content type first
        try:
            f = Gio.File.new_for_path(file_path)
            info = f.query_info('standard::content-type', Gio.FileQueryInfoFlags.NONE, None)
            content_type = info.get_content_type()
            language = manager.guess_language(os.path.basename(file_path), content_type)
            if language:
                return language
        except GLib.Error:
            pass
        
        # Fallback to extension matching
        ext = os.path.splitext(file_path)[1].lower()
        if ext:
            for lang_id in manager.get_language_ids():
                lang = manager.get_language(lang_id)
                if lang:
                    globs_meta = lang.get_metadata("globs")
                    if globs_meta:
                        globs = globs_meta.split(';')
                        for glob in globs:
                            if glob.endswith(ext) or glob == f"*{ext}":
                                return lang
        
        # Try by shebang if content provided
        if content:
            first_line = content.split('\n')[0] if content else ""
            if first_line.startswith('#!'):
                # Extract interpreter from shebang
                interpreter = first_line[2:].strip().split('/')[-1].split()[0]
                # Map common interpreters
                interpreter_map = {
                    'python': 'python3',
                    'python3': 'python3',
                    'bash': 'sh',
                    'sh': 'sh',
                    'zsh': 'sh',
                    'ruby': 'ruby',
                    'node': 'js',
                    'nodejs': 'js',
                    'perl': 'perl',
                    'php': 'php',
                }
                if interpreter in interpreter_map:
                    return manager.get_language(interpreter_map[interpreter])
        
        return None
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    
    def _on_copy_clicked(self, button):
        """Copy file content to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.full_content))
        
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
    
    def get_content(self) -> str:
        """Get the full file content."""
        return self.full_content
    
    def get_display_content(self) -> str:
        """Get the displayed (potentially truncated) content."""
        return self._get_display_content()
