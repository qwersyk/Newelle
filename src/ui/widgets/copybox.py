import threading
import subprocess
import os
import tempfile
import socket
from gi.repository import GLib, Gtk, GtkSource, Gio, Pango, Gdk, GObject


class CopyBox(Gtk.Box):
    """
    A widget for displaying code with syntax highlighting and optional execution capabilities.
    
    Can be used standalone without a parent window by using signals and callbacks.
    
    Signals:
        run-clicked: Emitted when the run button is clicked. Returns (command: str, language: str)
        skip-clicked: Emitted when the skip button is clicked (execution_request mode only)
        command-complete: Emitted when command execution completes. Returns (output: str)
        edit-clicked: Emitted when the edit button is clicked
    
    Args:
        txt: The code text to display
        lang: The language for syntax highlighting
        parent: Optional parent window (for legacy compatibility)
        id_message: Optional message ID for chat integration
        id_codeblock: Optional codeblock ID
        allow_edit: Whether to show an edit button
        color_scheme: GtkSourceView color scheme name
        executable_languages: List of languages that should show run button, or None for defaults
        execution_request: If True, behaves like a command confirmation dialog with Run/Skip buttons
        run_callback: Optional callback for command execution (signature: callback(command) -> output)
    """
    
    __gsignals__ = {
        'run-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'skip-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'command-complete': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'edit-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }
    
    # Default runnable languages
    DEFAULT_RUNNABLE_LANGUAGES = ["python", "python3", "html", "css", "js", "javascript"]
    DEFAULT_CONSOLE_LANGUAGES = ["console", "sh", "bash", "shell"]
    
    def __init__(
        self,
        txt: str,
        lang: str,
        parent=None,
        id_message: int = -1,
        id_codeblock: int = -1,
        allow_edit: bool = False,
        color_scheme: str = None,
        executable_languages: list = None,
        execution_request: bool = False,
        run_callback=None
    ):
        Gtk.Box.__init__(
            self,
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            css_classes=["osd", "toolbar", "code"]
        )
        
        # Store parameters
        self.txt = txt
        self.parent = parent
        self.id_message = id_message
        self.id_codeblock = id_codeblock
        self.execution_request = execution_request
        self.run_callback = run_callback
        self.has_responded = False
        
        # Set color scheme
        if color_scheme is None and parent is not None and hasattr(parent, "controller"):
            self.color_scheme = parent.controller.newelle_settings.editor_color_scheme
        else:
            self.color_scheme = color_scheme if color_scheme is not None else "Adwaita-dark"
        
        # Set executable languages
        if executable_languages is not None:
            self.runnable_languages = executable_languages
        else:
            self.runnable_languages = self.DEFAULT_RUNNABLE_LANGUAGES.copy()
        
        # Normalize language
        lang = lang.replace(" ", "")
        display_lang = lang
        replace_lang = [
            (["py", "py3"], "python"),
            (["bash", "shell", "console"], "sh"),
            (["javascript"], "js")
        ]
        for rep in replace_lang:
            if display_lang in rep[0]:
                display_lang = rep[1]
        self.lang = display_lang
        self.original_lang = lang
        
        # Calculate width based on longest line
        longest_line = max(txt.splitlines(), key=len) if txt else ""
        
        # Build UI based on mode
        if execution_request:
            self._build_execution_request_ui(txt, longest_line)
        else:
            self._build_standard_ui(txt, lang, display_lang, longest_line, allow_edit)
    
    def _build_execution_request_ui(self, txt: str, longest_line: str):
        """Build UI for execution request mode (like CommandConfirmBox)."""
        # Header row with title and buttons
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        
        terminal_icon = Gtk.Image.new_from_icon_name("gnome-terminal-symbolic")
        terminal_icon.set_pixel_size(16)
        terminal_icon.add_css_class("dim-label")
        header_box.append(terminal_icon)
        
        title_label = Gtk.Label(
            label="Terminal Command",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"]
        )
        header_box.append(title_label)
        
        # Terminal button
        self.terminal_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.terminal_button.set_icon_name("gnome-terminal-symbolic")
        self.terminal_button.set_tooltip_text("Open in Terminal")
        self.terminal_button.connect("clicked", self._on_execution_terminal_clicked)
        header_box.append(self.terminal_button)
        
        # Skip button
        self.skip_button = Gtk.Button(label="Skip", css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.skip_button.connect("clicked", self._on_skip_clicked)
        header_box.append(self.skip_button)
        
        # Run button (primary action)
        self.run_button = Gtk.Button(css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        run_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        run_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        run_content.append(run_icon)
        run_content.append(Gtk.Label(label="Run"))
        self.run_button.set_child(run_content)
        self.run_button.connect("clicked", self._on_execution_run_clicked)
        header_box.append(self.run_button)
        
        self.append(header_box)
        
        # Code display with syntax highlighting
        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(txt, -1)
        
        manager = GtkSource.LanguageManager.new()
        language = manager.get_language("sh")
        self.buffer.set_language(language)
        
        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        self.buffer.set_style_scheme(style_scheme)
        
        self.sourceview = GtkSource.View(
            monospace=True,
            width_request=min(12 * len(longest_line), 600)
        )
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
            max_content_height=150,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True
        )
        scroll.set_child(self.sourceview)
        self.append(scroll)
        
        # Output expander (hidden initially)
        self.output_expander = Gtk.Expander(
            label="Output",
            css_classes=["toolbar", "osd"]
        )
        self.output_expander.set_expanded(False)
        self.output_expander.set_visible(False)
        self.output_label = Gtk.Label(
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            halign=Gtk.Align.START,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6
        )
        self.output_expander.set_child(self.output_label)
        self.append(self.output_expander)
        
        # Status label (hidden initially)
        self.status_label = Gtk.Label(
            halign=Gtk.Align.START,
            visible=False,
            css_classes=["dim-label"]
        )
        self.append(self.status_label)
    
    def _build_standard_ui(self, txt: str, lang: str, display_lang: str, longest_line: str, allow_edit: bool):
        """Build standard code display UI."""
        box = Gtk.Box(halign=Gtk.Align.END)
        
        # Copy button
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.copy_button.set_child(icon)
        self.copy_button.connect("clicked", self.copy_button_clicked)
        
        # Source view setup
        self.sourceview = GtkSource.View(width_request=12 * len(longest_line), monospace=True)
        self.scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hexpand=True
        )
        
        # Edit button
        if allow_edit:
            self.edit_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="document-edit-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.edit_button.set_child(icon)
            self.edit_button.connect("clicked", self.edit_button_clicked)
            box.append(self.edit_button)
        
        # Buffer setup
        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(txt, -1)
        
        manager = GtkSource.LanguageManager.new()
        language = manager.get_language(display_lang)
        self.buffer.set_language(language)
        
        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        self.buffer.set_style_scheme(style_scheme)
        
        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_vexpand(True)
        self.sourceview.set_show_line_numbers(True)
        self.sourceview.set_editable(False)
        
        # Determine style based on language
        style = "success"
        if lang in ["python", "python3", "cpp", "php", "objc", "go", "typescript", "lua", "perl", "r", "dart", "sql", "latex"]:
            style = "accent"
        if lang in ["java", "javascript", "kotlin", "rust", "json"]:
            style = "warning"
        if lang in ["ruby", "swift", "scala"]:
            style = "error"
        if lang in ["console"]:
            style = ""
        
        # Header with language label
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        main.set_homogeneous(True)
        label = Gtk.Label(
            label=lang,
            halign=Gtk.Align.START,
            margin_start=10,
            css_classes=[style, "heading"],
            wrap=False,
            ellipsize=Pango.EllipsizeMode.END
        )
        main.append(label)
        self.append(main)
        self.scroll.set_child(self.sourceview)
        self.append(self.scroll)
        main.append(box)
        
        # Check if language is runnable
        is_runnable = lang in self.runnable_languages or display_lang in self.runnable_languages
        is_console = lang in self.DEFAULT_CONSOLE_LANGUAGES or display_lang in self.DEFAULT_CONSOLE_LANGUAGES
        
        if is_runnable:
            self._add_run_button(box, lang)
        elif is_console:
            self._add_console_buttons(box)
        
        box.append(self.copy_button)
    
    def _add_run_button(self, box: Gtk.Box, lang: str):
        """Add run button for runnable languages."""
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.run_button.set_child(icon)
        self.run_button.connect("clicked", self._on_run_button_clicked, lang)
        
        self.text_expander = Gtk.Expander(
            label="Console",
            css_classes=["toolbar", "osd"],
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10
        )
        self.text_expander.set_expanded(False)
        self.text_expander.set_visible(False)
        box.append(self.run_button)
        self.append(self.text_expander)
    
    def _add_console_buttons(self, box: Gtk.Box):
        """Add run and terminal buttons for console commands."""
        # Import here to avoid circular imports when used standalone
        try:
            from .terminal_dialog import TerminalDialog
            self._terminal_dialog_available = True
        except ImportError:
            self._terminal_dialog_available = False
        
        # Run button
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.run_button.set_child(icon)
        self.run_button.connect("clicked", self._on_console_run_clicked)
        
        # Run in external terminal button
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="gnome-terminal-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.terminal_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.terminal_button.set_child(icon)
        self.terminal_button.connect("clicked", self._on_terminal_button_clicked)
        
        self.text_expander = Gtk.Expander(
            label="Console",
            css_classes=["toolbar", "osd"],
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10
        )
        
        # Get console output from parent if available
        console = "None"
        if (self.parent is not None and 
            hasattr(self.parent, 'chat') and 
            self.id_message < len(self.parent.chat) and 
            self.parent.chat[self.id_message]["User"] == "Console"):
            console = self.parent.chat[self.id_message]["Message"]
        
        self.text_expander.set_child(
            Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=console, selectable=True)
        )
        self.text_expander.set_expanded(False)
        box.append(self.run_button)
        box.append(self.terminal_button)
        self.append(self.text_expander)
    
    # === Signal handlers ===
    
    def _on_run_button_clicked(self, widget, language):
        """Handle run button click - emit signal and optionally execute."""
        self.emit('run-clicked', self.txt, language)
        
        # If we have a parent with execute capability, use it (legacy mode)
        if self.parent is not None and hasattr(self.parent, 'execute_terminal_command'):
            self.run_code(widget, language)
        elif self.run_callback is not None:
            self._execute_with_callback(widget, language)
    
    def _on_console_run_clicked(self, widget):
        """Handle console run button click."""
        self.emit('run-clicked', self.txt, 'console')
        
        if self.parent is not None and hasattr(self.parent, 'execute_terminal_command'):
            self.run_console(widget)
        elif self.run_callback is not None:
            self._execute_console_with_callback(widget)
    
    def _on_terminal_button_clicked(self, widget):
        """Handle terminal button click."""
        self.emit('run-clicked', self.txt, 'terminal')
        
        if self.parent is not None:
            self.run_console_terminal(widget)

    def _on_execution_terminal_clicked(self, widget):
        """Handle terminal button click in execution_request mode."""
        from ...utility.strings import quote_string, add_S_to_sudo
        from ...utility.system import get_spawn_command
        from .terminal_dialog import TerminalDialog
        
        if self.has_responded:
            return
            
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        
        command = "cd " + quote_string(os.getcwd()) + "; " + self.txt + "; exec bash"
        
        terminal = TerminalDialog()
        
        def save_output(save):
            widget.set_sensitive(True)
            widget.set_icon_name("gnome-terminal-symbolic")
            if save is not None:
                # Mark as responded
                self.has_responded = True
                self.run_button.set_sensitive(False)
                self.skip_button.set_sensitive(False)
                
                # Show output
                self.output_label.set_text(save)
                self.output_expander.set_visible(True)
                self.output_expander.set_expanded(True)
                
                self.status_label.set_visible(False)
                
                # Emit signal
                self.emit('command-complete', save)
            else:
                return
        
        if self.parent is not None and hasattr(self.parent, 'virtualization') and not self.parent.virtualization:
            command = add_S_to_sudo(command)
            command = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;alias sudo=\"sudo -S\";" + command]
        else:
            command = ["bash", "-c", "export TERM=xterm-256color;" + command]
            
        terminal.load_terminal(command)
        terminal.save_output_func(save_output)
        terminal.present()
    
    def _on_execution_run_clicked(self, button):
        """Handle run click in execution_request mode."""
        if self.has_responded:
            return
        self.has_responded = True
        
        # Update UI to show running state
        self.run_button.set_sensitive(False)
        self.skip_button.set_sensitive(False)
        
        # Show spinner
        spinner = Gtk.Spinner(spinning=True)
        running_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        running_box.append(spinner)
        running_box.append(Gtk.Label(label="Running..."))
        self.run_button.set_child(running_box)
        
        # Emit signal
        self.emit('run-clicked', self.txt, self.lang)
        
        # Execute command if callback provided
        if self.run_callback is not None:
            def execute_command():
                output = self.run_callback(self.txt)
                GLib.idle_add(self._on_execution_complete, output)
            
            threading.Thread(target=execute_command, daemon=True).start()
    
    def _on_execution_complete(self, output):
        """Handle command completion in execution_request mode."""
        # Update button to show completed
        check_icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic")
        complete_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        complete_box.append(check_icon)
        complete_box.append(Gtk.Label(label="Completed"))
        self.run_button.set_child(complete_box)
        self.run_button.remove_css_class("suggested-action")
        self.run_button.add_css_class("success")
        
        # Show output
        self.output_label.set_text(output if output else "No output")
        self.output_expander.set_visible(True)
        self.output_expander.set_expanded(True)
        
        self.status_label.set_visible(False)
        
        # Emit completion signal
        self.emit('command-complete', output if output else "")
    
    def _on_skip_clicked(self, button):
        """Handle skip click in execution_request mode."""
        if self.has_responded:
            return
        self.has_responded = True
        
        # Update UI to show skipped state
        self.run_button.set_sensitive(False)
        self.skip_button.set_sensitive(False)
        
        skip_icon = Gtk.Image.new_from_icon_name("action-unavailable-symbolic")
        skip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        skip_box.append(skip_icon)
        skip_box.append(Gtk.Label(label="Skipped"))
        self.skip_button.set_child(skip_box)
        self.skip_button.add_css_class("dim-label")
        
        self.status_label.set_text("Command was skipped by user")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_visible(True)
        
        # Emit skip signal
        self.emit('skip-clicked')
        
        # Notify callback that command was skipped
        if self.run_callback is not None:
            self.run_callback(None)
    
    # === Callback-based execution (standalone mode) ===
    
    def _execute_with_callback(self, widget, language):
        """Execute code using the provided callback."""
        def execute():
            if language.lower() in ["python", "python3", "py"]:
                from ...utility.strings import quote_string
                command = "python3 -c {}".format(quote_string(self.txt))
                output = self.run_callback(command)
            else:
                output = self.run_callback(self.txt)
            
            GLib.idle_add(self._update_run_complete, widget, output)
        
        self.text_expander.set_visible(True)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        
        threading.Thread(target=execute, daemon=True).start()
    
    def _execute_console_with_callback(self, widget):
        """Execute console command using the provided callback."""
        def execute():
            output = self.run_callback(self.txt)
            GLib.idle_add(self._update_run_complete, widget, output)
        
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        
        threading.Thread(target=execute, daemon=True).start()
    
    def _update_run_complete(self, widget, output):
        """Update UI after execution completes."""
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(True)
        
        if hasattr(self, 'text_expander'):
            self.text_expander.set_child(
                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=output or "No output", selectable=True)
            )
            self.text_expander.set_visible(True)
        
        self.emit('command-complete', output if output else "")

    # === Legacy methods (for parent window compatibility) ===
    
    def copy_button_clicked(self, widget):
        """Copy code to clipboard."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.txt))
        self.copy_button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: self.copy_button.set_icon_name("edit-copy-symbolic"))
    
    def run_console(self, widget, multithreading=False):
        """Run console command (legacy mode with parent)."""
        if multithreading:
            code = self.parent.execute_terminal_command(self.txt)
            self.set_output(code[1])
            
            def update_ui():
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
                icon.set_icon_size(Gtk.IconSize.INHERIT)
                widget.set_child(icon)
                widget.set_sensitive(True)
            GLib.idle_add(update_ui)
        else:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(False)
            threading.Thread(target=self.run_console, args=[widget, True]).start()
    
    def set_output(self, output):
        """Set output in the expander and optionally update parent chat."""
        if self.parent is not None and hasattr(self.parent, 'chat'):
            if self.id_message < len(self.parent.chat) and self.parent.chat[self.id_message]["User"] == "Console":
                self.parent.chat[self.id_message]["Message"] = output
            else:
                self.parent.chat.append({"User": "Console", "Message": " " + output})
        
        self.text_expander.set_child(
            Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=output, selectable=True)
        )
        
        if (self.parent is not None and 
            hasattr(self.parent, 'status') and 
            self.parent.status and 
            len(self.parent.chat) - 1 == self.id_message and 
            self.id_message < len(self.parent.chat) and 
            self.parent.chat[self.id_message]["User"] == "Console"):
            self.parent.status = False
            self.parent.update_button_text()
            self.parent.scrolled_chat()
            threading.Thread(target=self.parent.send_message).start()
    
    def run_console_terminal(self, widget, multithreading=False):
        """Run command in external terminal (legacy mode with parent)."""
        from ...utility.strings import quote_string, add_S_to_sudo
        from ...utility.system import get_spawn_command
        from .terminal_dialog import TerminalDialog
        
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        command = "cd " + quote_string(os.getcwd()) + "; " + self.txt + "; exec bash"
        external_terminal = False
        
        if external_terminal:
            cmd = self.parent.external_terminal.split()
            arguments = [s.replace("{0}", command) for s in cmd]
            subprocess.Popen(get_spawn_command() + arguments)
        else:
            terminal = TerminalDialog()
            
            def save_output(save):
                widget.set_sensitive(True)
                widget.set_icon_name("gnome-terminal-symbolic")
                if save is not None:
                    self.set_output(save)
                else:
                    return
            
            if self.parent is not None and hasattr(self.parent, 'virtualization') and not self.parent.virtualization:
                command = add_S_to_sudo(command)
                command = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;alias sudo=\"sudo -S\";" + command]
            else:
                command = ["bash", "-c", "export TERM=xterm-256color;" + command]
            terminal.load_terminal(command)
            terminal.save_output_func(save_output)
            terminal.present()
    
    def run_code(self, widget, language, multithreading=False):
        """Run code (legacy mode with parent)."""
        from ...utility.strings import quote_string
        
        if multithreading:
            if language.lower() in ["python", "python3", "py"]:
                code = self.parent.execute_terminal_command("python3 -c {}".format(quote_string(self.txt)))
            elif language.lower() in ["html", "css", "js", "javascript"]:
                codeblocks = self.get_codeblocks()
                files = {
                    "html": None,
                    "css": None,
                    "js": None
                }
                for codeblock in codeblocks:
                    if codeblock.lang.lower() == "html":
                        files["html"] = codeblock.text
                    elif codeblock.lang.lower() == "css":
                        files["css"] = codeblock.text
                    elif codeblock.lang.lower() in ["js", "javascript"]:
                        files["js"] = codeblock.text
                
                # Create a random directory in the cache directory
                cache_dir = self.parent.controller.cache_dir
                temp_dir = tempfile.mkdtemp(dir=cache_dir)
                
                # Write the code to temporary files
                with open(os.path.join(temp_dir, "index.html"), "w") as f:
                    f.write(files["html"] or "")
                with open(os.path.join(temp_dir, "style.css"), "w") as f:
                    f.write(files["css"] or "")
                with open(os.path.join(temp_dir, "script.js"), "w") as f:
                    f.write(files["js"] or "")
                
                # Start HTTP server on random port
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", 0))
                    _, port = s.getsockname()
                
                command = "cd {} && python3 -m http.server {}".format(quote_string(temp_dir), port)
                
                def open_browser_later():
                    if self.parent is not None:
                        self.parent.ui_controller.new_browser_tab("http://localhost:{}".format(port), new=False)
                        return GLib.SOURCE_REMOVE
                GLib.timeout_add(100, open_browser_later)
                code = self.parent.execute_terminal_command(command)
            else:
                code = "ae"
            self.set_output(code[1])
            
            def update_ui():
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
                icon.set_icon_size(Gtk.IconSize.INHERIT)
                widget.set_child(icon)
                widget.set_sensitive(True)
            GLib.idle_add(update_ui)
        else:
            self.text_expander.set_visible(True)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(False)
            threading.Thread(target=self.run_code, args=[widget, language, True]).start()
    
    def edit_button_clicked(self, button):
        """Handle edit button click."""
        self.emit('edit-clicked', self.txt, self.lang)
        
        if self.parent is not None and hasattr(self.parent, 'add_editor_tab_inline'):
            self.parent.add_editor_tab_inline(self.id_message, self.id_codeblock, self.txt, self.lang)
    
    def get_codeblocks(self):
        """Get codeblocks from parent message (legacy mode)."""
        if self.parent is None or not hasattr(self.parent, 'chat'):
            return []
        
        from ...utility.message_chunk import get_message_chunks
        chunks = get_message_chunks(self.parent.chat[self.id_message]["Message"])
        codeblocks = [chunk for chunk in chunks if chunk.type == "codeblock"]
        return codeblocks
    
    # === Public API for standalone usage ===
    
    def set_executable_languages(self, languages: list):
        """Set which languages should be executable."""
        self.runnable_languages = languages
    
    def get_code(self) -> str:
        """Get the current code text."""
        return self.txt
    
    def get_language(self) -> str:
        """Get the current language."""
        return self.lang
    
    def set_run_callback(self, callback):
        """Set the callback for command execution."""
        self.run_callback = callback
    
    def complete_execution(self, output: str | None):
        """
        Manually mark execution as complete with the given output.
        Useful when handling execution externally via signals.
        
        For execution_request mode, this will:
        - If output is None: mark as skipped
        - If output is provided: mark as completed with output
        - Mark the widget as responded
        - Disable Run/Skip buttons
        
        Args:
            output: The command output to display, or None to mark as skipped
        """
        if self.execution_request:
            # Mark as responded to prevent further clicks
            self.has_responded = True
            
            # Disable buttons
            if hasattr(self, 'run_button'):
                self.run_button.set_sensitive(False)
            if hasattr(self, 'skip_button'):
                self.skip_button.set_sensitive(False)
            
            if output is None:
                # Mark as skipped
                skip_icon = Gtk.Image.new_from_icon_name("action-unavailable-symbolic")
                skip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                skip_box.append(skip_icon)
                skip_box.append(Gtk.Label(label="Skipped"))
                self.skip_button.set_child(skip_box)
                self.skip_button.add_css_class("dim-label")
                
                self.status_label.set_text("Command was skipped")
                self.status_label.add_css_class("dim-label")
                self.status_label.set_visible(True)
                
                self.emit('skip-clicked')
            else:
                # Update UI via the completion handler
                self._on_execution_complete(output)
        elif hasattr(self, 'text_expander'):
            if output is not None:
                self.text_expander.set_child(
                    Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=output, selectable=True)
                )
                self.text_expander.set_visible(True)
                self.text_expander.set_expanded(True)
    
    def is_responded(self) -> bool:
        """Check if user has responded (for execution_request mode)."""
        return self.has_responded
