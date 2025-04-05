from gi.repository import Gtk, Adw


class Shortcuts(Gtk.Window):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs,title=_('Help'))
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.set_titlebar(Adw.HeaderBar(css_classes=["flat"]))

        sect_main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.START,halign=Gtk.Align.CENTER)
        gr = Gtk.ShortcutsGroup(title=_("Shortcuts"))
        gr.append(Gtk.ShortcutsShortcut(title=_("Reload chat"), accelerator='<primary>r'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Reload folder"), accelerator='<primary>r'))
        gr.append(Gtk.ShortcutsShortcut(title=_("New tab"), accelerator='<primary>t'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Paste Image"), accelerator='<primary>v'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Focus message box"), accelerator='<primary>l'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Start recording"), accelerator='<primary>s'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Stop TTS"), accelerator='<primary>k'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Zoom in"), accelerator='<primary>plus'))
        gr.append(Gtk.ShortcutsShortcut(title=_("Zoom out"), accelerator='<primary>minus'))

        sect_main.append(gr)
        self.set_child(sect_main)
