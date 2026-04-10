from typing import Any, Callable
import threading
import time

from gi.repository import Gtk, Adw, Gio, GLib

from ..handlers import Handler
from ..utility.system import open_website, open_folder
from .widgets import ComboRowHelper, MultilineEntry


class ExtraSettingsBuilder:
    def __init__(
        self,
        settingsrows: dict,
        convert_constants: Callable[[Any], str],
        on_before_rebuild: Callable[[dict[str, Any], Handler], None] | None = None,
    ):
        self.settingsrows = settingsrows
        self.convert_constants = convert_constants
        self.on_before_rebuild = on_before_rebuild
        self.slider_labels = {}
        self.downloading = {}
        self.model_threads = {}

    def add_extra_settings(
        self,
        constants: dict[str, Any],
        handler: Handler,
        row: Adw.ExpanderRow,
        nested_settings: list | None = None,
        settings: list | None = None,
    ):
        if nested_settings is None:
            row_key = (handler.key, self.convert_constants(constants), handler.is_secondary())
            if row_key not in self.settingsrows:
                self.settingsrows[row_key] = {}
            self.settingsrows[row_key]["extra_settings"] = []
            settings_to_render = settings if settings is not None else handler.get_extra_settings()
        else:
            settings_to_render = nested_settings

        for setting in settings_to_render:
            setting_row = self.create_extra_setting(setting, handler, constants)
            if setting_row is None:
                continue
            row.add_row(setting_row)
            self.settingsrows[(handler.key, self.convert_constants(constants), handler.is_secondary())][
                "extra_settings"
            ].append(setting_row)

    def on_row_expanded_build_settings(self, row, _pspec, constants, handler):
        if not row.get_property("expanded"):
            return
        settings_key = (handler.key, self.convert_constants(constants), handler.is_secondary())
        row_state = self.settingsrows.get(settings_key)
        if row_state is None or row_state.get("extra_settings_loaded", False):
            return
        pending_settings = row_state.pop("pending_extra_settings", None)
        self.add_extra_settings(constants, handler, row, settings=pending_settings)
        row_state["extra_settings_loaded"] = True

    def create_extra_setting(
        self,
        setting: dict,
        handler: Handler,
        constants: dict[str, Any],
    ) -> Adw.ExpanderRow | Adw.ActionRow | Adw.ComboRow | None:
        if setting["type"] == "entry":
            row = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            value = str(handler.get_setting(setting["key"]))
            password = setting.get("password", False)
            entry = Gtk.Entry(
                valign=Gtk.Align.CENTER,
                text=value,
                name=setting["key"],
                visibility=(not password),
            )
            entry.connect("changed", self.setting_change_entry, constants, handler)
            row.add_suffix(entry)
            if password:
                button = Gtk.Button(
                    valign=Gtk.Align.CENTER,
                    name=setting["key"],
                    css_classes=["flat"],
                    icon_name="view-show",
                )
                button.connect(
                    "clicked",
                    lambda _button, current_entry: current_entry.set_visibility(
                        not current_entry.get_visibility()
                    ),
                    entry,
                )
                row.add_suffix(button)
        elif setting["type"] == "multilineentry":
            row = Adw.ExpanderRow(title=setting["title"], subtitle=setting["description"])
            value = str(handler.get_setting(setting["key"]))
            entry = MultilineEntry()
            entry.set_text(value)
            entry.set_on_change(self.setting_change_multilinentry)
            entry.name = setting["key"]
            entry.constants = constants
            entry.handler = handler
            row.add_row(entry)
        elif setting["type"] == "button":
            row = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            button = Gtk.Button(valign=Gtk.Align.CENTER, name=setting["key"])
            if "label" in setting:
                button.set_label(setting["label"])
            elif "icon" in setting:
                button.set_icon_name(setting["icon"])
            button.connect("clicked", setting["callback"])
            row.add_suffix(button)
        elif setting["type"] == "toggle":
            row = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            value = bool(handler.get_setting(setting["key"]))
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER, active=value, name=setting["key"])
            toggle.connect("state-set", self.setting_change_toggle, constants, handler)
            row.add_suffix(toggle)
        elif setting["type"] == "combo":
            row = Adw.ComboRow(title=setting["title"], subtitle=setting["description"], name=setting["key"])
            helper = ComboRowHelper(row, setting["values"], handler.get_setting(setting["key"]))
            helper.connect("changed", self.setting_change_combo, constants, handler)
        elif setting["type"] == "range":
            row = Adw.ActionRow(title=setting["title"], subtitle=setting["description"], valign=Gtk.Align.CENTER)
            box = Gtk.Box()
            scale = Gtk.Scale(name=setting["key"], round_digits=setting["round-digits"])
            scale.set_range(setting["min"], setting["max"])
            scale.set_value(round(handler.get_setting(setting["key"]), setting["round-digits"]))
            scale.set_size_request(120, -1)
            scale.connect("change-value", self.setting_change_scale, constants, handler)
            label = Gtk.Label(label=str(handler.get_setting(setting["key"])))
            box.append(label)
            box.append(scale)
            self.slider_labels[scale] = label
            row.add_suffix(box)
        elif setting["type"] == "spin":
            adj = Gtk.Adjustment(
                value=handler.get_setting(setting["key"]),
                lower=setting["min"],
                upper=setting["max"],
                step_increment=setting["step"],
                page_increment=setting["page"],
            )
            row = Adw.SpinRow(
                title=setting["title"],
                subtitle=setting["description"],
                adjustment=adj,
                digits=setting["round-digits"],
            )
            row.set_name(setting["key"])
            row.connect("notify::value", self.setting_change_spin, constants, handler)
        elif setting["type"] == "nested":
            row = Adw.ExpanderRow(title=setting["title"], subtitle=setting["description"])
            self.add_extra_settings(constants, handler, row, setting["extra_settings"])
        elif setting["type"] == "download":
            row = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
            if setting["is_installed"]:
                actionbutton.set_icon_name("user-trash-symbolic")
                actionbutton.connect(
                    "clicked",
                    lambda _button, cb=setting["callback"], key=setting["key"]: cb(key),
                )
                actionbutton.add_css_class("error")
            else:
                actionbutton.set_icon_name(
                    "folder-download-symbolic"
                    if "download-icon" not in setting
                    else setting["download-icon"]
                )
                actionbutton.connect("clicked", self.download_setting, setting, handler)
                actionbutton.add_css_class("accent")
            row.add_suffix(actionbutton)
        else:
            return None

        if "website" in setting:
            website_button = self.create_web_button(setting["website"])
            row.add_suffix(website_button)
        if "folder" in setting:
            folder_button = self.create_web_button(setting["folder"], folder=True)
            row.add_suffix(folder_button)
        if "refresh" in setting:
            refresh_icon = setting.get("refresh_icon", "view-refresh-symbolic")
            refreshbutton = Gtk.Button(icon_name=refresh_icon, valign=Gtk.Align.CENTER, css_classes=["flat"])

            def refresh_setting(_button, cb=setting["refresh"]):
                refreshbutton.set_child(Gtk.Spinner(spinning=True))
                cb(_button)

            refreshbutton.connect("clicked", refresh_setting)
            row.add_suffix(refreshbutton)

        return row

    def on_setting_change(
        self,
        constants: dict[str, Any],
        handler: Handler,
        key: str,
        force_change: bool = False,
    ):
        if not force_change:
            setting_info = [info for info in handler.get_extra_settings_list() if info["key"] == key]
            if len(setting_info) == 0:
                return
            setting_info = setting_info[0]
        else:
            setting_info = {}

        if force_change or (
            "update_settings" in setting_info and setting_info["update_settings"]
        ):
            settings_key = (handler.key, self.convert_constants(constants), handler.is_secondary())
            row_state = self.settingsrows.get(settings_key)
            if row_state is None:
                return
            if self.on_before_rebuild is not None:
                self.on_before_rebuild(constants, handler)
            if not row_state.get("extra_settings_loaded", True):
                row_state["pending_extra_settings"] = handler.get_extra_settings()
                return

            row = row_state["row"]
            setting_list = row_state.get("extra_settings", [])
            for setting_row in setting_list:
                row.remove(setting_row)
            self.add_extra_settings(constants, handler, row)

    def setting_change_entry(self, entry, constants, handler: Handler):
        name = entry.get_name()
        handler.set_setting(name, entry.get_text())
        self.on_setting_change(constants, handler, name)

    def setting_change_multilinentry(self, entry):
        entry.handler.set_setting(entry.name, entry.get_text())
        self.on_setting_change(entry.constants, entry.handler, entry.name)

    def setting_change_toggle(self, toggle, _state, constants, handler):
        enabled = toggle.get_active()
        handler.set_setting(toggle.get_name(), enabled)
        self.on_setting_change(constants, handler, toggle.get_name())

    def setting_change_scale(self, scale, _scroll, value, constants, handler):
        setting = scale.get_name()
        digits = scale.get_round_digits()
        value = round(value, digits)
        self.slider_labels[scale].set_label(str(value))
        handler.set_setting(setting, value)
        self.on_setting_change(constants, handler, setting)

    def setting_change_spin(self, row, _pspec, constants, handler):
        setting = row.get_name()
        value = row.get_value()
        if row.get_digits() == 0:
            value = int(value)

        handler.set_setting(setting, value)
        self.on_setting_change(constants, handler, setting)

    def setting_change_combo(self, helper, value, constants, handler):
        setting = helper.combo.get_name()
        handler.set_setting(setting, value)
        self.on_setting_change(constants, handler, setting)

    def download_setting(self, button: Gtk.Button, setting, handler: Handler, uninstall=False):
        if uninstall:
            return
        box = Gtk.Box(homogeneous=True, spacing=4)
        box.set_orientation(Gtk.Orientation.VERTICAL)
        icon = Gtk.Image.new_from_gicon(
            Gio.ThemedIcon(
                name=(
                    "folder-download-symbolic"
                    if "download-icon" not in setting
                    else setting["download-icon"]
                )
            )
        )
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        progress = Gtk.ProgressBar(hexpand=False)
        progress.set_size_request(4, 4)
        box.append(icon)
        box.append(progress)
        button.set_child(box)
        button.disconnect_by_func(self.download_setting)
        button.connect("clicked", lambda _x: setting["callback"](setting["key"]))
        th = threading.Thread(target=self.download_setting_thread, args=(handler, setting, button, progress))
        self.model_threads[(setting["key"], handler.key)] = [th, 0]
        th.start()

    def update_download_status_setting(self, handler, setting, progressbar):
        while (setting["key"], handler.key) in self.downloading and self.downloading[(setting["key"], handler.key)]:
            try:
                perc = setting["download_percentage"](setting["key"])
                GLib.idle_add(progressbar.set_fraction, perc)
            except Exception as e:
                print(e)
            time.sleep(1)

    def download_setting_thread(
        self,
        handler: Handler,
        setting: dict,
        button: Gtk.Button,
        progressbar: Gtk.ProgressBar,
    ):
        self.model_threads[(setting["key"], handler.key)][1] = threading.current_thread().ident
        self.downloading[(setting["key"], handler.key)] = True
        th = threading.Thread(
            target=self.update_download_status_setting,
            args=(handler, setting, progressbar),
        )
        th.start()
        setting["callback"](setting["key"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button.add_css_class("error")
        button.set_child(icon)
        self.downloading[(setting["key"], handler.key)] = False

    def create_web_button(self, website, folder=False) -> Gtk.Button:
        wbbutton = Gtk.Button(icon_name="internet-symbolic" if not folder else "search-folder-symbolic")
        wbbutton.add_css_class("flat")
        wbbutton.set_valign(Gtk.Align.CENTER)
        wbbutton.set_name(website)
        if not folder:
            wbbutton.connect("clicked", lambda _: open_website(website))
        else:
            wbbutton.connect("clicked", lambda _: open_folder(website))
        return wbbutton
