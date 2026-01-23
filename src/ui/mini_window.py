from gi.repository import Gtk, GLib, Adw, Gdk

class MiniWindow(Gtk.Window):
    def __init__(self, application, main_window, **kwargs):
        super().__init__(**kwargs)
        self.main_window = main_window
        self.set_application(application)
        self.set_default_size(500, 100)
        self.set_title(_("Newelle"))
        self.set_decorated(False)
        self.add_css_class("mini-window")

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.main_box)

        self.placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.placeholder.set_halign(Gtk.Align.CENTER)
        self.placeholder.set_vexpand(True)
        placeholder_label = Gtk.Label(label=_("Chat is opened in mini window"))
        placeholder_label.add_css_class("dim-label")
        self.placeholder.append(placeholder_label)

        self.chat_panel = None
        if hasattr(main_window, 'secondary_message_chat_block'):
            self.chat_panel = main_window.secondary_message_chat_block
            if self.chat_panel.get_parent():
                self.original_parent = self.chat_panel.get_parent()
                self.chat_panel.unparent()

                for child in self.original_parent:
                    if isinstance(child, Gtk.Box) and child != self.chat_panel:
                        child.unparent()

                self.original_parent.append(self.placeholder)

            self.main_box.append(self.chat_panel)

        self.target_height = 100
        self.current_height = 100
        self.is_animating = False

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)
        self.connect('close-request', self._on_close_request)
        GLib.timeout_add(100, self._check_size)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_close_request(self, *args):
        if self.chat_panel:
            if self.chat_panel.get_parent():
                self.chat_panel.unparent()

            if self.placeholder.get_parent():
                self.placeholder.unparent()

            if hasattr(self, 'original_parent'):
                self.original_parent.append(self.chat_panel)

            self.chat_panel = None
            self.original_parent = None

        return False

    def _check_size(self):
        total_height = sum(
            getattr(self.main_window, block).get_allocated_height()
            for block in [
                'chat_list_block',
                'input_box',
                'chat_controls_entry_block'
            ]
            if hasattr(self.main_window, block)
        )

        target = min(max(total_height, 100), 500)

        if abs(target - self.target_height) > 5:
            self.target_height = target
            if not self.is_animating:
                self.is_animating = True
                GLib.timeout_add(16, self._animate_size)
        return True

    def _animate_size(self):
        diff = self.target_height - self.current_height
        if abs(diff) < 1:
            self.current_height = self.target_height
            self.is_animating = False
            self.set_default_size(500, int(self.current_height))
            return False

        self.current_height += diff * 0.3
        self.set_default_size(500, int(self.current_height))
        return True
