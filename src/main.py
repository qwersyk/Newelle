import sys
import os
import gettext
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Pango
from .ui.settings import Settings
from .window import MainWindow
from .ui.shortcuts import Shortcuts
from .ui.thread_editing import ThreadEditing
from .ui.extension import Extension
from .ui.mini_window import MiniWindow


class MyApp(Adw.Application):
    def __init__(self, version, **kwargs):
        self.version = version
        super().__init__(flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE, **kwargs)
        self.settings = Gio.Settings.new("io.github.qwersyk.Newelle")
        self.add_main_option("run-action", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Run an action", "ACTION")
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
                .window-bar-label {
                        color: @view_fg_color;
                        font-weight: 600;
                }
                @keyframes chat_locked_wave {
                    0% {
                        box-shadow: inset -400px 0 40px -60px transparent;
                    }
                    50% {
                        box-shadow: inset 0 0 40px -60px alpha(@view_fg_color, 0.15);
                    }
                    100% {
                        box-shadow: inset 400px 0 40px -60px transparent;
                    }
                }
                .chat-locked {
                        animation: chat_locked_wave 3s ease-in-out infinite;
                }
        '''
        self.windows = []
        self.shared_chats = None
        self.window_chat_usage = {}
        self.left_sidebar_visible = None
        self.right_sidebar_visible = None
        self.right_sidebar_name = None
        # Create shared window_bar (one for all windows)
        self.window_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
            css_classes=["toolbar"],
            hexpand=True,
        )
        self.window_bar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            propagate_natural_width=True,
            hexpand=True,
            min_content_height=48,
        )
        self.window_bar_scroll.set_child(self.window_bar)
        self.window_bar.set_visible(False)
        self.window_bar_scroll.set_visible(False)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css, -1)
        display = Gdk.Display.get_default()
        if display is not None:
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
        action = Gio.SimpleAction.new("settings", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("thread_editing", None)
        action.connect('activate', self.thread_editing_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("extension", None)
        action.connect('activate', self.extension_action)
        self.add_action(action)
        # Window management actions
        self.create_action('new_window', self.create_window)
        self.create_action('close_window', self.close_active_window)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

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
                        developers=['Yehor Hliebov  https://github.com/qwersyk',
                                    "Francesco Caracciolo https://github.com/FrancescoCaracciolo",
                                    "Pim Snel https://github.com/mipmip"],
                        documenters=["Francesco Caracciolo https://github.com/FrancescoCaracciolo"],
                        designers=["Nokse22 https://github.com/Nokse22", "Jared Tweed https://github.com/JaredTweed"],
                        translator_credits="\n".join(["Amine Saoud (Arabic) https://github.com/amiensa",
                                                      "Heimen Stoffels (Dutch) https://github.com/Vistaus",
                                                      "Albano Battistella (Italian) https://github.com/albanobattistella",
                                                      "Oliver Tzeng (Traditional Chinese, all languages) https://github.com/olivertzeng",
                                                      "Aritra Saha (Bengali, Hindi) https://github.com/olumolu"]),
                        copyright='© 2025 qwersyk').present()

    def thread_editing_action(self, *a):
        threadediting = ThreadEditing(self)
        threadediting.present()

    def settings_action(self, *a):
        settings = Settings(self, self.win.controller)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings

    def settings_action_paged(self, page=None, *a):
        settings = Settings(self, self.win.controller, False, page)
        settings.present()
        settings.connect("close-request", self.close_settings)
        self.settingswindow = settings

    def close_settings(self, *a):
        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        settings.set_int("chat", self.win.chat_id)
        settings.set_string("path", os.path.normpath(self.win.main_path))
        # Persist current profile snapshot before other windows sync
        if hasattr(self, "win") and hasattr(self.win, "controller"):
            active_profile = self.win.current_profile
            self.win.controller.save_profile_snapshot(active_profile)
            for win in self.windows:
                if win.current_profile == active_profile:
                    win.update_settings()
        self.settingswindow.destroy()
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

    def stdout_monitor_action(self, *a):
        """Show the stdout monitor dialog"""
        self.win.show_stdout_monitor_dialog()

    def close_window(self, *a):
        window = a[0] if len(a) > 0 and isinstance(a[0], MainWindow) else getattr(self, "win", None)
        if window is not None and hasattr(window, "controller"):
            window.controller.save_profile_snapshot(window.current_profile)
        if window in self.window_chat_usage:
            self.window_chat_usage.pop(window, None)
        if hasattr(self, "mini_win") and window is self.window:
            self.mini_win.close()

        streams = getattr(window, "streams", [])
        if all(element.poll() is not None for element in streams):
            settings = Gio.Settings.new('io.github.qwersyk.Newelle')
            settings.set_int("window-width", window.get_width())
            settings.set_int("window-height", window.get_height())
            if window is self.window:
                self.quit()
            return False
        else:
            dialog = Adw.MessageDialog(
                transient_for=window,
                heading=_("Terminal threads are still running in the background"),
                body=_("When you close the window, they will be automatically terminated"),
                body_use_markup=True
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Close"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda d, r, w=window: self.close_message(w, r))
            dialog.present()
            return True

    def close_message(self, window, status):
        if status == "close":
            for i in getattr(window, "streams", []):
                i.terminate()
            window.destroy()
            if window is self.window:
                self.quit()

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains("run-action"):
            action_name = options.lookup_value("run-action").get_string()
            if self.lookup_action(action_name):
                self.activate_action(action_name, None)
            else:
                command_line.printerr(f"Action '{action_name}' not found.\n")
                return 1

        self.activate()
        return 0

    def on_activate(self, app):
        if not hasattr(self, "win"):
            self.win = MainWindow(application=app)
            self.window = self.win
            self.windows.append(self.win)
            self.win.connect("close-request", self.close_window)
            self._attach_shared_chats(self.win)
            self.update_chat_ownership(self.win)
            self.store_sidebar_state(self.win)
            # First window becomes active controller
            if hasattr(self.win, "controller"):
                self.win.controller.settings_proxy.set_active(True)

        if self.settings.get_string("startup-mode") == "mini":
            if hasattr(self, "mini_win"):
                self.mini_win.close()
            self.mini_win = MiniWindow(application=self, main_window=self.win)
            self.mini_win.present()
            self.settings.set_string("startup-mode", "normal")
        else:
            self.window.present()

    def focus_message(self, *a):
        self.win.focus_input()

    def _attach_shared_chats(self, target_win, preserve_active=False):
        """Ensure all windows share the same chat list without saving to disk."""
        if self.shared_chats is None:
            self.shared_chats = target_win.chats
            return

        active_chat = None
        if preserve_active and 0 <= target_win.chat_id < len(target_win.chats):
            active_chat = target_win.chats[target_win.chat_id]

        target_win.controller.chats = self.shared_chats
        target_win.chats = self.shared_chats

        if active_chat is not None:
            if target_win.chat_id >= len(self.shared_chats):
                self.shared_chats.append(active_chat)
            else:
                self.shared_chats[target_win.chat_id] = active_chat

        target_win.chat_id = min(target_win.chat_id, len(self.shared_chats) - 1)
        target_win.chat = target_win.chats[target_win.chat_id]["chat"]
        target_win.controller.chat = target_win.chat

    def _ensure_chat_available(self, win):
        """Move the window to a free chat if its current one is locked elsewhere."""
        locked = self.get_locked_chat_ids(win)
        if win.chat_id not in locked:
            return

        for idx in range(len(win.chats)):
            if idx not in locked:
                win.chat_id = idx
                win.chat = win.chats[win.chat_id]["chat"]
                win.controller.chat = win.chat
                self.update_chat_ownership(win)
                return

        win.new_chat(None)
        self.update_chat_ownership(win)

    def update_chat_ownership(self, win):
        """Track which chat is currently open in each window."""
        self.window_chat_usage[win] = win.chat_id

    def get_locked_chat_ids(self, requester=None):
        """Return chat ids used by other windows."""
        return {
            cid
            for window, cid in self.window_chat_usage.items()
            if requester is None or window is not requester
        }

    def get_window_for_chat(self, chat_id):
        for window, cid in self.window_chat_usage.items():
            if cid == chat_id:
                return window
        return None

    def focus_chat_in_other_window(self, chat_id):
        """Switch to the window that owns the given chat, if any."""
        win = self.get_window_for_chat(chat_id)
        if win is not None:
            self.set_win(win)

    def store_sidebar_state(self, win):
        """Persist sidebar visibility from the given window."""
        self.left_sidebar_visible = win.main.get_show_sidebar()
        self.right_sidebar_visible = win.main_program_block.get_show_sidebar()
        self.right_sidebar_name = win.main_program_block.get_name()

    def apply_sidebar_state(self, win):
        """Apply stored sidebar visibility to a window without forcing switches."""
        if self.left_sidebar_visible is not None and win.main.get_show_sidebar() != self.left_sidebar_visible:
            win._sidebar_syncing = True
            win.main.set_show_sidebar(self.left_sidebar_visible)
            win._sidebar_syncing = False

        if self.right_sidebar_visible is not None:
            desired_name = self.right_sidebar_name or win.main_program_block.get_name()
            win._sidebar_syncing = True
            win.main_program_block.set_name(desired_name)
            if win.main_program_block.get_show_sidebar() != self.right_sidebar_visible:
                win.main_program_block.set_show_sidebar(self.right_sidebar_visible)
            win._sidebar_syncing = False

    def is_chat_locked(self, chat_id, requester=None):
        return chat_id in self.get_locked_chat_ids(requester)

    def on_chat_removed(self, removed_index):
        """Reindex chat ownership after a chat is deleted."""
        for window, cid in list(self.window_chat_usage.items()):
            if cid > removed_index:
                new_cid = cid - 1
                self.window_chat_usage[window] = new_cid
                window.chat_id = new_cid
                window.chat = window.chats[new_cid]["chat"]
                window.controller.chat = window.chat

    def update_window_bar(self):
        """Update the shared window_bar content."""
        # Clear existing children
        child = self.window_bar.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.window_bar.remove(child)
            child = next_child

        if len(self.windows) <= 1:
            self.window_bar.set_visible(False)
            self.window_bar_scroll.set_visible(False)
            return

        self.window_bar.set_visible(True)
        self.window_bar_scroll.set_visible(True)

        self.window_bar.set_homogeneous(True)

        for win in self.windows:
            name = _("Window")
            if 0 <= win.chat_id < len(win.chats):
                name = win.chats[win.chat_id]["name"]

            is_active = win is self.win
            label_text = name
            switch_btn = Gtk.Button(css_classes=[] if is_active else ["flat"], hexpand=True)
            switch_btn.set_child(
                Gtk.Label(
                    label=label_text,
                    ellipsize=Pango.EllipsizeMode.END,
                    xalign=0.5,
                    width_chars=10,
                    single_line_mode=True,
                    css_classes=["window-bar-label"],
                )
            )

            if is_active:
                switch_btn.set_sensitive(False)
                switch_btn.set_can_target(False)
                switch_btn.set_tooltip_text(_("Current window"))
            else:
                switch_btn.connect("clicked", lambda _b, w=win: self.switch_to_window(w))

            close_btn = Gtk.Button(css_classes=["flat"], icon_name="window-close-symbolic")
            close_btn.set_tooltip_text(_("Close window"))
            close_btn.connect("clicked", lambda _b, w=win: self.close_window_entry(w))

            item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, css_classes=["linked"], hexpand=True)
            item_box.append(switch_btn)
            item_box.append(close_btn)
            self.window_bar.append(item_box)

    def refresh_window_bar(self):
        """Refresh window switcher bar."""
        self.update_window_bar()

    def create_window(self, *a):
        """Create a new window and switch to it."""
        win = MainWindow(application=self)
        self._attach_shared_chats(win)
        win.new_chat(None)
        self._ensure_chat_available(win)
        self.windows.append(win)
        win.connect("close-request", self.close_window)
        self.set_win(win)
        win.show_chat()
        self.refresh_window_bar()

    def switch_to_window(self, target_win):
        """Switch to an existing window."""
        if target_win not in self.windows:
            return
        self.set_win(target_win)
        self.refresh_window_bar()

    def close_window_entry(self, target_win):
        """Close a window from the switcher bar."""
        if target_win not in self.windows or len(self.windows) <= 1:
            return
        was_active = target_win is self.win
        is_self_window = target_win is self.window
        self.window_chat_usage.pop(target_win, None)
        self.windows = [w for w in self.windows if w is not target_win]
        if not is_self_window:
            try:
                target_win.destroy()
            except Exception:
                pass
        if was_active and not is_self_window and self.windows:
            self.set_win(self.windows[0])
        self.refresh_window_bar()
        self.win.update_history()

    def close_active_window(self, *a):
        """Close the currently active window via shortcut."""
        self.close_window_entry(self.win)

    def switch_window_by_index(self, index, *a):
        """Switch to window by numeric index (1-based)."""
        if 0 <= index < len(self.windows):
            if self.windows[index] is not self.win:
                self.set_win(self.windows[index])

    def set_win(self, win):
        prev_win = getattr(self, "win", None)
        # Save settings of the window we are leaving
        if prev_win is not None and hasattr(prev_win, "controller"):
            prev_win.controller.save_profile_snapshot(prev_win.current_profile)
            prev_win.controller.settings_proxy.set_active(False)

        # Detach window_bar_scroll from previous window if exists
        if prev_win is not None and hasattr(prev_win, 'window_bar_container'):
            parent = self.window_bar_scroll.get_parent()
            if parent is not None:
                parent.remove(self.window_bar_scroll)

        if prev_win is not None and prev_win is not win:
            self._attach_shared_chats(win, preserve_active=True)
        else:
            self._attach_shared_chats(win)
        self._ensure_chat_available(win)
        # Apply the target window profile into Gio.Settings and refresh its state
        if hasattr(win, "controller"):
            win.controller.settings_proxy.set_active(True)
            win.controller.apply_profile(win.current_profile)
            win.update_settings()
        self.apply_sidebar_state(win)
        self.win = win
        self.win.main_program_block.unparent()
        self.window.set_content(self.win.main_program_block)

        # Attach window_bar_scroll to new window
        if hasattr(self.win, 'window_bar_container'):
            self.win.window_bar_container.append(self.window_bar_scroll)

        # Update model display in header
        if hasattr(self.win, 'update_model_popup'):
            self.win.update_model_popup()

        self.win.update_history()
        self.update_chat_ownership(self.win)
        self.refresh_window_bar()
        self.window.present()
        self.store_sidebar_state(self.win)

    def reload_chat(self, *a):
        self.win.show_chat()
        self.win.notification_block.add_toast(
            Adw.Toast(title=_('Chat is rebooted')))

    def reload_folder(self, *a):
        self.win.update_folder()
        self.win.notification_block.add_toast(
            Adw.Toast(title=_('Folder is rebooted')))

    def new_chat(self, *a):
        self.win.new_chat(None)
        self.win.notification_block.add_toast(
            Adw.Toast(title=_('Chat is created')))

    def start_recording(self, *a):
        if not self.win.recording:
            self.win.start_recording(self.win.recording_button)
        else:
            self.win.stop_recording(self.win.recording_button)

    def stop_tts(self, *a):
        self.win.mute_tts(self.win.mute_tts_button)

    def do_shutdown(self):
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


def main(version):
    app = MyApp(application_id="io.github.qwersyk.Newelle", version=version)
    app.create_action('reload_chat', app.reload_chat, ['<primary>r'])
    app.create_action('reload_folder', app.reload_folder, ['<primary>e'])
    app.create_action('new_chat', app.new_chat, ['<primary>t'])
    app.create_action('focus_message', app.focus_message, ['<primary>l'])
    app.create_action('start_recording', app.start_recording, ['<primary>g'])
    app.create_action('stop_tts', app.stop_tts, ['<primary>k'])
    app.create_action('save', app.save, ['<primary>s'])
    app.create_action('zoom', app.zoom, ['<primary>plus'])
    app.create_action('zoom', app.zoom, ['<primary>equal'])
    app.create_action('zoom_out', app.zoom_out, ['<primary>minus'])
    app.create_action('new_window', app.create_window, ['<primary><shift>n'])
    app.create_action('close_window', app.close_active_window, ['<primary><shift>w'])
    for i in range(1, 10):
        idx = i - 1
        app.create_action(
            f'switch_window_{i}',
            lambda action, param=None, idx=idx: app.switch_window_by_index(idx),
            [f'<primary>{i}'],
        )
    app.run(sys.argv)
