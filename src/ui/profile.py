
from pydoc import describe
import shutil
import os
import json
from ..constants import SETTINGS_GROUPS
from ..utility.profile_settings import ROOT_PROFILE, get_profile_descendants, get_profile_groups, get_profile_parent
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

        self.parent_names = []
        self.parent_row = None
        if not (editing and self.original_name == ROOT_PROFILE):
            self.build_parent_row(editing)
        self.settings_row = Adw.ExpanderRow(
            title=_("Overridden Settings"),
            subtitle=_("Selected groups use this profile's own values"),
        )
        self.build_settings_group(editing)
        self.settings_group.add(self.settings_row)
        self._update_inheritance_description()


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

            if len(self.profile_settings) > 1:
                self.transfer_group = Adw.PreferencesGroup(
                    title=_("Transfer Settings"),
                    description=_("Copy settings groups from another profile into this one"),
                )
                self.page.add(self.transfer_group)
                self.transfer_button = Gtk.Button(label=_("Copy Settings From…"))
                self.transfer_button.connect("clicked", self.open_transfer_dialog)
                self.transfer_group.add(self.transfer_button)
       
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
            warning = Gtk.Label(label=_("Overridden settings start as a copy of the base profile"), wrap=True)
            g.add(warning)
            self.page.add(g)

    def build_parent_row(self, edit=False):
        """Add the "Based on" selector, hiding choices that would create a loop."""
        excluded = set()
        if edit:
            excluded = {self.original_name} | get_profile_descendants(self.profile_settings, self.original_name)
            selected = get_profile_parent(self.profile_settings, self.original_name) or ROOT_PROFILE
        else:
            selected = self.parent.current_profile
        self.parent_names = [name for name in self.profile_settings if name not in excluded]
        if ROOT_PROFILE in self.parent_names:
            self.parent_names.remove(ROOT_PROFILE)
            self.parent_names.insert(0, ROOT_PROFILE)
        self.parent_row = Adw.ComboRow(
            title=_("Based on"),
            subtitle=_("Settings that are not overridden come from this profile"),
            model=Gtk.StringList.new(self.parent_names),
        )
        if selected in self.parent_names:
            self.parent_row.set_selected(self.parent_names.index(selected))
        self.parent_row.connect("notify::selected", lambda *_args: self._update_inheritance_description())
        self.settings_group.add(self.parent_row)

    def get_selected_parent(self):
        if self.parent_row is None or not self.parent_names:
            return None
        index = self.parent_row.get_selected()
        if index >= len(self.parent_names):
            return ROOT_PROFILE
        return self.parent_names[index]

    def _update_inheritance_description(self):
        parent = self.get_selected_parent()
        if parent is None:
            self.settings_group.set_description(_("This is the base profile: it stores every setting."))
            self.settings_row.set_sensitive(False)
            return
        self.settings_group.set_description(
            _("Other settings follow “{0}”. Changing them while this profile is active also changes “{0}”.").format(parent)
        )


    def open_transfer_dialog(self, _button):
        # The overridden groups shown here would be stale after a transfer.
        self.close()
        self.parent.show_transfer_profile_settings(target=self.original_name)

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
        self._updating_settings_switches = False

        self.select_all_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.select_all_row = Adw.ActionRow(
            title=_("All Settings"),
            subtitle=_("Override or inherit every settings group"),
            vexpand=False,
        )
        self.select_all_row.add_suffix(self.select_all_switch)
        self.select_all_row.set_activatable_widget(self.select_all_switch)
        self.settings_row.add_row(self.select_all_row)

        # New profiles start independent so editing them never changes the base by surprise.
        owned = get_profile_groups(self.profile_settings, self.profile_name, SETTINGS_GROUPS) if edit else list(SETTINGS_GROUPS)
        for setting, group in SETTINGS_GROUPS.items():
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
            toggle.set_active(setting in owned)
            toggle.connect("notify::active", self.on_setting_switch_toggled)
            row = Adw.ActionRow(title=group["title"], subtitle=group["description"], vexpand=False)
            row.add_suffix(toggle)
            self.settings_row.add_row(row)
            self.settings_switches[setting] = toggle

        self.select_all_switch.connect("notify::active", self.on_toggle_all_settings)
        self._sync_select_all_switch()

    def on_toggle_all_settings(self, switch, _pspec):
        if self._updating_settings_switches:
            return
        self._updating_settings_switches = True
        active = switch.get_active()
        for toggle in self.settings_switches.values():
            toggle.set_active(active)
        self._updating_settings_switches = False

    def on_setting_switch_toggled(self, switch, _pspec):
        if self._updating_settings_switches:
            return
        self._sync_select_all_switch()

    def _sync_select_all_switch(self):
        all_active = all(toggle.get_active() for toggle in self.settings_switches.values()) if self.settings_switches else False
        self._updating_settings_switches = True
        self.select_all_switch.set_active(all_active)
        self._updating_settings_switches = False

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

        if not self.profile_name:
            self.add_toast(Adw.Toast.new(_("Please enter a profile name.")))
            return
        renamed = not self.editing or self.original_name != self.profile_name
        if renamed and self.profile_name in self.profile_settings:
            self.add_toast(Adw.Toast.new(_("A profile with this name already exists.")))
            return

        picture = self.profile_settings[self.original_name].get("picture") if self.editing else None
        if self.pic_path is not None:
            path = os.path.join(self.parent.path, "profiles")
            if not os.path.exists(path):
                os.makedirs(path)
            shutil.copy(self.pic_path, os.path.join(path, self.profile_name + ".png"))
            picture = os.path.join(path, self.profile_name + ".png")

        overridden = [setting for setting in self.settings_switches if self.settings_switches[setting].get_active()]
        parent = self.get_selected_parent()
        if self.editing:
            if not self.parent.apply_profile_edit(self.original_name, self.profile_name, picture, overridden, parent):
                return
        else:
            self.parent.create_profile(self.profile_name, picture, None, overridden, parent or ROOT_PROFILE)
            GLib.idle_add(self.parent.switch_profile, self.profile_name)
        self.close()


class TransferSettingsDialog(Adw.PreferencesDialog):
    """Copy settings groups from one existing profile to another."""

    def __init__(self, parent, profile_settings, source=None, target=None):
        super().__init__()
        self.parent = parent
        self.profile_settings = profile_settings
        self.profile_names = list(profile_settings)
        self.set_title(_("Transfer Settings"))
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        self.add(page)

        profiles_group = Adw.PreferencesGroup(
            description=_("Copied groups become overridden in the target profile, so its base profile is not changed. Profiles that inherit these groups from the target also get the new values."),
        )
        page.add(profiles_group)
        if target not in self.profile_names:
            target = parent.current_profile
        if source not in self.profile_names or source == target:
            source = next((name for name in self.profile_names if name != target), target)
        self.source_row = Adw.ComboRow(title=_("From"), model=Gtk.StringList.new(self.profile_names))
        self.source_row.set_selected(self.profile_names.index(source))
        self.target_row = Adw.ComboRow(title=_("To"), model=Gtk.StringList.new(self.profile_names))
        self.target_row.set_selected(self.profile_names.index(target))
        profiles_group.add(self.source_row)
        profiles_group.add(self.target_row)

        groups_group = Adw.PreferencesGroup(title=_("Settings to Copy"))
        page.add(groups_group)
        self.group_switches = {}
        for key, group in SETTINGS_GROUPS.items():
            row = Adw.SwitchRow(title=group["title"], subtitle=group["description"])
            row.connect("notify::active", self._update_state)
            groups_group.add(row)
            self.group_switches[key] = row

        button_group = Adw.PreferencesGroup()
        page.add(button_group)
        self.transfer_button = Gtk.Button(label=_("Copy Settings"))
        self.transfer_button.add_css_class("suggested-action")
        self.transfer_button.connect("clicked", self.on_transfer_clicked)
        button_group.add(self.transfer_button)

        self.source_row.connect("notify::selected", self._update_state)
        self.target_row.connect("notify::selected", self._update_state)
        self._update_state()

    def _selected_name(self, row):
        index = row.get_selected()
        return self.profile_names[index] if index < len(self.profile_names) else None

    def _selected_groups(self):
        return [key for key, row in self.group_switches.items() if row.get_active()]

    def _update_state(self, *_args):
        source = self._selected_name(self.source_row)
        target = self._selected_name(self.target_row)
        self.transfer_button.set_sensitive(
            source is not None and target is not None and source != target and bool(self._selected_groups())
        )

    def on_transfer_clicked(self, _button):
        source = self._selected_name(self.source_row)
        target = self._selected_name(self.target_row)
        groups = self._selected_groups()
        if self.parent.transfer_profile_settings(source, target, groups):
            self.parent.workspace_toast(
                _("Copied {0} settings group(s) from “{1}” to “{2}”.").format(len(groups), source, target)
            )
            self.close()
