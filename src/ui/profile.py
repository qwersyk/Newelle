
from pydoc import describe
import shutil
import os
import json
from ..constants import SETTINGS_GROUPS
from gi.repository import Gdk, Gtk, Adw, Gio, GLib

class ProfileDialog(Adw.PreferencesDialog):
    def __init__(self, parent, profile_settings, profile_name=None):
        super().__init__()
        self.pic_path = None
        self.profile_settings = profile_settings
        self.parent = parent
        
        editing = False
        if profile_name is None:
            self.profile_name = "Assistant " + str(len(self.profile_settings) + 1)
        else:
            editing = True
            self.original_name = profile_name
            self.profile_name = profile_name

        self.editing = editing
        self.set_search_enabled(False)
        self.page = Adw.PreferencesPage()
        self.add(self.page)
        
        self.avatar_group = Adw.PreferencesGroup()
        self.page.add(self.avatar_group)
        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)
        self.settings_group = Adw.PreferencesGroup(title=_("Settings"))
        self.page.add(self.settings_group) 
        self.button_group = Adw.PreferencesGroup()
        self.page.add(self.button_group)
        
        # Avatar
        self.avatar = Adw.Avatar(
            text=self.profile_name,
            show_initials=True,
            size=70,
        )
        self.avatar.set_margin_bottom(24)

        # Make avatar clickable
        click_recognizer = Gtk.GestureClick()
        click_recognizer.connect("pressed", self.on_avatar_clicked)
        self.avatar.add_controller(click_recognizer)

        self.avatar_group.add(self.avatar)

        row = Adw.EntryRow(title=_("Profile Name"), text=self.profile_name)
        row.connect("changed", self.on_profile_name_changed)
        self.entry = row
        self.group.add(row)

        self.settings_row = Adw.ExpanderRow(title=_("Copied Settings"), subtitle=_("Settings that will be copied to the new profile"))
        self.build_settings_group(editing)
        self.settings_group.add(self.settings_row)


        # File Filter for image selection
        self.image_filter = Gtk.FileFilter()
        self.image_filter.set_name("Images")
        self.image_filter.add_mime_type("image/*")
        
        if not editing:
            # Creating a profile
            self.set_title(_("Create Profile"))
            image = None
            self.import_group = Adw.PreferencesGroup(title=_("Import Profile"))
            self.page.add(self.import_group)
            self.import_button = Gtk.Button(label=_("Import Profile"))
            self.import_button.connect("clicked", self.import_profile)
            self.import_group.add(self.import_button)
        else:
            # Editing a profile
            self.set_title(_("Edit Profile"))
            self.profile_name = profile_name
            image = self.profile_settings[self.profile_name]["picture"]
            editing = True

            self.export_group = Adw.PreferencesGroup(title=_("Export Profile"))
            self.page.add(self.export_group)
            
            self.password_row = Adw.ActionRow(title=_("Export Passwords"), subtitle=_("Also export password-like fields"))
            self.export_password = Gtk.Switch(active=False, valign=Gtk.Align.CENTER)
            self.propic_row = Adw.ActionRow(title=_("Export Propic"), subtitle=_("Also export the profile picture"))
            self.export_propic = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)

            self.propic_row.add_suffix(self.export_propic)
            self.password_row.add_suffix(self.export_password)
            self.export_group.add(self.password_row)
            self.export_group.add(self.propic_row)

            self.export_button_group = Adw.PreferencesGroup()
            self.page.add(self.export_button_group)
            self.export_button = Gtk.Button(label=_("Export Profile"))
            self.export_button.connect("clicked", self.export_profile)
            self.export_button_group.add(self.export_button)
       
        if image is not None:
            texture = Gdk.Texture.new_from_filename(image)
            self.avatar.set_show_initials(False)
            self.avatar.set_custom_image(texture)
            self.avatar.get_last_child().get_last_child().set_icon_size(Gtk.IconSize.LARGE)
        # Create Button
        self.create_button = Gtk.Button(label=_("Create") if not editing else _("Apply"))
        self.create_button.add_css_class("suggested-action")
        self.create_button.connect("clicked", self.on_create_clicked)
        self.button_group.add(self.create_button)

        if not editing:
            g = Adw.PreferencesGroup()
            warning = Gtk.Label(label=_("The settings of the current profile will be copied into the new one"), wrap=True)
            g.add(warning)
            self.page.add(g)


    def export_profile(self, button):
        filter = Gtk.FileFilter(name=_("Newelle Profiles"), patterns=["*.np", "*.json"]) 
        dialog = Gtk.FileDialog(accept_label=_("Export"), title=_("Export Profile"), default_filter=filter)
        dialog.set_initial_name(self.original_name + ".np")
        dialog.save(self.parent, None, self.export_profile_file)

    def import_profile(self, button):
        filter = Gtk.FileFilter(name=_("Newelle Profiles"), patterns=["*.np", "*.json"])
        dialog = Gtk.FileDialog(accept_label=_("Import"), title=_("Import Profile"), default_filter=filter)
        dialog.open(self.parent, None, self.import_profile_file)

    def import_profile_file(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except Exception as e:
            print(e)
            return

        if file is not None:
            path = file.get_path()
            js = json.loads(open(path).read())
            self.parent.controller.import_profile(js)
            self.parent.reload_profiles()
            self.close()
            

    def export_profile_file(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except Exception as e:
            print(e)
            return

        if file is not None:
            path = file.get_path()
            with open(path, "w") as f:
                
                json.dump(self.parent.controller.export_profile(self.original_name, self.export_password.get_active(), self.export_propic.get_active()), f)
        
    def build_settings_group(self, edit=False):
        self.settings_switches = {}
        for setting, group in SETTINGS_GROUPS.items():
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
            if edit:
                toggle.set_active(setting in self.profile_settings[self.profile_name].get("settings_groups",[]))     
            else:
                toggle.set_active(True)
            row = Adw.ActionRow(title=group["title"], subtitle=group["description"], vexpand=False)
            row.add_suffix(toggle)
            self.settings_row.add_row(row)
            self.settings_switches[setting] = toggle

    def on_profile_name_changed(self, entry):
        """Updates the avatar text when the profile name changes."""
        if len(entry.get_text()) > 30:
            self.create_button.grab_focus()
            entry.set_text(entry.get_text()[:30])
            return
        profile_name = entry.get_text()
        self.profile_name = profile_name
        if profile_name:
            self.avatar.set_text(profile_name)
        else:
            self.avatar.set_text(
                "Assistant " + str(len(self.profile_settings) + 1)
            )

    def on_avatar_clicked(self, gesture, n_press, x, y):
        """Opens the file chooser when the avatar is clicked."""
        # File Chooser
        filters = Gio.ListStore.new(Gtk.FileFilter)

        image_filter = Gtk.FileFilter(name="Images", patterns=["*.png", "*.jpg", "*.jpeg", "*.webp"])

        filters.append(image_filter)

        dialog = Gtk.FileDialog(title=_("Set profile picture"),
                                modal=True,
                                default_filter=image_filter,
                                filters=filters)
        dialog.open(self.parent, None, self.on_file_chosen)

    def on_file_chosen(self, dialog, result):
        """Handles the selected file from the file chooser."""
        
        try:
            file = dialog.open_finish(result)
        except Exception as _:
            return
        if file is None:
            return
        file_path = file.get_path()
        self.pic_path = file_path
        texture = Gdk.Texture.new_from_file(file)
        self.avatar.set_custom_image(
           texture 
        )
        self.avatar.set_show_initials(False)
        # Gotta do this to make it smaller
        self.avatar.get_last_child().get_last_child().set_icon_size(Gtk.IconSize.NORMAL)

    def on_create_clicked(self, button):
        """Handles the create button click."""

        if self.pic_path is not None:
            path = os.path.join(self.parent.path, "profiles")
            if not os.path.exists(path):
                os.makedirs(path)
            shutil.copy(self.pic_path, os.path.join(path, self.profile_name + ".png"))
            self.pic_path = os.path.join(path, self.profile_name + ".png")
        if not self.profile_name:
            toast = Adw.Toast.new("Please enter a profile name.")
            self.parent.add_toast(toast)
            return
        
        # Get the custom image from the avatar (if any)
        copied_settings = [setting for setting in self.settings_switches if self.settings_switches[setting].get_active()] 
        if self.editing:
            self.parent.switch_profile(self.original_name)
        self.parent.create_profile(self.profile_name, self.pic_path, {}, copied_settings)
        GLib.idle_add(self.parent.switch_profile, self.profile_name)
        if self.editing and self.original_name != self.profile_name:
            GLib.idle_add(self.parent.delete_profile, self.original_name)
        self.close()
