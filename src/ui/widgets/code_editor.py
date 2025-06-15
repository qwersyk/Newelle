import gi
import os

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5') # Using GtkSourceView 5

from gi.repository import Gtk, GObject, GtkSource, Gio, GLib, Adw, Gdk

class CodeEditorWidget(Gtk.Box):
    """
    A text editor widget for code, using GtkSource.View.
    It can edit a file or a given text string.
    """

    __gsignals__ = {
        'content-saved': (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # Emits file_path
        'add-to-chat': (GObject.SignalFlags.RUN_FIRST, None, ()),  # Emits when add to chat is clicked
        'edit_state_changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,))  # Emits when the modified state changes
    }

    def __init__(self, **kwargs):
        """
        Initialize the CodeEditorWidget.
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)

        self.current_file_path = None
        self._source_buffer = None
        self._source_view = None
        self._language_manager = GtkSource.LanguageManager.get_default()
        self.is_modified = False  # Track if the file is edited
        
        # Search and replace functionality
        self._search_context = None
        self._search_settings = None
        self._search_bar = None
        self._search_entry = None
        self._replace_entry = None

        self._build_ui()
        self._setup_editor()
        self._setup_search()
        self._source_buffer.connect('changed', self._on_buffer_changed)
        self._add_keyboard_shortcuts()

    def _build_ui(self):
        """Build the user interface."""
        # Create a header bar
        header_bar = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False, show_end_title_buttons=False)
        header_bar.set_title_widget(Gtk.Label(label=os.path.basename(self.current_file_path) if self.current_file_path else ""))
        # Create save button
        save_button = Gtk.Button.new_from_icon_name('document-save-symbolic')
        save_button.connect('clicked', self._on_save_clicked)
        header_bar.pack_start(save_button)

        # Create open button
        open_button = Gtk.Button.new_from_icon_name('document-open-symbolic')
        open_button.connect('clicked', self._on_open_clicked)
        header_bar.pack_start(open_button)

        # Create search button
        search_button = Gtk.Button.new_from_icon_name('edit-find-symbolic')
        search_button.connect('clicked', self._on_search_clicked)
        header_bar.pack_start(search_button)

        # Create add to chat button
        add_to_chat_button = Gtk.Button(icon_name="attach-symbolic")
        add_to_chat_button.connect('clicked', self._on_add_to_chat_clicked)
        header_bar.pack_start(add_to_chat_button)

        # Add header bar to the top of the box
        self.append(header_bar)

        # Create search bar
        self._create_search_bar()

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        self.append(scrolled_window)

        self._source_view = GtkSource.View.new()
        self._source_view.set_monospace(True)
        self._source_view.set_show_line_numbers(True)
        self._source_view.set_highlight_current_line(True)
        self._source_view.set_auto_indent(True)
        self._source_view.set_indent_width(4)
        self._source_view.set_insert_spaces_instead_of_tabs(True)
        # Enable space drawing
        # self._source_view.set_draw_spaces(GtkSource.DrawSpacesFlags.SPACE | GtkSource.DrawSpacesFlags.TAB | GtkSource.DrawSpacesFlags.NEWLINE | GtkSource.DrawSpacesFlags.NBSP | GtkSource.DrawSpacesFlags.LEADING | GtkSource.DrawSpacesFlags.TEXT | GtkSource.DrawSpacesFlags.TRAILING)


        # Optional: Add a minimap
        # minimap = GtkSource.View.new()
        # minimap_widget = GtkSource.Map.new_for_view(self._source_view)
        # minimap_widget.set_vexpand(True)
        # self.pack_start(minimap_widget, False, False, 0) # Add minimap to the side

        scrolled_window.set_child(self._source_view)

    def _create_search_bar(self):
        """Create the search and replace bar."""
        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_key_capture_widget(self)
        self._search_bar.set_show_close_button(True)
        
        # Main container for search/replace
        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)
        
        # First row: Search entry and navigation buttons
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        # Search entry
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search...")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect('search-changed', self._on_search_changed)
        self._search_entry.connect('activate', self._on_search_next)
        search_row.append(self._search_entry)
        
        # Search navigation buttons
        prev_button = Gtk.Button.new_from_icon_name('go-up-symbolic')
        prev_button.set_tooltip_text("Previous match")
        prev_button.connect('clicked', self._on_search_previous)
        search_row.append(prev_button)
        
        next_button = Gtk.Button.new_from_icon_name('go-down-symbolic')
        next_button.set_tooltip_text("Next match")
        next_button.connect('clicked', self._on_search_next)
        search_row.append(next_button)
        
        # Case sensitive toggle
        case_button = Gtk.ToggleButton()
        case_button.set_label("Aa")
        case_button.set_tooltip_text("Match case")
        case_button.connect('toggled', self._on_case_sensitive_toggled)
        search_row.append(case_button)
        
        # Whole word toggle
        word_button = Gtk.ToggleButton()
        word_button.set_label("W")
        word_button.set_tooltip_text("Whole words only")
        word_button.connect('toggled', self._on_whole_word_toggled)
        search_row.append(word_button)
        
        # Replace toggle button
        replace_toggle = Gtk.ToggleButton()
        replace_toggle.set_label("Replace")
        replace_toggle.connect('toggled', self._on_replace_toggled)
        search_row.append(replace_toggle)
        
        search_box.append(search_row)
        
        # Second row: Replace entry and buttons (initially hidden)
        self._replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._replace_row.set_visible(False)
        
        # Replace entry
        self._replace_entry = Gtk.Entry()
        self._replace_entry.set_placeholder_text("Replace with...")
        self._replace_entry.set_hexpand(True)
        self._replace_entry.connect('activate', self._on_replace_next)
        self._replace_row.append(self._replace_entry)
        
        # Replace buttons
        replace_button = Gtk.Button.new_with_label("Replace")
        replace_button.connect('clicked', self._on_replace_next)
        self._replace_row.append(replace_button)
        
        replace_all_button = Gtk.Button.new_with_label("Replace All")
        replace_all_button.connect('clicked', self._on_replace_all)
        self._replace_row.append(replace_all_button)
        
        search_box.append(self._replace_row)
        
        self._search_bar.set_child(search_box)
        # Connect the search entry to the search bar to fix GTK warning
        self._search_bar.connect_entry(self._search_entry)
        self.append(self._search_bar)

    def _setup_editor(self):
        """Set up the GtkSource.Buffer."""
        self._source_buffer = GtkSource.Buffer.new(None) # No Gtk.TextTagTable initially
        self._source_buffer.set_highlight_syntax(True)
        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme('Adwaita-dark')
        self._source_buffer.set_style_scheme(style_scheme)
        self._source_view.set_buffer(self._source_buffer)

    def _setup_search(self):
        """Set up search functionality."""
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_case_sensitive(False)
        self._search_settings.set_at_word_boundaries(False)
        self._search_settings.set_wrap_around(True)
        
    def _update_search_context(self):
        """Update the search context when buffer changes."""
        if self._source_buffer and self._search_settings:
            self._search_context = GtkSource.SearchContext.new(self._source_buffer, self._search_settings)
            self._search_context.set_highlight(True)

    def _guess_language(self, file_path: str):
        """Guess GtkSource.Language from file_path."""
        if not file_path:
            return None
        
        # GtkSource.LanguageManager.guess_language() takes a filename and content type.
        # We can use Gio to get the content type.
        f = Gio.File.new_for_path(file_path)
        try:
            info = f.query_info('standard::content-type', Gio.FileQueryInfoFlags.NONE, None)
            content_type = info.get_content_type()
            language = self._language_manager.guess_language(os.path.basename(file_path), content_type)
            return language
        except GLib.Error: # Handle cases where content type detection might fail
            # Fallback to guessing by extension (simplified)
            # A more robust fallback would parse lang.get_metadata("globs")
            # which might return something like "*.py;*.pyw"
            # For now, we rely mostly on content_type or explicit set.
            # This is a basic fallback.
            name, ext = os.path.splitext(os.path.basename(file_path))
            if ext:
                # Try to find a language that lists this exact extension (e.g. ".py")
                # This is a very simplified approach.
                # GtkSource.LanguageManager.get_language_ids() -> list of str
                # GtkSource.LanguageManager.get_language(id) -> GtkSource.Language
                # GtkSource.Language.get_metadata("globs") -> e.g. "*.py;*.pyw"
                for lang_id in self._language_manager.get_language_ids():
                    lang = self._language_manager.get_language(lang_id)
                    if lang:
                        globs_meta = lang.get_metadata("globs")
                        if globs_meta:
                            globs = globs_meta.split(';')
                            if ext in globs or f"*{ext}" in globs: # Basic check
                                return lang
            return None


    def set_language_by_id(self, language_id: str):
        """Sets the syntax highlighting language by its ID (e.g., 'python', 'javascript')."""
        language = self._language_manager.get_language(language_id)
        if self._source_buffer:
            self._source_buffer.set_language(language)
        if language:
            print(f"Language set to: {language.get_name()}")
        else:
            print(f"Could not find language for ID: {language_id}")


    def load_from_string(self, text: str, language_id: str = None):
        """
        Loads text into the editor.

        Args:
            text (str): The text content to load.
            language_id (str, optional): The language ID for syntax highlighting (e.g., 'python').
                                         If None, no specific language is set initially.
        """
        if self._source_buffer:
            self._source_buffer.set_text(text)
            self.current_file_path = None # Reset file path when loading from string
            if language_id:
                self.set_language_by_id(language_id)
            else:
                # Clear any existing language if none is provided
                self._source_buffer.set_language(None)
            # Update search context for new buffer
            self._update_search_context()

    def load_from_file(self, file_path: str):
        """
        Loads content from a file into the editor.
        Attempts to set syntax highlighting based on the file type.

        Args:
            file_path (str): The path to the file to load.

        Returns:
            bool: True if loading was successful, False otherwise.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.current_file_path = os.path.abspath(file_path)
            if self._source_buffer:
                self._source_buffer.set_text(content)
                self._source_buffer.set_modified(False) # Mark as unmodified initially
                self.is_modified = False  # Reset modified state

                language = self._guess_language(file_path)
                if language:
                    self._source_buffer.set_language(language)
                    print(f"Guessed language: {language.get_name()} for {file_path}")
                else:
                    # Clear language if none could be guessed
                    self._source_buffer.set_language(None)
                    print(f"Could not guess language for {file_path}")
                
                # Update search context for new buffer
                self._update_search_context()
                return True
        except Exception as e:
            print(f"Error loading file '{file_path}': {e}")
            self.current_file_path = None
            # Clear content on error
            if self._source_buffer:
                self._source_buffer.set_text(f"# Error loading file: {file_path}\n# {e}")
                self._source_buffer.set_language(None) # No language for error message
                self._update_search_context()
            return False

    def get_text(self) -> str:
        """
        Returns the current text content of the editor.
        """
        if not self._source_buffer:
            return ""
        
        start_iter = self._source_buffer.get_start_iter()
        end_iter = self._source_buffer.get_end_iter()
        return self._source_buffer.get_text(start_iter, end_iter, True)

    def save_to_file(self, file_path: str = None) -> bool:
        """
        Saves the current content to a file.
        If file_path is None, it uses the current_file_path if available.

        Args:
            file_path (str, optional): The path to save the file to.

        Returns:
            bool: True if saving was successful, False otherwise.
        """
        path_to_save = file_path if file_path else self.current_file_path

        if not path_to_save:
            print("Error saving: No file path specified and no current file loaded.")
            # Optionally, you could open a save-as dialog here
            # For now, just return False
            return False

        content = self.get_text()
        try:
            with open(path_to_save, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.current_file_path = os.path.abspath(path_to_save) # Update current path
            if self._source_buffer:
                self._source_buffer.set_modified(False) # Mark as unmodified
                self.is_modified = False  # Reset modified state
            
            # Attempt to set language if it's a new file or path changed
            # and language wasn't set by file type before (e.g. new untitled file)
            if self._source_buffer and not self._source_buffer.get_language():
                guessed_lang = self._guess_language(path_to_save)
                if guessed_lang:
                    self._source_buffer.set_language(guessed_lang)

            self.is_modified = False
            self.emit('edit_state_changed', self.is_modified)
            self.emit('content-saved', self.current_file_path)
            print(f"Content saved to: {self.current_file_path}")
            return True
        except Exception as e:
            print(f"Error saving file '{path_to_save}': {e}")
            return False

    def _on_save_clicked(self, button):
        """Handle save button click."""
        self.save() 
    
    def save(self):
        if self.current_file_path:
            self.save_to_file()
        else:
            # Emit signal if no file path
            self.emit('content-saved', None)
    
    def saved(self):
        self.is_modified = False
        self.emit('edit_state_changed', self.is_modified)
    
    def _on_open_clicked(self, button):
        """Handle open button click."""
        if self.current_file_path:
            # Open the file in an external editor
            os.system(f'xdg-open {self.current_file_path}')

    def _on_add_to_chat_clicked(self, button):
        """Handle add to chat button click."""
        self.emit('add-to-chat')

    def _on_buffer_changed(self, buffer):
        """Update the modified state when the buffer changes."""
        self.is_modified = buffer.get_modified()
        self.emit('edit_state_changed', self.is_modified)

    def _add_keyboard_shortcuts(self):
        """Add keyboard shortcuts for the editor."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events."""
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_s:
                self._on_save_clicked(None)
                return Gdk.EVENT_STOP
            elif keyval == Gdk.KEY_f:
                self._on_search_clicked(None)
                return Gdk.EVENT_STOP
            elif keyval == Gdk.KEY_h:
                self._on_search_clicked(None)
                # Also show replace row
                replace_toggle = None
                search_box = self._search_bar.get_child()
                if search_box:
                    search_row = search_box.get_first_child()
                    if search_row:
                        # Find the replace toggle button
                        child = search_row.get_first_child()
                        while child:
                            if isinstance(child, Gtk.ToggleButton) and child.get_label() == "Replace":
                                replace_toggle = child
                                break
                            child = child.get_next_sibling()
                        if replace_toggle:
                            replace_toggle.set_active(True)
                return Gdk.EVENT_STOP
            elif keyval == Gdk.KEY_g:
                self._on_search_next()
                return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_F3:
            self._on_search_next()
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Escape:
            if self._search_bar.get_search_mode():
                self._search_bar.set_search_mode(False)
                return Gdk.EVENT_STOP
        
        return Gdk.EVENT_PROPAGATE

    def _on_search_clicked(self, button):
        """Handle search button click."""
        self._search_bar.set_search_mode(True)
        self._search_entry.grab_focus()

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        search_text = entry.get_text()
        if self._search_settings:
            self._search_settings.set_search_text(search_text)
        
        if search_text and self._search_context:
            # Start search from current cursor position
            cursor_iter = self._source_buffer.get_iter_at_mark(self._source_buffer.get_insert())
            found, start_iter, end_iter, wrapped = self._search_context.forward(cursor_iter)
            if found:
                self._source_buffer.select_range(start_iter, end_iter)
                self._source_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)

    def _on_search_next(self, widget=None):
        """Search for next occurrence."""
        if not self._search_context or not self._search_entry.get_text():
            return
        
        # Get current selection or cursor position
        bounds = self._source_buffer.get_selection_bounds()
        if bounds:
            start_iter = bounds[1]  # Start from end of current selection
        else:
            start_iter = self._source_buffer.get_iter_at_mark(self._source_buffer.get_insert())
        
        found, match_start, match_end, wrapped = self._search_context.forward(start_iter)
        if found:
            self._source_buffer.select_range(match_start, match_end)
            self._source_view.scroll_to_iter(match_start, 0.0, False, 0.0, 0.0)

    def _on_search_previous(self, widget):
        """Search for previous occurrence."""
        if not self._search_context or not self._search_entry.get_text():
            return
        
        # Get current selection or cursor position
        bounds = self._source_buffer.get_selection_bounds()
        if bounds:
            start_iter = bounds[0]  # Start from beginning of current selection
        else:
            start_iter = self._source_buffer.get_iter_at_mark(self._source_buffer.get_insert())
        
        found, match_start, match_end, wrapped = self._search_context.backward(start_iter)
        if found:
            self._source_buffer.select_range(match_start, match_end)
            self._source_view.scroll_to_iter(match_start, 0.0, False, 0.0, 0.0)

    def _on_case_sensitive_toggled(self, button):
        """Toggle case sensitive search."""
        if self._search_settings:
            self._search_settings.set_case_sensitive(button.get_active())

    def _on_whole_word_toggled(self, button):
        """Toggle whole word search."""
        if self._search_settings:
            self._search_settings.set_at_word_boundaries(button.get_active())

    def _on_replace_toggled(self, button):
        """Toggle replace row visibility."""
        self._replace_row.set_visible(button.get_active())
        if button.get_active():
            self._replace_entry.grab_focus()

    def _on_replace_next(self, widget):
        """Replace current match and find next."""
        if not self._search_context or not self._search_entry.get_text():
            return
        
        replace_text = self._replace_entry.get_text()
        bounds = self._source_buffer.get_selection_bounds()
        
        if bounds:
            # Check if current selection matches search
            start_iter, end_iter = bounds
            selected_text = self._source_buffer.get_text(start_iter, end_iter, False)
            search_text = self._search_entry.get_text()
            
            # Simple case-insensitive comparison if case insensitive search
            if (self._search_settings.get_case_sensitive() and selected_text == search_text) or \
               (not self._search_settings.get_case_sensitive() and selected_text.lower() == search_text.lower()):
                try:
                    self._search_context.replace(start_iter, end_iter, replace_text, len(replace_text))
                except GLib.Error as e:
                    print(f"Replace error: {e}")
        
        # Find next occurrence
        self._on_search_next()

    def _on_replace_all(self, widget):
        """Replace all occurrences."""
        if not self._search_context or not self._search_entry.get_text():
            return
        
        replace_text = self._replace_entry.get_text()
        try:
            count = self._search_context.replace_all(replace_text, len(replace_text))
            print(f"Replaced {count} occurrences")
        except GLib.Error as e:
            print(f"Replace all error: {e}")
