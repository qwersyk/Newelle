import gettext

from gi.repository import Gtk, GLib, Gdk

from .widgets.chat_tab import ChatTab
from ..utility.system import has_primary_modifier

_ = gettext.gettext


class MiniWindow(Gtk.Window):
    def __init__(self, application, main_window, **kwargs):
        super().__init__(**kwargs)
        self.main_window = main_window
        self.set_application(application)
        self.set_title(_("Newelle Mini Window"))
        self.set_default_size(540, 520)
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("mini-window")

        active_tab = main_window.get_active_chat_tab()
        chat_id = active_tab.chat_id if active_tab is not None else main_window.chat_id

        self.chat_view = ChatTab(main_window, chat_id)
        self.chat_view.set_hexpand(True)
        self.chat_view.set_vexpand(True)
        self.set_child(self.chat_view)

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        GLib.idle_add(self.chat_view.show_chat)
        GLib.idle_add(self.chat_view.focus_input)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if has_primary_modifier(state) and Gdk.keyval_to_lower(keyval) == Gdk.KEY_w:
            self.close()
            return True
        return False
