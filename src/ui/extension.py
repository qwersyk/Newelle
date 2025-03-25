import subprocess
from threading import Thread
import os

from ..controller import NewelleController

from ..utility.system import get_spawn_command

from ..constants import AVAILABLE_EMBEDDINGS, AVAILABLE_LLMS, AVAILABLE_MEMORIES, AVAILABLE_PROMPTS, AVAILABLE_RAGS, AVAILABLE_STT, AVAILABLE_TTS, PROMPTS
from .settings import Settings
from ..extensions import ExtensionLoader
from gi.repository import Gtk, Adw, Gio, GLib


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
        self.controller.set_extensionsloader(self.extensionloader) 
        settings = Settings(self.app, self.controller, headless=True)

        self.main = Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,valign=Gtk.Align.FILL,halign=Gtk.Align.CENTER,orientation=Gtk.Orientation.VERTICAL)
        self.main.set_size_request(300, -1)
        self.scrolled_window.set_child(self.main)
        self.extensiongroup = Adw.PreferencesGroup(title=_("Extensions"))
        self.main.append(self.extensiongroup)
        for extension in self.extensionloader.get_extensions():
            
            settings.settingsrows[(extension.key, "extension", False)]= {} 
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
                settings.add_extra_settings(settings.extensionloader.extensionsmap, extension, row) 
            else:
                row = Adw.ActionRow(title=extension.name)
                row.add_suffix(button)
                row.add_suffix(switch)
                # Add invisible icon for alignment purposes
                invisible_icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-information-symbolic"))
                invisible_icon.set_opacity(0)
                row.add_suffix(invisible_icon)
            
            settings.add_flatpak_waning_button(extension, row)
            self.extensiongroup.add(row)                            
        download_button = Gtk.Button(label=_("User guide to Extensions"), margin_top=10)
        download_button.connect("clicked", lambda x : subprocess.Popen(get_spawn_command() + ["xdg-open", "https://github.com/qwersyk/Newelle/wiki/User-guide-to-Extensions"]))
        self.main.append(download_button)
        download_button = Gtk.Button(label=_("Download new Extensions"), margin_top=10)
        download_button.connect("clicked", lambda x : subprocess.Popen(get_spawn_command() + ["xdg-open", "https://github.com/topics/newelle-extension"]))
        self.main.append(download_button)
        folder_button = Gtk.Button(label=_("Choose an extension"), css_classes=["suggested-action"], margin_top=10)
        folder_button.connect("clicked", self.on_folder_button_clicked)
        self.main.append(folder_button)
    
    def change_status(self,widget,*a):        
        name = widget.get_name()
        if widget.get_active():
            self.extensionloader.enable(name)
        else:
            self.extensionloader.disable(name)
            self.extensionloader.remove_handlers(self.extensionloader.get_extension_by_id(name), AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAGS)
            self.extensionloader.remove_prompts(self.extensionloader.get_extension_by_id(name), PROMPTS, AVAILABLE_PROMPTS)
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
            self.update()
        else:
            self.notification_block.add_toast(Adw.Toast(title="This is not an extension or it is not correct"))

        return

