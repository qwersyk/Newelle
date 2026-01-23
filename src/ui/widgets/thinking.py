from gi.repository import Gtk, GObject, Adw, GLib
from .markuptextview import MarkupTextView

class ThinkingWidget(Gtk.Box):
    """
    A widget that displays a "thinking" state with an animated spinner
    and an expandable section revealing the step-by-step thinking process
    in a TextView.
    """
    __gsignals__ = {
        'thinking-started': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'thinking-stopped': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.add_css_class("card")
        self.set_margin_top(10)
        self.set_margin_end(10)
        self.set_margin_bottom(5)
        self.set_margin_start(5)
        self._is_thinking = False

        # --- UI Elements ---
        self.expander = Adw.ExpanderRow(
            title=_("Thoughts"),
            subtitle=_("Expand to see details"),
            show_enable_switch=False,
            expanded=False # Start collapsed
        )

        self.spinner = Gtk.Spinner(
            spinning=False,
            visible=False # Hide initially
        )
        
        # Icon to show when thinking is finished
        self.finished_icon = Gtk.Image(
            icon_name="brain-augemnted-symbolic",
            visible=True
        )
        
        # Add both spinner and icon to the start of the expander row header
        self.expander.add_prefix(self.spinner)
        self.expander.add_prefix(self.finished_icon)

        self.textview = MarkupTextView(
            editable=False, 
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            vexpand=True,
            hexpand=True,
            pixels_below_lines=5,
            left_margin=6,
            right_margin=6,
            top_margin=6,
            bottom_margin=6,
        )
        self.textbuffer = self.textview.get_buffer()

        scrolled_window = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=150,
            max_content_height=400,
            child=self.textview
        )
        scrolled_window.add_css_class("expander-inset-content")

        self.expander.add_row(scrolled_window)

        self.append(self.expander)


    def start_thinking(self, initial_message=""):
        """Starts the thinking process indication."""
        if self._is_thinking:
            return # Already thinking

        self._is_thinking = True
        # Use GLib.idle_add to ensure UI updates happen on the main thread
        GLib.idle_add(self._update_ui_start_thinking, initial_message)
        self.emit('thinking-started')

    def stop_thinking(self, final_title="LLM Thought Process"):
        """Stops the thinking process indication."""
        if not self._is_thinking:
            return # Not thinking

        print("ThinkingWidget: Stopping thinking...")
        self._is_thinking = False
        # Use GLib.idle_add to ensure UI updates happen on the main thread
        GLib.idle_add(self._update_ui_stop_thinking, final_title)
        self.emit('thinking-stopped')

    def append_thinking(self, text):
        """Appends text to the thinking process TextView."""
        if not self._is_thinking:
            print("Warning: append_thinking called while not in thinking state.")

        # Ensure UI updates happen on the main thread
        GLib.idle_add(self._update_ui_append_text, text)

    def set_thinking(self, text):
        """Sets the entire thinking process text, replacing existing content."""
        # Ensure UI updates happen on the main thread
        GLib.idle_add(self._update_ui_set_text, text)


    def clear_thinking(self):
        """Clears the thinking text."""
        GLib.idle_add(self.textbuffer.set_text, "", -1)

    def is_thinking(self):
        """Returns True if the widget is currently in the thinking state."""
        return self._is_thinking

    def _update_ui_start_thinking(self, initial_message):
        self.spinner.set_visible(True)
        self.spinner.start()
        self.finished_icon.set_visible(False)  # Hide the finished icon
        self.expander.set_title(_("Thinking..."))
        self.expander.set_subtitle(_("The LLM is thinking... Expand to see thought process"))
        self.textbuffer.set_text(initial_message, -1) # Clear and set initial text
        self._scroll_to_end()
        return GLib.SOURCE_REMOVE 

    def _update_ui_stop_thinking(self, final_title):
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.finished_icon.set_visible(True)  # Show the finished icon
        self.expander.set_title(final_title)
        if self.textbuffer.get_char_count() > 0:
             self.expander.set_subtitle(_("Expand to see details"))
        else:
             self.expander.set_subtitle(_("No thought process recorded"))
        return GLib.SOURCE_REMOVE # Remove the idle source

    def _update_ui_append_text(self, text):
        end_iter = self.textbuffer.get_end_iter()
        self.textbuffer.insert(end_iter, text, -1)
        # Auto-scroll to the end only if the user hasn't scrolled up manually
        # A simple way is to always scroll for this example
        self._scroll_to_end()
        return GLib.SOURCE_REMOVE # Remove the idle source

    def _update_ui_set_text(self, text):
        self.textbuffer.set_text(text, -1)
        # Scroll to the beginning after setting text
        start_iter = self.textbuffer.get_start_iter()
        self.textview.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)
        return GLib.SOURCE_REMOVE # Remove the idle source

    def _scroll_to_end(self):
        """Scrolls the TextView to the end."""
        end_iter = self.textbuffer.get_end_iter()
        # Mark ensures the view scrolls down even if text added rapidly
        end_mark = self.textbuffer.create_mark("end_mark", end_iter, False)
        self.textview.scroll_to_mark(end_mark, 0.0, True, 0.0, 1.0) # Align bottom
        # Clean up the mark
        GLib.idle_add(self.textbuffer.delete_mark, end_mark)

