import sys
import os
import signal
import gettext
import gi

from .utility.util import convert_history_openai

gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
from .ui_controller import HeadlessController 
from .ui.settings import Settings
from .window import MainWindow
from .ui.mini_window import MiniWindow
from .ui.shortcuts import Shortcuts
from .ui.thread_editing import ThreadEditing
from .ui.scheduled_tasks import ScheduledTasksWindow
from .ui.extension import Extension
from .utility.system import activate_macos_application, primary_accel
from .ui.interfaces import InterfacesWindow


class MyApp(Adw.Application):
    def __init__(self, version, **kwargs):
        self.version = version
        super().__init__(**kwargs)
        self.settings = Gio.Settings.new("io.github.qwersyk.Newelle")
        self.settingswindow = None
        self.win = None
        self.mini_windows = []
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
        .done{
            background-color: rgba(33, 155, 98,0.02);
        }
        .failed{
            background-color: rgba(254, 31, 41,0.02);
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
        .image{
            -gtk-icon-size:400px;
        }
        .video {
            min-height: 400px;
        }
        .mini-window {
            border-radius: 12px;
            border: 1px solid alpha(@card_fg_color, 0.15);
            box-shadow: 0 2px 4px alpha(black, 0.1);
            margin: 4px;
        }
        @keyframes pulse_opacity {
          0% { opacity: 1.0; }
          50% { opacity: 0.5; }
          100% { opacity: 1.0; }
        }

        .pulsing-label {
          animation-name: pulse_opacity;
          animation-duration: 1.8s;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
        }

        /* Chat history row styling */
        .navigation-sidebar row.chat-row-selected {
          background-color: alpha(@accent_bg_color, 0.15);
          border-radius: 6px;
        }
        
        .navigation-sidebar row.chat-row-selected:hover {
          background-color: alpha(@accent_bg_color, 0.25);
        }

        .window-bar-label {
                color: @view_fg_color;
                font-weight: 600;
        }
        @keyframes chat_locked_pulse {
            0% { background-color: alpha(@view_fg_color, 0.06); }
            50% { background-color: alpha(@view_fg_color, 0.12); }
            100% { background-color: alpha(@view_fg_color, 0.06); }
        }
        .chat-locked {
                background-color: alpha(@view_fg_color, 0.06);
                animation: chat_locked_pulse 1.6s ease-in-out infinite;
        }

        /* Folder row styling */
        .navigation-sidebar row.folder-row {
          border-radius: 6px;
          margin-top: 2px;
        }

        .navigation-sidebar row.folder-row-drop-hover {
          background-color: alpha(@accent_bg_color, 0.20);
          border-radius: 6px;
        }

        .folder-icon-picker-btn {
          min-width: 36px;
          min-height: 36px;
          padding: 4px;
        }

        .folder-icon-picker-btn:checked {
          background-color: alpha(@accent_bg_color, 0.25);
        }

        .unfolder-drop-area {
          border-radius: 6px;
        }

        .unfolder-drop-area-hover {
          background-color: alpha(@accent_bg_color, 0.12);
        }

        .message-text {
          line-height: 1.75;
        }

        .prompt-drop-target {
          outline: 2px solid @accent_color;
          outline-offset: -2px;
          border-radius: 12px;
        }
        '''
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css, -1)
        display = Gdk.Display.get_default() 
        if display is not None:
            icon_dir = os.getenv("NEWELLE_ICON_DIR")
            if icon_dir and os.path.isdir(icon_dir):
                Gtk.IconTheme.get_for_display(display).add_search_path(icon_dir)
            Gtk.StyleContext.add_provider_for_display(
                display,
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
        action = Gio.SimpleAction.new("preferences", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("settings", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("thread_editing", None)
        action.connect('activate', self.thread_editing_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("scheduled_tasks", None)
        action.connect('activate', self.scheduled_tasks_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("extension", None)
        action.connect('activate', self.extension_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("interfaces", None)
        action.connect('activate', self.interfaces_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("export_current_chat", None)
        action.connect('activate', self.export_current_chat_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("export_all_chats", None)
        action.connect('activate', self.export_all_chats_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("import_chats", None)
        action.connect('activate', self.import_chats_action)
        self.add_action(action)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.build_menubar()
    
    def create_action(self, name, callback, shortcuts=None):
        action = self.lookup_action(name)
        if action is None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        if shortcuts:
            self.set_action_shortcuts(name, shortcuts)

    def set_action_shortcuts(self, name, shortcuts):
        self.set_accels_for_action(f"app.{name}", shortcuts)

    def build_menubar(self):
        menubar = Gio.Menu()

        file_menu = Gio.Menu()
        file_menu.append(_("New Chat"), "app.new_chat")
        file_menu.append(_("New Tab"), "app.new_chat_tab")
        file_menu.append(_("Mini Window"), "app.open_mini_window")
        file_menu.append(_("Close Window"), "app.close_active_window")
        menubar.append_submenu(_("File"), file_menu)

        view_menu = Gio.Menu()
        view_menu.append(_("Explorer Tab"), "app.new_explorer_tab")
        view_menu.append(_("Terminal Tab"), "app.new_terminal_tab")
        view_menu.append(_("Browser Tab"), "app.new_browser_tab")
        menubar.append_submenu(_("View"), view_menu)

        chat_menu = Gio.Menu()
        chat_menu.append(_("Reload Chat"), "app.reload_chat")
        chat_menu.append(_("Reload Folder"), "app.reload_folder")
        chat_menu.append(_("Export Current Chat"), "app.export_current_chat")
        chat_menu.append(_("Export All Chats"), "app.export_all_chats")
        chat_menu.append(_("Import Chats"), "app.import_chats")
        menubar.append_submenu(_("Chat"), chat_menu)

        tools_menu = Gio.Menu()
        tools_menu.append(_("Keyboard shortcuts"), "app.shortcuts")
        tools_menu.append(_("Thread Editing"), "app.thread_editing")
        tools_menu.append(_("Scheduled Tasks"), "app.scheduled_tasks")
        tools_menu.append(_("Extensions"), "app.extension")
        tools_menu.append(_("Interfaces"), "app.interfaces")
        menubar.append_submenu(_("Tools"), tools_menu)

        self.set_menubar(menubar)

    def _has_visible_windows(self) -> bool:
        return any(window.get_mapped() for window in self.get_windows())

    def _ensure_main_window(self) -> MainWindow:
        if self.win is None:
            self.win = MainWindow(application=self)
            self.win.set_hide_on_close(True)
            self.win.connect("close-request", self.on_main_window_close_request)
        return self.win

    def _present_main_window(self) -> MainWindow:
        window = self._ensure_main_window()
        if getattr(window, "mini_mode", False):
            window.leave_mini_mode()
        window.present()
        if hasattr(window, "stabilize_initial_layout"):
            GLib.idle_add(window.stabilize_initial_layout)
        GLib.idle_add(activate_macos_application)
        GLib.timeout_add(100, lambda: activate_macos_application() or False)
        return window

    def _on_mini_window_destroy(self, window):
        if window in self.mini_windows:
            self.mini_windows.remove(window)

    def _open_new_mini_window(self, present: bool = True) -> MiniWindow:
        main_window = self._ensure_main_window()
        mini_window = MiniWindow(application=self, main_window=main_window)
        mini_window.connect("destroy", self._on_mini_window_destroy)
        self.mini_windows.append(mini_window)
        if present:
            mini_window.present()
        return mini_window

    def on_shortcuts_action(self, *a):
        shortcuts = Shortcuts(self)
        shortcuts.present()

    def on_about_action(self, *a):
        Adw.AboutWindow(transient_for=self.props.active_window,
                        application_name='Newelle',
                        application_icon='io.github.qwersyk.Newelle',
                        developer_name='qwersyk',
                        version=self.version,
                        issue_url='https://github.com/qwersyk/Newelle/issues',
                        website='https://github.com/qwersyk/Newelle',
                        developers=['Yehor Hliebov  https://github.com/qwersyk',"Francesco Caracciolo https://github.com/FrancescoCaracciolo", "Pim Snel https://github.com/mipmip"],
                        documenters=["Francesco Caracciolo https://github.com/FrancescoCaracciolo"],
                        designers=["Nokse22 https://github.com/Nokse22", "Jared Tweed https://github.com/JaredTweed"],
                        translator_credits="\n".join(["Amine Saoud (Arabic) https://github.com/amiensa","Heimen Stoffels (Dutch) https://github.com/Vistaus","Albano Battistella (Italian) https://github.com/albanobattistella","Oliver Tzeng (Traditional Chinese, all languages) https://github.com/olivertzeng","Aritra Saha (Bengali, Hindi) https://github.com/olumolu","NorwayFun (Georgian) https://github.com/NorwayFun"]),
                        copyright='© 2025 qwersyk').present()

    def thread_editing_action(self, *a):
        threadediting = ThreadEditing(self)
        threadediting.present()

    def scheduled_tasks_action(self, *a):
        scheduled_tasks = ScheduledTasksWindow(self)
        scheduled_tasks.present()

    def settings_action(self, *a): 
        if self.settingswindow is not None and self.settingswindow.get_visible():
            self.settingswindow.present()
            return
        settings = Settings(self, self._present_main_window().controller)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings

    def settings_action_paged(self, page=None, *a): 
        if self.settingswindow is not None and self.settingswindow.get_visible():
            self.settingswindow.present()
            return
        settings = Settings(self, self._present_main_window().controller, False, page)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings
    
    def close_settings(self, *a):
        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        settings.set_int("chat", self.win.chat_id)
        settings.set_string("path", os.path.normpath(self.win.main_path))
        self.win.update_settings()
        self.settingswindow.destroy()
        self.settingswindow = None
        return True

    def extension_action(self, *a):
        extension = Extension(self)
        def close(win):
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            settings.set_int("chat", self.win.chat_id)
            settings.set_string("path", os.path.normpath(self.win.main_path))
            self.win.update_settings()
            win.destroy()
            return True
        extension.connect("close-request", close) 
        extension.present()

    def interfaces_action(self, *a):
        interfaces = InterfacesWindow(self)
        interfaces.present()
    
    def export_current_chat_action(self, *a):
        """Export the current chat"""
        if self.win is not None:
            self.win.export_chat(export_all=False)
    
    def export_all_chats_action(self, *a):
        """Export all chats"""
        if self.win is not None:
            self.win.export_chat(export_all=True)
    
    def import_chats_action(self, *a):
        """Import chats from a file"""
        if self.win is not None:
            self.win.import_chat(None)
    
    def stdout_monitor_action(self, *a):
        """Show the stdout monitor dialog"""
        self.win.show_stdout_monitor_dialog()
    
    def close_window(self, *a):
        if self.win is None:
            return False
        if all(element.poll() is not None for element in self.win.streams):
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            if not self.win.mini_mode:
                settings.set_int("window-width", self.win.get_width())
                settings.set_int("window-height", self.win.get_height())
            self.win.controller.close_application()
            return False
        else:
            dialog = Adw.MessageDialog(
                transient_for=self.win,
                heading=_("Terminal threads are still running in the background"),
                body=_("When you close the window, they will be automatically terminated"),
                body_use_markup=True
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", self.close_message)
            dialog.present()
            return True
    
    def close_message(self,a,status):
        if status=="close":
            for i in self.win.streams:
                i.terminate()
            self.win.controller.close_application()
            self.quit()

    def on_main_window_close_request(self, window, *args):
        if not window.mini_mode:
            self.settings.set_int("window-width", window.get_width())
            self.settings.set_int("window-height", window.get_height())
        return False

    def on_activate(self, app):
        self._ensure_main_window()

        if self.settings.get_string("startup-mode") == "mini" and not self._has_visible_windows():
            self.settings.set_string("startup-mode", "normal")
            self._open_new_mini_window()
            return

        if not self._has_visible_windows():
            self._present_main_window()

    def focus_message(self, *a):
        self._present_main_window()
        self.win.focus_input()

    def reload_chat(self,*a):
        self._present_main_window()
        self.win.show_chat()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is rebooted')))

    def reload_folder(self,*a):
        self._present_main_window()
        self.win.update_folder()
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Folder is rebooted')))

    def new_chat(self,*a):
        self._present_main_window()
        self.win.new_chat(None)
        self.win.notification_block.add_toast(
                Adw.Toast(title=_('Chat is created')))

    def new_chat_tab(self, *a):
        self._present_main_window()
        self.win._on_create_chat_tab(None)

    def new_explorer_tab(self, *a):
        self._present_main_window()
        self.win.add_explorer_tab()

    def new_terminal_tab(self, *a):
        self._present_main_window()
        self.win.add_terminal_tab()

    def new_browser_tab(self, *a):
        self._present_main_window()
        self.win.add_browser_tab()

    def open_mini_window(self, *a):
        self._open_new_mini_window()

    def start_recording(self,*a):
        tab = self.win.get_active_chat_tab()
        if tab is None:
            return
        if not self.win.recording:
            self.win.start_recording(tab.recording_button)
        else:
            self.win.stop_recording(tab.recording_button)

    def stop_tts(self,*a):
        self.win.mute_tts(self.win.mute_tts_button)

    def stop_chat(self, *a):
        if self.win is not None and not self.win.status:
            self.win.stop_chat()

    def close_active_window(self, *a):
        window = self.props.active_window or getattr(self, "win", None)
        if window is None:
            return
        if window is self.win:
            window.close_active_chat_tab_or_window()
            return
        window.close()

    def quit_application(self, *a):
        if self.win is not None:
            if self.close_window():
                return
        self.quit()
    
    def do_shutdown(self):
        if self.win is not None:
            self.win.save_chat()
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            settings.set_int("chat", self.win.chat_id)
            settings.set_string("path", os.path.normpath(self.win.main_path))
            self.win.stream_number_variable += 1
        Gtk.Application.do_shutdown(self)

    def zoom(self, *a):
        zoom = min(250, self.settings.get_int("zoom") + 10)
        self.win.set_zoom(zoom)
        self.settings.set_int("zoom", zoom)

    def zoom_out(self, *a):
        zoom = max(100, self.settings.get_int("zoom") - 10)
        self.win.set_zoom(zoom)
        self.settings.set_int("zoom", zoom)
    
    def save(self, *a):
        self.win.save()
    def pretty_print_chat(self, *a):
        for msg in self.win.chat:
            print(msg["User"], msg["Message"])
    def debug(self, *a):
        self.pretty_print_chat()
        print(convert_history_openai(self.win.chat, [], True))

def run_headless(interface_key, version):
    """Start an interface without the GUI."""
    from .controller import NewelleController
    from .constants import AVAILABLE_INTERFACES

    if interface_key not in AVAILABLE_INTERFACES:
        available = ", ".join(AVAILABLE_INTERFACES.keys())
        print(f"Unknown interface '{interface_key}'. Available: {available}", file=sys.stderr)
        return 1

    info = AVAILABLE_INTERFACES[interface_key]
    print(f"Starting {info['title']} (headless)...")

    controller = NewelleController(sys.path)
    controller.ui_init()
    controller.handlers.load_handlers()
    controller.handlers.select_handlers(controller.newelle_settings, skip_auto_start_interfaces=True)
    ui_controller = HeadlessController(controller)
    controller.set_ui_controller(ui_controller)

    from .utility.replacehelper import ReplaceHelper
    ReplaceHelper.set_controller(controller)

    iface = controller.handlers.get_object(AVAILABLE_INTERFACES, interface_key, False)
    if iface is None:
        print(f"Failed to initialize interface '{interface_key}'", file=sys.stderr)
        return 1

    iface.start()
    if not iface.is_running():
        print(f"Interface '{interface_key}' failed to start", file=sys.stderr)
        return 1

    print(f"{info['title']} is running. Press Ctrl+C to stop.")

    # Run a GLib_MainLoop so GLib.idle_add (used by tool execution, etc.) works
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nStopping interface...")
        iface.stop()
    return 0


def main(version):
    app = MyApp(application_id="io.github.qwersyk.Newelle.macos", version = version)
    app.create_action('reload_chat', app.reload_chat, [primary_accel("r")])
    app.create_action('reload_folder', app.reload_folder, [primary_accel("e")])
    app.create_action('new_chat', app.new_chat, [primary_accel("n")])
    app.create_action('new_chat_tab', app.new_chat_tab, [primary_accel("t")])
    app.create_action('new_explorer_tab', app.new_explorer_tab)
    app.create_action('new_terminal_tab', app.new_terminal_tab)
    app.create_action('new_browser_tab', app.new_browser_tab)
    app.create_action('open_mini_window', app.open_mini_window, [primary_accel("<Shift>m")])
    app.create_action('close_active_window', app.close_active_window, [primary_accel("w")])
    app.create_action('quit', app.quit_application, [primary_accel("q")])
    app.create_action('focus_message', app.focus_message, [primary_accel("l")])
    app.create_action('start_recording', app.start_recording, [primary_accel("<Shift>r")])
    app.create_action('stop_chat', app.stop_chat, [primary_accel("period"), "Escape"])
    app.create_action('stop_tts', app.stop_tts, [primary_accel("k")])
    app.create_action('save', app.save, [primary_accel("s")])
    app.create_action('zoom', app.zoom, [primary_accel("plus"), primary_accel("equal")])
    app.create_action('zoom_out', app.zoom_out, [primary_accel("minus")])
    app.create_action('debug', app.debug, [primary_accel("b")])
    app.set_action_shortcuts("preferences", [primary_accel("comma")])
    app.set_action_shortcuts("shortcuts", ["<Shift>" + primary_accel("slash")])
    app.run(sys.argv)
