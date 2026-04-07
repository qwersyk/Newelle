import json
import threading

from gi.repository import Gtk, Adw, Gio, GLib

from ..controller import NewelleController
from ..constants import AVAILABLE_INTERFACES
from .extra_settings import ExtraSettingsBuilder
from ..utility.system import can_escape_sandbox
from ..handlers.interfaces.interface import Interface


class InterfacesWindow(Gtk.Window):
    def __init__(self, app):
        Gtk.Window.__init__(self, title=_("Interfaces"))
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.controller: NewelleController = app.win.controller
        self.app = app
        self.sandbox = can_escape_sandbox()

        self.set_default_size(500, 500)
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.set_titlebar(Adw.HeaderBar(css_classes=["flat"]))

        self.notification_block = Adw.ToastOverlay()
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.notification_block.set_child(self.scrolled_window)
        self.set_child(self.notification_block)

        self.settingsrows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.settingsrows,
            convert_constants=self._convert_constants,
        )
        self._interface_rows = {}
        self._play_buttons = {}
        self._enabled_switches = {}
        self._interfaces = {}
        self._interface_settings = {}

        self._load_interface_settings()
        self._build_ui()

    def _load_interface_settings(self):
        raw = self.settings.get_string("interfaces-settings")
        try:
            self._interface_settings = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            self._interface_settings = {}

    def _save_interface_settings(self):
        self.settings.set_string("interfaces-settings", json.dumps(self._interface_settings))

    def _get_interface_setting(self, key, field, default=None):
        if key in self._interface_settings and field in self._interface_settings[key]:
            return self._interface_settings[key][field]
        return default

    def _set_interface_setting(self, key, field, value):
        if key not in self._interface_settings:
            self._interface_settings[key] = {}
        self._interface_settings[key][field] = value
        self._save_interface_settings()

    def _convert_constants(self, _constants):
        return "interface"

    def _build_ui(self):
        self.main = Gtk.Box(
            margin_top=10, margin_start=10, margin_bottom=10, margin_end=10,
            valign=Gtk.Align.FILL, halign=Gtk.Align.CENTER,
            orientation=Gtk.Orientation.VERTICAL,
        )
        self.main.set_size_request(300, -1)
        self.scrolled_window.set_child(self.main)

        self.interfaces_group = Adw.PreferencesGroup(title=_("Available Interfaces"), description=_("Interfaces are background running services that allow third party applications to interact with Newelle. Enabling an interface means making it auto-start with Newelle."))
        self.main.append(self.interfaces_group)

        for key in AVAILABLE_INTERFACES:
            model = AVAILABLE_INTERFACES[key]
            interface: Interface = self.controller.handlers.get_object(AVAILABLE_INTERFACES, key)
            interface.set_controller(self.controller)
            self._interfaces[key] = interface

            self.settingsrows[(key, "interface", False)] = {}
            interface.set_extra_settings_update(
                lambda _, iface=interface, k=key: GLib.idle_add(
                    self._on_extra_settings_update, iface, k
                )
            )

            extra_settings = interface.get_extra_settings()
            if len(extra_settings) > 0:
                row = Adw.ExpanderRow(title=model["title"], subtitle=model.get("description", ""))
                self.settingsrows[(key, "interface", False)]["extra_settings_loaded"] = True
                self.extra_settings_builder.add_extra_settings(
                    AVAILABLE_INTERFACES, interface, row, settings=extra_settings
                )
            else:
                row = Adw.ActionRow(title=model["title"], subtitle=model.get("description", ""))
                self.settingsrows[(key, "interface", False)]["extra_settings_loaded"] = True

            self.settingsrows[(key, "interface", False)]["row"] = row
            self.settingsrows[(key, "interface", False)]["extra_settings"] = []

            enabled = self._get_interface_setting(key, "enabled", False)
            is_running = interface.is_running()
            print(is_running)
            enabled_switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=enabled)
            enabled_switch.connect("notify::active", self._on_enabled_toggled, key)
            self._enabled_switches[key] = enabled_switch

            play_button = Gtk.Button(
                css_classes=["flat"], valign=Gtk.Align.CENTER,
                icon_name="media-playback-stop-symbolic" if is_running else "media-playback-start-symbolic",
                sensitive=interface.is_installed(),
            )
            play_button.connect("clicked", self._on_play_button_clicked, key, interface)
            self._play_buttons[key] = play_button

            install_button = None
            if not interface.is_installed():
                if (interface.key, interface.schema_key) in self.controller.installing_handlers:
                    install_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
                    install_button.add_css_class("accent")
                    spinner = Gtk.Spinner(spinning=True)
                    install_button.set_child(spinner)
                else:
                    install_button = Gtk.Button(
                        css_classes=["flat"], valign=Gtk.Align.CENTER,
                        icon_name="folder-download-symbolic",
                    )
                    install_button.add_css_class("accent")
                    install_button.connect("clicked", self._on_install_button_clicked, key, interface)

            row.add_suffix(play_button)
            row.add_suffix(enabled_switch)

            if not self.sandbox and interface.requires_sandbox_escape() or not interface.is_installed():
                play_button.set_sensitive(False)

            if install_button is not None:
                row.add_suffix(install_button)
                self._interface_rows[key] = row

            self._interface_rows[key] = row
            self.interfaces_group.add(row)

        if len(AVAILABLE_INTERFACES) == 0:
            empty_row = Adw.ActionRow(title=_("No interfaces available"))
            self.interfaces_group.add(empty_row)

    def _on_enabled_toggled(self, switch, _pspec, key):
        self._set_interface_setting(key, "enabled", switch.get_active())

    def _on_play_button_clicked(self, button, key, interface: Interface):
        if interface.is_running():
            interface.stop()
            button.set_icon_name("media-playback-start-symbolic")
        else:
            interface.start()
            button.set_icon_name("media-playback-stop-symbolic")

    def _on_install_button_clicked(self, button, key, interface):
        spinner = Gtk.Spinner(spinning=True)
        button.set_child(spinner)
        button.disconnect_by_func(self._on_install_button_clicked)
        t = threading.Thread(target=self._install_interface_async, args=(button, interface, key))
        t.start()

    def _install_interface_async(self, button, interface, key):
        self.controller.installing_handlers[(interface.key, interface.schema_key)] = True
        interface.install()
        interface.on_installed()
        self.controller.installing_handlers[(interface.key, interface.schema_key)] = False
        GLib.idle_add(self._update_ui_after_install, button, interface, key)

    def _update_ui_after_install(self, button, interface, key):
        button.set_child(None)
        button.set_sensitive(False)
        play_button = self._play_buttons.get(key)
        if play_button is not None:
            play_button.set_sensitive(True)

    def _on_extra_settings_update(self, interface: Interface, key: str):
        row_state = self.settingsrows.get((key, "interface", False))
        if row_state is None:
            return
        row = row_state.get("row")
        if row is None:
            return
        extra_settings_list = row_state.get("extra_settings", [])
        for child in extra_settings_list:
            row.remove(child)
        row_state["extra_settings"] = []
        self.extra_settings_builder.add_extra_settings(
            AVAILABLE_INTERFACES, interface, row, settings=interface.get_extra_settings()
        )
