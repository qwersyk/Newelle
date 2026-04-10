from gi.repository import Gtk, Adw
from ..utility.system import primary_accel


class Shortcuts(Gtk.Window):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs,title=_('Help'))
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.set_titlebar(Adw.HeaderBar(css_classes=["flat"]))

        sect_main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.START,halign=Gtk.Align.CENTER)
        gr = Gtk.ShortcutsGroup(title=_("Shortcuts"))
        gr.append(Gtk.ShortcutsShortcut(title=_("New chat"), accelerator=primary_accel("n")))
        gr.append(Gtk.ShortcutsShortcut(title=_("New tab"), accelerator=primary_accel("t")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Open mini window"), accelerator=primary_accel("<Shift>m")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Close window"), accelerator=primary_accel("w")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Quit app"), accelerator=primary_accel("q")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Reload chat"), accelerator=primary_accel("r")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Reload folder"), accelerator=primary_accel("e")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Paste image"), accelerator=primary_accel("v")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Focus message box"), accelerator=primary_accel("l")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Start/stop recording"), accelerator=primary_accel("<Shift>r")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Stop generation"), accelerator=primary_accel("period")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Save"), accelerator=primary_accel("s")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Stop TTS"), accelerator=primary_accel("k")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Zoom in"), accelerator=primary_accel("plus")))
        gr.append(Gtk.ShortcutsShortcut(title=_("Zoom out"), accelerator=primary_accel("minus")))

        sect_main.append(gr)
        self.set_child(sect_main)
