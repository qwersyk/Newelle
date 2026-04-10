import os
import subprocess
from ..controller import NewelleController

from ..utility.system import can_escape_sandbox, get_spawn_command

from ..constants import AVAILABLE_EMBEDDINGS, AVAILABLE_LLMS, AVAILABLE_MEMORIES, AVAILABLE_PROMPTS, AVAILABLE_RAGS, AVAILABLE_STT, AVAILABLE_TTS, AVAILABLE_WEBSEARCH, PROMPTS
from .extra_settings import ExtraSettingsBuilder
from .widgets import CopyBox
from ..extensions import ExtensionLoader
from gi.repository import Gtk, Adw, Gio, GLib
from threading import Thread


class Extension(Gtk.Window):
    def __init__(self,app):
        Gtk.Window.__init__(self, title=_("Extensions"))
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')

        self.directory = GLib.get_user_config_dir()
        self.controller : NewelleController = app.win.controller
        self.path = self.controller.extension_path 
        self.pip_directory = self.controller.pip_path 
        self.extension_path = self.controller.extension_path 
        self.extensions_cache = self.controller.extension_path
        self.sandbox = can_escape_sandbox()
                
        self.app = app
        self.set_default_size(500, 500)
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.set_titlebar(Adw.HeaderBar(css_classes=["flat"]))

        self.notification_block = Adw.ToastOverlay()
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.notification_block.set_child(self.scrolled_window)

        self.set_child(self.notification_block)
        self.update()
    
    def update(self):
        self.extensionloader = ExtensionLoader(self.extension_path, pip_path=self.pip_directory, extension_cache=self.extensions_cache, settings=self.settings)
        self.extensionloader.load_extensions()
        self.extensionloader.set_ui_controller(self.controller.ui_controller)
        self.controller.set_extensionsloader(self.extensionloader) 
        self.extra_settings_rows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.extra_settings_rows,
            convert_constants=self._convert_extension_constants,
        )

        self.main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.FILL,halign=Gtk.Align.CENTER,orientation=Gtk.Orientation.VERTICAL)
        self.main.set_size_request(300, -1)
        self.scrolled_window.set_child(self.main)
        self.extensiongroup = Adw.PreferencesGroup(title=_("Installed Extensions"))
        self.main.append(self.extensiongroup)
        for extension in self.extensionloader.get_extensions():
            
            self.extra_settings_rows[(extension.key, "extension", False)] = {}
            extension.set_extra_settings_update(
                lambda _, current_extension=extension: GLib.idle_add(
                    self.extra_settings_builder.on_setting_change,
                    self.extensionloader.extensionsmap,
                    current_extension,
                    current_extension.key,
                    True,
                )
            )
            button = Gtk.Button(css_classes=["flat", "destructive-action"], margin_top=10,margin_start=10,margin_end=10,margin_bottom=10)
            button.connect("clicked", self.delete_extension)
            button.set_name(extension.id)

            icon_name="user-trash-symbolic"
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name=icon_name))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            button.set_child(icon)
            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            switch.connect("notify::state", self.change_status)
            switch.set_name(extension.id) 
            if extension not in self.extensionloader.disabled_extensions:
                switch.set_active(True)
            
            if len(extension.get_extra_settings()) > 0:
                row = Adw.ExpanderRow(title=extension.name)
                row.add_suffix(switch)
                row.add_suffix(button)
                self.extra_settings_builder.add_extra_settings(self.extensionloader.extensionsmap, extension, row)
            else:
                row = Adw.ActionRow(title=extension.name)
                row.add_suffix(button)
                row.add_suffix(switch)
                # Add invisible icon for alignment purposes
                invisible_icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-information-symbolic"))
                invisible_icon.set_opacity(0)
                row.add_suffix(invisible_icon)
            
            self.extra_settings_rows[(extension.key, "extension", False)]["row"] = row
            self.add_flatpak_warning_button(extension, row)
            self.extensiongroup.add(row)                            
        download_button = Gtk.Button(label=_("User guide to Extensions"), margin_top=10)
        download_button.connect("clicked", lambda x : subprocess.Popen(get_spawn_command() + ["xdg-open", "https://github.com/qwersyk/Newelle/wiki/User-guide-to-Extensions"]))
        self.main.append(download_button)
        download_button = Gtk.Button(label=_("Download new Extensions"), margin_top=10)
        download_button.connect("clicked", lambda x : subprocess.Popen(get_spawn_command() + ["xdg-open", "https://github.com/topics/newelle-extension"]))
        self.main.append(download_button)
        folder_button = Gtk.Button(label=_("Install extension from file..."), css_classes=["suggested-action"], margin_top=10)
        folder_button.connect("clicked", self.on_folder_button_clicked)
        self.main.append(folder_button)

    def _convert_extension_constants(self, _constants):
        return "extension"

    def add_flatpak_warning_button(self, handler, row):
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        if handler.requires_sandbox_escape() and not self.sandbox:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="warning-outline-symbolic"))
            actionbutton.connect("clicked", self.show_flatpak_sandbox_notice)
            actionbutton.add_css_class("error")
            actionbutton.set_child(icon)
            if type(row) is Adw.ActionRow:
                row.add_suffix(actionbutton)
            elif type(row) is Adw.ExpanderRow:
                row.add_action(actionbutton)
            elif type(row) is Adw.ComboRow:
                row.add_suffix(actionbutton)

    def show_flatpak_sandbox_notice(self, _el=None):
        dialog = Adw.MessageDialog(
            title="Permission Error",
            modal=True,
            transient_for=self,
            destroy_with_parent=True,
        )
        dialog.set_heading(_("Not enough permissions"))
        dialog.set_body_use_markup(True)
        dialog.set_body(_("Newelle does not have enough permissions to run commands on your system, please run the following command"))
        dialog.add_response("close", _("Understood"))
        dialog.set_default_response("close")
        dialog.set_extra_child(CopyBox("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle", "bash"))
        dialog.set_close_response("close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda current_dialog, _response_id: current_dialog.destroy())
        dialog.present()
    
    def change_status(self,widget,*a):        
        name = widget.get_name()
        if widget.get_active():
            self.extensionloader.enable(name)
        else:
            self.extensionloader.disable(name)
            self.extensionloader.remove_handlers(self.extensionloader.get_extension_by_id(name), AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH)
            self.extensionloader.remove_prompts(self.extensionloader.get_extension_by_id(name), PROMPTS, AVAILABLE_PROMPTS)
            self.extensionloader.remove_tools(self.controller.tools, self.extensionloader.get_extension_by_id(name))
    def delete_extension(self,widget):
        self.extensionloader.remove_extension(widget.get_name())
        self.update()
    
    def on_folder_button_clicked(self, widget):
        filter = Gtk.FileFilter(name="Newelle Extensions", patterns=["*.py"])
        dialog = Gtk.FileDialog(title="Import extension", modal=True, default_filter=filter)
        dialog.open(self, None, self.process_folder)

    def process_folder(self, dialog, result):
        try:
            file=dialog.open_finish(result)
        except Exception as _:
            return
        if file is None:
            return
        file_path = file.get_path()
        self.extensionloader.add_extension(file_path)
        self.extensionloader.load_extensions()

        for extid, filename in self.extensionloader.filemap.items():
            if filename == os.path.basename(file_path):
                ext = self.extensionloader.get_extension_by_id(extid)
                if ext is None:
                    continue
                Thread(target=ext.install).start()
                break
        
        if os.path.basename(file_path) in self.extensionloader.filemap.values():
            self.notification_block.add_toast(Adw.Toast(title="Extension added. New extensions will run"))
            self.extensionloader.load_extensions()
            # Edit extension settings in order to reload on update
            ext = self.extensionloader.get_enabled_extensions()[0] if len(self.extensionloader.get_enabled_extensions()) > 0 else None
            if ext is not None:
                ext.set_setting("reload_requested", ext.get_setting("reload_requested", False, 0) + 1)
            self.update()
        else:
            self.notification_block.add_toast(Adw.Toast(title="This is not an extension or it is not correct"))

        return

