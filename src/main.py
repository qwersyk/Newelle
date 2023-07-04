import sys
import gi, os

gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
import pickle
from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GtkSource, GObject
from .settings import Settings
from .window import MainWindow
from .shortcuts import Shortcuts
from .thread_editing import ThreadEditing





class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        css = '''
        .code{
        background-color: rgb(38,38,38);
        }

        .code .sourceview text{
            background-color: rgb(38,38,38);
        }
        .code .sourceview border gutter{
            background-color: rgb(38,38,38);
        }
        .sourceview{
            color: rgb(192,191,188);
        }
        .copy-action{
            color:rgb(255,255,255);
            background-color: rgb(38,162,105);
        }
        .large{
            -gtk-icon-size:100px;
        }
        .empty-folder{
            font-size:25px;
            font-weight:800;
            -gtk-icon-size:120px;
        }
        .user{
            background-color: rgba(61, 152, 255,0.03);
        }
        .assistant{
            background-color: rgba(184, 134, 17,0.02);
        }
        .console-done{
            background-color: rgba(33, 155, 98,0.02);
        }
        .console-error{
            background-color: rgba(254, 31, 41,0.02);
        }
        .console-restore{
            background-color: rgba(184, 134, 17,0.02);
        }
        .file{
            background-color: rgba(222, 221, 218,0.03);
        }
        .folder{
            background-color: rgba(189, 233, 255,0.03);
        }
        .message-warning{
            background-color: rgba(184, 134, 17,0.02);
        }
        .transparent{
            background-color: rgba(0,0,0,0);
        }
        .chart{
            background-color: rgba(61, 152, 255,0.25);
        }
        .right-angles{
            border-radius: 0;
        }

        '''
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css, -1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.connect('activate', self.on_activate)
        action = Gio.SimpleAction.new("about", None)
        action.connect('activate', self.on_about_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("shortcuts", None)
        action.connect('activate', self.on_shortcuts_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("settings", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("thread_editing", None)
        action.connect('activate', self.thread_editing_action)
        self.add_action(action)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

    def on_shortcuts_action(self, widget, _):
        shortcuts = Shortcuts(self)
        shortcuts.present()

    def on_about_action(self, widget, _):
        Adw.AboutWindow(transient_for=self.props.active_window,
                        application_name='Newelle',
                        application_icon='io.github.qwersyk.Newelle',
                        developer_name='qwersyk',
                        version='0.1.5',
                        developers=['qwersyk'],
                        copyright='© 2023 qwersyk').present()

    def thread_editing_action(self, widget, _):
        threadediting = ThreadEditing(self)
        threadediting.present()

    def settings_action(self, widget, _):
        settings = Settings(self)
        settings.present()

    def on_activate(self, app):
        self.win = MainWindow(application=app)
        self.win.present()

    def reload_chat(self,*a):
        self.win.show_chat()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is rebooted')))

    def reload_folder(self,*a):
        self.win.update_folder()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Folder is rebooted')))

    def new_chat(self,*a):
        self.win.new_chat(None)
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is created')))

    def do_shutdown(self):
        os.chdir(os.path.expanduser("~"))
        with open(self.win.path + self.win.filename, 'wb') as f:
            pickle.dump(self.win.chats, f)
        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        settings.set_int("chat", self.win.chat_id)
        settings.set_string("path", os.path.normpath(self.win.main_path))
        Gtk.Application.do_shutdown(self)


def main(version):
    app = MyApp(application_id="io.github.qwersyk.Newelle")
    app.create_action('reload_chat', app.reload_chat, ['<primary>r'])
    app.create_action('reload_folder', app.reload_folder, ['<primary>e'])
    app.create_action('new_chat', app.new_chat, ['<primary>t'])
    app.run(sys.argv)
