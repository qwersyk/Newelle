from gi.repository import Gtk, Gdk, GLib


class MiniWindow(Gtk.Window):
    """Compact window hosting the chat panel taken from the main window.

    The main window must have finished building its UI (see its "ui-built"
    signal), since the chat panel is reparented here and given back on close.
    """

    def __init__(self, application, main_window, **kwargs):
        super().__init__(application=application, **kwargs)
        self.main_window = main_window
        self.set_default_size(500, 650)
        self.set_title(_("Newelle Mini Window"))
        self.set_decorated(False)
        self.add_css_class("mini-window")

        # WindowHandle so the undecorated window can be dragged around
        self.handle = Gtk.WindowHandle()
        self.set_child(self.handle)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.handle.set_child(self.main_box)

        # Shown in the main window while the chat panel is hosted here
        self.placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.placeholder.set_halign(Gtk.Align.CENTER)
        self.placeholder.set_vexpand(True)
        placeholder_label = Gtk.Label(label=_("Chat is opened in mini window"))
        placeholder_label.add_css_class("dim-label")
        self.placeholder.append(placeholder_label)

        self.chat_panel = None
        self.original_parent = None
        self._attach_chat_panel()

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)
        self.connect('close-request', self._on_close_request)

    def _attach_chat_panel(self):
        self.chat_panel = self.main_window.secondary_message_chat_block
        parent = self.chat_panel.get_parent()
        if parent is not None:
            self.original_parent = parent
            self.chat_panel.unparent()
            self.original_parent.append(self.placeholder)
        self.main_box.append(self.chat_panel)
        # The mini window always uses the compact input bar, whatever the setting
        self.main_window._apply_compact_input_bar(True)
        # Reparenting and the compact layout rebuild steal focus; put it back
        # on the input once the window is mapped.
        GLib.idle_add(self.main_window.focus_input)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_close_request(self, *args):
        if self.chat_panel is not None:
            if self.chat_panel.get_parent() is not None:
                self.chat_panel.unparent()

            if self.placeholder.get_parent() is not None:
                self.placeholder.unparent()

            if self.original_parent is not None:
                self.original_parent.append(self.chat_panel)

            self.chat_panel = None
            self.original_parent = None
            # Give each tab back the layout chosen in the settings
            self.main_window._refresh_compact_input_bar()

        return False
