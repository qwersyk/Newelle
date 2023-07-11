import gi
from gi.repository import Gtk, Adw, Gio, Pango
import time


class ThreadEditing(Gtk.Window):
    def __init__(self, app, *args, **kwargs):
        super().__init__(*args, **kwargs,title=_('Thread editing'))
        self.set_default_size(400, 400)
        self.set_transient_for(app.win)
        self.set_modal(True)
        header = Adw.HeaderBar(css_classes=["flat"])
        self.set_titlebar(header)

        button_reload = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button_reload.set_child(icon)
        button_reload.connect("clicked", self.update_window)

        header.pack_end(button_reload)
        self.app = app
        self.update_window()
    def update_window(self,*a):
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.START,halign=Gtk.Align.CENTER,orientation=Gtk.Orientation.VERTICAL)
        if len(self.app.win.streams)==0:
            main.set_opacity(0.4)
            main.set_vexpand(True)
            main.set_valign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="network-offline-symbolic"))
            icon.set_css_classes(["empty-folder"])
            icon.set_valign(Gtk.Align.END)
            icon.set_vexpand(True)
            main.append(icon)
            main.append(Gtk.Label(label=_("No threads are running"), vexpand=True,valign=Gtk.Align.START,css_classes=["empty-folder", "heading"]))
        else:
            for i in range(len(self.app.win.streams)):
                stream_menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,css_classes=["card"],margin_top=10,margin_start=10,margin_end=10,margin_bottom=10)
                stream_menu.set_size_request(300, -1)
                box = Gtk.Box(margin_top=10,margin_start=10,margin_end=10,margin_bottom=10)
                box.append(Gtk.Label(label=_("Thread number: ")+str(i+1)))
                button = Gtk.Button(margin_start=5, margin_end=5,
                                                       valign=Gtk.Align.CENTER,halign=Gtk.Align.END, hexpand= True)
                button.connect("clicked", self.stop_flow)
                button.set_name(str(i))
                box.append(button)
                stream_menu.append(box)
                main.append(stream_menu)
                icon_name="media-playback-stop-symbolic"
                if  self.app.win.streams[i].poll() != None:
                    try:
                        code = str(self.app.win.streams[i].communicate()[0].decode())
                    except Exception:
                        code = None

                    icon_name = "emblem-ok-symbolic"
                    button.set_sensitive(False)
                    text_expander = Gtk.Expander(
                        label="Console", css_classes=["toolbar", "osd"], margin_start=10, margin_bottom=10,
                        margin_end=10
                    )
                    text_expander.set_child(Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=code, selectable=True))
                    stream_menu.append(text_expander)
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name=icon_name))
                icon.set_icon_size(Gtk.IconSize.INHERIT)
                button.set_child(icon)
        scrolled_window.set_child(main)
        self.set_child(scrolled_window)
    def stop_flow(self,widget):
        self.app.win.streams[int(widget.get_name())].terminate()
        self.update_window()
