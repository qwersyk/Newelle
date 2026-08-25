import json
import threading

from gi.repository import Gtk, Adw, GLib

from ..controller import NewelleController
from ..constants import AVAILABLE_INTERFACES
from .extra_settings import ExtraSettingsBuilder
from ..utility.system import can_escape_sandbox
from ..utility.download_manager import get_download_manager
from ..handlers.interfaces.interface import Interface


class InterfacesPage(Adw.PreferencesPage):
    def __init__(self, app, controller: NewelleController):
        super().__init__(
            icon_name="controls-big-symbolic",
            title=_("Interfaces"),
        )
        self.settings = controller.settings
        self.controller = controller
        self.app = app
        self.sandbox = can_escape_sandbox()

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

    def refresh(self):
        """Rebuild the page when extension-provided interfaces change."""
        if hasattr(self, "interfaces_group"):
            self.remove(self.interfaces_group)
        self.settingsrows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.settingsrows,
            convert_constants=self._convert_constants,
        )
        self._interface_rows = {}
        self._play_buttons = {}
        self._enabled_switches = {}
        self._interfaces = {}
        self._load_interface_settings()
        self._build_ui()

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
        self.interfaces_group = Adw.PreferencesGroup(title=_("Available Interfaces"), description=_("Interfaces are background running services that allow third party applications to interact with Newelle. Enabling an interface means making it auto-start with Newelle."))
        self.add(self.interfaces_group)

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
                if get_download_manager().has_active(interface.get_install_source_id()):
                    install_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
                    install_button.add_css_class("accent")
                    spinner = Gtk.Spinner(spinning=True)
                    install_button.set_child(spinner)
                    install_button.connect(
                        "clicked", lambda _button: self.app.downloads_action()
                    )
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
            if interface.is_locally_running():
                interface.stop()
            else:
                Interface.stop_external(key, interface.path)
            button.set_icon_name("media-playback-start-symbolic")
        else:
            interface.start()
            button.set_icon_name("media-playback-stop-symbolic")

    def _on_install_button_clicked(self, button, key, interface):
        spinner = Gtk.Spinner(spinning=True)
        button.set_child(spinner)
        button.disconnect_by_func(self._on_install_button_clicked)
        button.connect("clicked", lambda _button: self.app.downloads_action())
        t = threading.Thread(target=self._install_interface_async, args=(button, interface, key))
        t.start()

    def _install_interface_async(self, button, interface, key):
        try:
            interface.install_with_progress(
                _("Install {name}").format(name=getattr(interface, "name", key))
            )
        except Exception as error:
            print(f"Error installing interface {key}: {error}")
        GLib.idle_add(self._update_ui_after_install, button, interface, key)

    def _update_ui_after_install(self, button, interface, key):
        installed = interface.is_installed()
        if installed:
            button.set_child(None)
            button.set_sensitive(False)
        else:
            button.set_child(Gtk.Image(icon_name="folder-download-symbolic"))
            button.set_sensitive(True)
            button.connect("clicked", self._on_install_button_clicked, key, interface)
        play_button = self._play_buttons.get(key)
        if play_button is not None:
            play_button.set_sensitive(installed)

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


class InterfacesWindow(Adw.Window):
    """Compatibility wrapper for callers that still open interfaces directly."""

    def __init__(self, app):
        super().__init__(
            title=_("Interfaces"),
            default_width=600,
            default_height=600,
            transient_for=app.win,
            modal=True,
        )
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(
            InterfacesPage(app, app.win.controller)
        )
        self.set_content(toolbar_view)
