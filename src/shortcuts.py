import gi, os
import pickle
from gi.repository import Gtk, Adw


class Shortcuts(Gtk.Window):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs,title='Help')
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.set_titlebar(Adw.HeaderBar(css_classes=["flat"]))

        sect_main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.START,halign=Gtk.Align.CENTER)
        gr = Gtk.ShortcutsGroup(title="Shortcuts")
        gr.append(Gtk.ShortcutsShortcut(title="Reload chat", accelerator='<primary>r'))
        gr.append(Gtk.ShortcutsShortcut(title="Reload folder", accelerator='<primary>r'))
        gr.append(Gtk.ShortcutsShortcut(title="New tab", accelerator='<primary>t'))

        sect_main.append(gr)
        self.set_child(sect_main)
