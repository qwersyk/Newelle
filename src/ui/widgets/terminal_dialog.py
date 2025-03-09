import gi
from gi.repository import Gtk, Adw, GLib, Pango, Gdk
import sys

from gi.repository.GObject import GObject
if sys.platform != 'win32':
    gi.require_version('Vte', '3.91')
    from gi.repository import Vte

if sys.platform != 'win32':    
    class Terminal(Vte.Terminal):
        def __init__(self, script:list):
            super().__init__(css_classes=["terminal"])
            self.set_font(Pango.FontDescription.from_string("Monospace 12"))
            self.set_clear_background(False)
            pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT, None)
            self.set_pty(pty)
            pty.spawn_async(
                GLib.get_current_dir(),
                script,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
                None
            )
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self.on_key_press)
            self.add_controller(key_controller)

        def on_key_press(self, controller, keyval, keycode, state):
            ctrl = state & Gdk.ModifierType.CONTROL_MASK
            shift = state & Gdk.ModifierType.SHIFT_MASK
            if ctrl and keyval == Gdk.KEY_c:
                self.copy_clipboard()
                return True
            elif ctrl and keyval == Gdk.KEY_v:
                self.paste_clipboard()
                return True
            return False
        def get_output(self):
            txt = self.get_text_format(Vte.Format.TEXT)
            return txt 
else:
    class Terminal(Gtk.Box):
        def __init__(self, script:list):
            self.append(Gtk.Label(label="Terminal not supported"))

        def get_output(self):
            return ""

class TerminalDialog(Adw.Dialog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Accessibility
        self.set_title("Terminal") 
        self.connect("close-attempt", self.closing_terminal)
        self.set_can_close(False)
        self.output_func = lambda x: x
        # Toolbar View
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_css_class("osd")

        # Header Bar
        header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(header_bar)

        # Scrolled Window
        self.terminal_scroller = Gtk.ScrolledWindow(
            propagate_natural_height=True,
            propagate_natural_width=True
        )
        toolbar_view.set_content(self.terminal_scroller)
        self.set_child(toolbar_view)

    def load_terminal(self, command:list[str]):
        self.set_terminal(Terminal(command))

    def save_output_func(self, output_func):
        self.output_func = output_func

    def set_terminal(self, terminal):
        self.terminal = terminal
        self.terminal_scroller.set_child(terminal)

    def close_window(self,dialog, response):
        self.set_can_close(True)
        self.close()
        if response == "save": 
            self.output_func(self.terminal.get_output())
        else:
            self.output_func(None)

    def closing_terminal(self, *args):
        if self.get_can_close():
            return False
        dialog = Adw.AlertDialog(body="Do you want to send the output of the terminal to the LLM to get a response?\nNote: Only the visible text will be sent as response", title="Send output?")
        dialog.add_response("save", "Send output")
        dialog.add_response("close", "Discard output")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self.close_window)
        dialog.present()
        return True 
