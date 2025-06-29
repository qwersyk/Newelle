import gettext
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
from ..utility.stdout_capture import StdoutMonitor

# Add gettext function
_ = gettext.gettext


class StdoutMonitorDialog:
    """Window for monitoring stdout output in real-time with terminal interface"""
    
    def __init__(self, parent_window):
        self.parent_window = parent_window
        self.window = None
        self.stdout_monitor = None
        self.stdout_buffer = ""
        self.stdout_textview = None
        self.stdout_buffer_obj = None
        self.stdout_status_label = None
        self.stdout_line_count_label = None
        self.stdout_toggle_button = None
        
    def show_window(self):
        """Create and show the stdout monitor window"""
        if self.window is not None:
            self.window.present()
            return
            
        # Create the window
        self.window = Gtk.Window()
        self.window.set_title(_("Program Output Monitor"))
        self.window.set_default_size(800, 600)
        self.window.set_transient_for(self.parent_window)
        self.window.set_modal(False)
        
        # Create main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Header bar
        header_bar = Adw.HeaderBar(css_classes=["flat"])
        header_bar.set_title_widget(Gtk.Label(label=_("Program Output Monitor")))
        
        # Clear button
        clear_button = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            css_classes=["flat"]
        )
        clear_button.set_tooltip_text(_("Clear output"))
        clear_button.connect("clicked", self._clear_stdout_buffer)
        header_bar.pack_start(clear_button)
        
        # Toggle monitoring button
        # Check if monitoring is already active to set initial state
        is_already_monitoring = self.stdout_monitor and self.stdout_monitor.is_active()
        
        self.stdout_toggle_button = Gtk.ToggleButton(
            icon_name="media-playback-stop-symbolic" if is_already_monitoring else "media-playback-start-symbolic",
            css_classes=["destructive-action"] if is_already_monitoring else ["suggested-action"],
            active=is_already_monitoring
        )
        self.stdout_toggle_button.set_tooltip_text(_("Start/Stop monitoring"))
        self.stdout_toggle_button.connect("toggled", self._toggle_stdout_monitoring)
        header_bar.pack_end(self.stdout_toggle_button)
        
        content_box.append(header_bar)
        
        # Create terminal-like text view
        stdout_scrolled = Gtk.ScrolledWindow()
        stdout_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        stdout_scrolled.set_vexpand(True)
        stdout_scrolled.set_hexpand(True)
        
        self.stdout_textview = Gtk.TextView(
            editable=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            css_classes=["terminal-output"]
        )
        
        # Style the text view to look like a terminal
        self._apply_terminal_styling()
        
        self.stdout_buffer_obj = self.stdout_textview.get_buffer()
        
        # If we have accumulated buffer content, display it immediately
        if hasattr(self, '_accumulated_buffer') and self._accumulated_buffer:
            self.stdout_buffer_obj.set_text(self._accumulated_buffer)
            # Update line count
            line_count = self.stdout_buffer_obj.get_line_count()
            # We'll set this label shortly, but store the count for now
            self._initial_line_count = line_count
        else:
            self._initial_line_count = 0
        
        stdout_scrolled.set_child(self.stdout_textview)
        content_box.append(stdout_scrolled)
        
        # Status bar
        status_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=12,
            margin_end=12,
            margin_top=6,
            margin_bottom=6
        )
        
        self.stdout_status_label = Gtk.Label(
            label=_("Monitoring: Active") if is_already_monitoring else _("Monitoring: Stopped"),
            css_classes=["caption"]
        )
        status_box.append(self.stdout_status_label)
        
        # Line count label
        self.stdout_line_count_label = Gtk.Label(
            label=_("Lines: {}").format(self._initial_line_count),
            css_classes=["caption"],
            halign=Gtk.Align.END,
            hexpand=True
        )
        status_box.append(self.stdout_line_count_label)
        
        content_box.append(status_box)
        
        self.window.set_child(content_box)
        
        # Connect window close event
        self.window.connect("close-request", self._on_window_close_request) 
        # If monitoring is already active, start the display update timer
        if is_already_monitoring:
            GLib.timeout_add(100, self._update_stdout_display)
        
        # Auto-scroll to bottom if there's existing content
        if hasattr(self, '_accumulated_buffer') and self._accumulated_buffer:
            GLib.idle_add(self._scroll_to_bottom)
        
        # Present the window
        self.window.present()
    
    def _apply_terminal_styling(self):
        """Apply terminal-like styling to the text view"""
        css_provider = Gtk.CssProvider()
        css_data = """
        .terminal-output {
            background-color: #1e1e1e;
            color: #ffffff;
            font-family: 'DejaVu Sans Mono', 'Consolas', 'Liberation Mono', monospace;
            font-size: 11pt;
            padding: 12px;
        }
        
        .terminal-output text {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        
        .terminal-output:focus {
            outline: none;
            box-shadow: none;
        }
        """
        
        css_provider.load_from_data(css_data.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def _toggle_stdout_monitoring(self, button):
        """Toggle stdout monitoring on/off"""
        if button.get_active():
            self._start_stdout_monitoring()
        else:
            self._stop_stdout_monitoring()
    
    def _start_stdout_monitoring(self):
        """Start monitoring stdout"""
        if self.stdout_monitor and self.stdout_monitor.is_active():
            # Monitoring is already active, just update UI and start display timer
            if self.stdout_status_label:
                self.stdout_status_label.set_label(_("Monitoring: Active"))
            if self.stdout_toggle_button:
                self.stdout_toggle_button.set_icon_name("media-playback-stop-symbolic")
                self.stdout_toggle_button.remove_css_class("suggested-action")
                self.stdout_toggle_button.add_css_class("destructive-action")
            # Start the update timer if dialog is shown
            GLib.timeout_add(100, self._update_stdout_display)
            return
            
        self.stdout_status_label.set_label(_("Monitoring: Active"))
        self.stdout_toggle_button.set_icon_name("media-playback-stop-symbolic")
        self.stdout_toggle_button.remove_css_class("suggested-action")
        self.stdout_toggle_button.add_css_class("destructive-action")
        
        # Create stdout monitor
        self.stdout_monitor = StdoutMonitor(self._on_stdout_received)
        self.stdout_monitor.start_monitoring()
        
        # Start the update timer
        GLib.timeout_add(100, self._update_stdout_display)
    
    def _stop_stdout_monitoring(self):
        """Stop monitoring stdout"""
        if not self.stdout_monitor or not self.stdout_monitor.is_active():
            return
            
        self.stdout_status_label.set_label(_("Monitoring: Stopped"))
        self.stdout_toggle_button.set_icon_name("media-playback-start-symbolic")
        self.stdout_toggle_button.remove_css_class("destructive-action")
        self.stdout_toggle_button.add_css_class("suggested-action")
        
        # Stop the monitor
        if self.stdout_monitor:
            self.stdout_monitor.stop_monitoring()
    
    def _on_stdout_received(self, text):
        """Callback when new stdout text is received"""
        self.stdout_buffer += text
        
        # Also accumulate in a persistent buffer for when dialog is not shown
        if not hasattr(self, '_accumulated_buffer'):
            self._accumulated_buffer = ""
        self._accumulated_buffer += text
        
        # Limit accumulated buffer size to prevent memory issues
        if len(self._accumulated_buffer) > 100000:  # 100KB limit
            # Keep only the last 80KB
            self._accumulated_buffer = self._accumulated_buffer[-80000:]
    
    def _update_stdout_display(self):
        """Update the display with new stdout content"""
        if not self.stdout_monitor or not self.stdout_monitor.is_active():
            return False
            
        if self.stdout_buffer and self.stdout_buffer_obj:
            # Get the end iterator
            end_iter = self.stdout_buffer_obj.get_end_iter()
            
            # Insert the new text
            self.stdout_buffer_obj.insert(end_iter, self.stdout_buffer)
            
            # Clear the buffer
            self.stdout_buffer = ""
            
            # Auto-scroll to bottom
            mark = self.stdout_buffer_obj.get_insert()
            self.stdout_textview.scroll_mark_onscreen(mark)
            
            # Update line count
            line_count = self.stdout_buffer_obj.get_line_count()
            self.stdout_line_count_label.set_label(_("Lines: {}").format(line_count))
            
            # Limit buffer size to prevent memory issues
            if line_count > 1000:
                start_iter = self.stdout_buffer_obj.get_start_iter()
                # Remove first 200 lines
                line_200_iter = self.stdout_buffer_obj.get_iter_at_line(200)
                self.stdout_buffer_obj.delete(start_iter, line_200_iter)
        
        return True  # Continue the timer
    
    def _clear_stdout_buffer(self, button):
        """Clear the stdout display buffer"""
        if self.stdout_buffer_obj:
            self.stdout_buffer_obj.set_text("")
            self.stdout_line_count_label.set_label(_("Lines: 0"))
        self.stdout_buffer = ""
        # Also clear the accumulated buffer
        if hasattr(self, '_accumulated_buffer'):
            self._accumulated_buffer = ""
    
    def _on_window_close_request(self, window):
        """Handle window close event"""
        # Don't stop monitoring when window is closed - let it continue in background
        # Only reset the window reference
        if self.window is not None:
            self.window.close()
            self.window = None
        return False
    
    def close(self):
        """Close the window"""
        if self.window:
            self.window.close()
    
    def is_open(self):
        """Check if the window is currently open"""
        return self.window is not None
    
    def stop_monitoring_external(self):
        """Stop monitoring when called externally (e.g., on app shutdown)"""
        if self.stdout_monitor and self.stdout_monitor.is_active():
            self.stdout_monitor.stop_monitoring()
    
    def is_monitoring_active(self):
        """Check if stdout monitoring is currently active"""
        return self.stdout_monitor and self.stdout_monitor.is_active()
    
    def _scroll_to_bottom(self):
        """Scroll the text view to the bottom"""
        if self.stdout_buffer_obj and self.stdout_textview:
            # Get the end mark and scroll to it
            end_iter = self.stdout_buffer_obj.get_end_iter()
            end_mark = self.stdout_buffer_obj.create_mark(None, end_iter, False)
            self.stdout_textview.scroll_mark_onscreen(end_mark)
            self.stdout_buffer_obj.delete_mark(end_mark) 
