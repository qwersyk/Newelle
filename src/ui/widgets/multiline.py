from gi.repository import Gtk, Gdk, GLib
from .. import apply_css_to_widget

class MultilineEntry(Gtk.Box):

    def __init__(self, enter_on_ctrl=False):
        Gtk.Box.__init__(self)
        self.placeholding = True
        self.placeholder = ""
        self.enter_func = None
        self.on_change_func = None
        self.on_image_pasted = lambda *a: None
        self.enter_on_ctrl = enter_on_ctrl
        # Handle enter key
        # Call handle_enter_key only when shift is not pressed
        # shift + enter = new line
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", lambda controller, keyval, keycode, state:
            self.handle_enter_key() if keyval == Gdk.KEY_Return and (not self.enter_on_ctrl and not (state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK)) or self.enter_on_ctrl and (state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK))) 
            else self.handle_paste() if keyval == Gdk.KEY_v and (state & Gdk.ModifierType.CONTROL_MASK) else None
        )

        # Scroll
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_max_content_height(150)
        scroll.set_propagate_natural_height(True)
        scroll.set_margin_start(10)
        scroll.set_margin_end(10)
        self.append(scroll)

        # TextView
        self.input_panel = Gtk.TextView()
        self.input_panel.set_wrap_mode(Gtk.WrapMode.WORD)
        self.input_panel.set_hexpand(True)
        self.input_panel.set_vexpand(False)
        self.input_panel.set_top_margin(5)
        self.input_panel.add_controller(key_controller)
        # Event management
        focus_controller = Gtk.EventControllerFocus.new()
        self.input_panel.add_controller(focus_controller)
         
        # Connect the enter and leave signals
        focus_controller.connect("enter", self.on_focus_in, None)
        focus_controller.connect("leave", self.on_focus_out, None)

        # Add style to look like a GTK Entry
        self.add_css_class("card")
        self.add_css_class("frame")
        self.input_panel.add_css_class("multilineentry")
        apply_css_to_widget(self.input_panel, """
            .multilineentry {
                background-color: rgba(0,0,0,0);
                font-size: 15px;
                font-family: 'System UI', -apple-system, sans-serif;
            }
            .multilineentry text {
                background-color: transparent;
            }
        """)

        # Add TextView to the ScrolledWindow
        scroll.set_child(self.input_panel)

    def set_enter_on_ctrl(self, enter_on_ctrl):
        self.enter_on_ctrl = enter_on_ctrl

    def handle_paste(self):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.read_texture_async(None, self.image_pasted)
    
    def image_pasted(self, clipboard, texture):
        try:
            img : Gdk.MemoryTexture = clipboard.read_texture_finish(texture)
        except Exception as _:
            return
        self.on_image_pasted(img.save_to_png_bytes().get_data())

    def set_placeholder(self, text):
        self.placeholder = text
        if self.placeholding:
            self.set_text(self.placeholder, False)

    def set_on_image_pasted(self, function):
        self.on_image_pasted = function

    def set_on_enter(self, function):
        """Add a function that is called when ENTER (without SHIFT) is pressed"""
        self.enter_func = function

    def handle_enter_key(self):
        if self.enter_func is not None:
            GLib.idle_add(self.set_text, self.get_text().rstrip("\n"))
            GLib.idle_add(self.enter_func, self)

    def get_input_panel(self):
        return self.input_panel

    def set_text(self, text, remove_placeholder=True):
        if remove_placeholder:
            self.placeholding = False
        self.input_panel.get_buffer().set_text(text)

    def get_text(self):
        return self.input_panel.get_buffer().get_text(self.input_panel.get_buffer().get_start_iter(), self.input_panel.get_buffer().get_end_iter(), False)

    def on_focus_in(self, widget, data):
        if self.placeholding:
            self.set_text("", False)
            self.placeholding = False

    def on_focus_out(self, widget, data):
        if self.get_text() == "":
            self.placeholding = True
            self.set_text(self.placeholder, False)

    def set_on_change(self, function):
        self.on_change_func = function
        self.input_panel.get_buffer().connect("changed", self.on_change)

    def on_change(self, buffer):
        if self.on_change_func is not None:
            self.on_change_func(self)

