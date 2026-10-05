import os
import subprocess
from threading import Thread

from gi.repository import Adw, Gdk, GLib, Gtk

from ..controller import NewelleController
from ..utility.system import can_escape_sandbox, get_spawn_command
from .extensions_catalog import ExtensionMarketplaceView, install_extension_files
from .extra_settings import ExtraSettingsBuilder
from .widgets import CopyBox


class ExtensionPage(Adw.PreferencesPage):
    """Manage user-installed extensions inside the settings window."""

    def __init__(
        self,
        app,
        controller: NewelleController,
        toast_callback=None,
    ):
        super().__init__(
            icon_name="extension-symbolic",
            title=_("Extensions"),
        )
        self.app = app
        self.controller = controller
        self.settings = controller.settings
        self.toast_callback = toast_callback
        self.extension_path = controller.extension_path
        self.pip_directory = controller.pip_path
        self.extensions_cache = controller.extensions_cache
        self.sandbox = can_escape_sandbox()
        self.page_groups = []
        self.marketplace_initialized = False
        self.installed_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        self.marketplace_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        self.extensions_view_stack = Adw.ViewStack(vhomogeneous=False)
        self.extensions_view_stack.add_titled_with_icon(
            self.installed_page,
            name="installed",
            title=_("Installed"),
            icon_name="extension-symbolic",
        )
        self.extensions_view_stack.add_titled_with_icon(
            self.marketplace_page,
            name="marketplace",
            title=_("Marketplace"),
            icon_name="folder-download-symbolic",
        )
        self.extensions_view_stack.connect(
            "notify::visible-child-name", self._on_extensions_tab_changed
        )
        tabs_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
        )
        tabs_box.append(
            Adw.ViewSwitcher(
                stack=self.extensions_view_stack,
                policy=Adw.ViewSwitcherPolicy.WIDE,
                halign=Gtk.Align.CENTER,
            )
        )
        tabs_box.append(self.extensions_view_stack)
        self.tabs_group = Adw.PreferencesGroup()
        self.tabs_group.add(tabs_box)
        super().add(self.tabs_group)
        self._add_drag_and_drop()
        self.update()

    def _add_toast(self, title):
        if self.toast_callback is not None:
            self.toast_callback(Adw.Toast(title=title))

    def _parent_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _clear_page(self):
        child = self.installed_page.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.installed_page.remove(child)
            child = next_child
        self.page_groups = []

    def _add_group(self, group):
        self.installed_page.append(group)
        self.page_groups.append(group)

    def _on_extensions_tab_changed(self, stack, _pspec):
        if (
            stack.get_visible_child_name() == "marketplace"
            and not self.marketplace_initialized
        ):
            self.marketplace_initialized = True
            warning_group = Adw.PreferencesGroup()
            warning_group.add(
                Adw.ActionRow(
                    title=_("Third-party extensions"),
                    subtitle=_(
                        "Extensions are created by third-party users. Newelle does not review or control their code."
                    ),
                    icon_name="dialog-warning-symbolic",
                )
            )
            self.marketplace_page.append(warning_group)

            marketplace_group = Adw.PreferencesGroup(
                title=_("Extension Marketplace"),
                description=_(
                    "Browse GitHub repositories tagged newelle-extension and choose which files to install"
                ),
            )
            self.marketplace = ExtensionMarketplaceView(
                parent=self,
                controller=self.controller,
                install_callback=lambda files: install_extension_files(
                    files, self.extension_path
                ),
                on_installed=self._on_marketplace_installed,
            )
            marketplace_group.add(self.marketplace)
            self.marketplace_page.append(marketplace_group)

    def _on_marketplace_installed(self, installed_files=None):
        self._reload_user_extensions()
        installed_names = {
            os.path.basename(path) for path in (installed_files or [])
        }
        for extension_id, filename in self.extensionloader.filemap.items():
            if filename not in installed_names:
                continue
            extension = self.extensionloader.get_extension_by_id(extension_id)
            if extension is None:
                continue
            Thread(target=extension.install, daemon=True).start()
        self.update()

    def update(self):
        self._clear_page()
        self.extensionloader = self.controller.extensionloader
        self.extra_settings_rows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.extra_settings_rows,
            convert_constants=self._convert_extension_constants,
        )

        self.extensiongroup = Adw.PreferencesGroup(title=_("Installed Extensions"))
        self._add_group(self.extensiongroup)

        for extension in self.extensionloader.get_extensions():
            self._add_extension_row(extension)

        self._add_extension_actions()

    def _add_extension_row(self, extension):
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

        toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        toggle.set_active(extension not in self.extensionloader.disabled_extensions)
        toggle.connect("state-set", self.change_status, extension.id)

        has_extra_settings = bool(extension.get_extra_settings())
        if has_extra_settings:
            row = Adw.ExpanderRow(title=extension.name)
            row.add_suffix(toggle)
            self.extra_settings_builder.add_extra_settings(
                self.extensionloader.extensionsmap,
                extension,
                row,
            )
        else:
            row = Adw.ActionRow(title=extension.name)
            row.add_suffix(toggle)

        row.add_prefix(
            Gtk.Image(icon_name="extension-symbolic", css_classes=["dim-label"])
        )

        delete_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "destructive-action"],
        )
        delete_button.set_tooltip_text(_("Remove"))
        delete_button.connect("clicked", self.delete_extension, extension.id)
        row.add_suffix(delete_button)

        self.extra_settings_rows[(extension.key, "extension", False)]["row"] = row
        self.add_flatpak_warning_button(extension, row)
        self.extensiongroup.add(row)

    def _add_extension_actions(self):
        actions = Adw.PreferencesGroup()
        self._add_group(actions)

        guide_row = Adw.ActionRow(title=_("User guide to Extensions"))
        guide_button = Gtk.Button(
            icon_name="internet-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        guide_button.connect(
            "clicked",
            lambda _button: subprocess.Popen(
                get_spawn_command()
                + [
                    "xdg-open",
                    "https://github.com/qwersyk/Newelle/wiki/User-guide-to-Extensions",
                ]
            ),
        )
        guide_row.add_suffix(guide_button)
        guide_row.set_activatable_widget(guide_button)
        actions.add(guide_row)

        download_row = Adw.ActionRow(title=_("Download new Extensions"))
        download_button = Gtk.Button(
            icon_name="internet-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        download_button.connect(
            "clicked",
            lambda _button: subprocess.Popen(
                get_spawn_command()
                + ["xdg-open", "https://github.com/topics/newelle-extension"]
            ),
        )
        download_row.add_suffix(download_button)
        download_row.set_activatable_widget(download_button)
        actions.add(download_row)

        install_row = Adw.ActionRow(title=_("Install extension from file..."))
        install_row.set_subtitle(_("You can also drag and drop the file here"))
        install_button = Gtk.Button(
            label=_("Install"),
            valign=Gtk.Align.CENTER,
            css_classes=["suggested-action"],
        )
        install_button.connect("clicked", self.on_folder_button_clicked)
        install_row.add_suffix(install_button)
        install_row.set_activatable_widget(install_button)
        actions.add(install_row)

    def _convert_extension_constants(self, _constants):
        return "extension"

    def add_flatpak_warning_button(self, handler, row):
        if not handler.requires_sandbox_escape() or self.sandbox:
            return

        action_button = Gtk.Button(
            icon_name="warning-outline-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "error"],
        )
        action_button.connect("clicked", self.show_flatpak_sandbox_notice)
        if isinstance(row, Adw.ExpanderRow):
            row.add_action(action_button)
        else:
            row.add_suffix(action_button)

    def show_flatpak_sandbox_notice(self, _button=None):
        dialog = Adw.MessageDialog(
            title=_("Permission Error"),
            modal=True,
            transient_for=self._parent_window(),
            destroy_with_parent=True,
        )
        dialog.set_heading(_("Not enough permissions"))
        dialog.set_body_use_markup(True)
        dialog.set_body(
            _(
                "Newelle does not have enough permissions to run commands on your system, please run the following command"
            )
        )
        dialog.add_response("close", _("Understood"))
        dialog.set_default_response("close")
        dialog.set_extra_child(
            CopyBox(
                "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle",
                "bash",
            )
        )
        dialog.set_close_response("close")
        dialog.set_response_appearance(
            "close",
            Adw.ResponseAppearance.DESTRUCTIVE,
        )
        dialog.connect(
            "response",
            lambda current_dialog, _response_id: current_dialog.destroy(),
        )
        dialog.present()

    def change_status(self, _switch, state, extension_id):
        if state:
            self.extensionloader.enable(extension_id)
        else:
            self.extensionloader.disable(extension_id)
        self._reload_user_extensions({extension_id})
        GLib.idle_add(self.update)
        return False

    def _reload_user_extensions(self, extension_ids=None):
        refreshes = self.controller.reload_extensions(extension_ids)
        self.extensionloader = self.controller.extensionloader
        return refreshes

    def delete_extension(self, _button, extension_id):
        self.extensionloader.remove_extension(extension_id)
        self._reload_user_extensions({extension_id})
        self.update()

    def on_folder_button_clicked(self, _button):
        file_filter = Gtk.FileFilter(
            name="Newelle Extensions",
            patterns=["*.py"],
        )
        dialog = Gtk.FileDialog(
            title=_("Import extension"),
            modal=True,
            default_filter=file_filter,
        )
        dialog.open(self._parent_window(), None, self.process_folder)

    def process_folder(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if file is None:
            return

        self._install_extension_files([file.get_path()])

    def _add_drag_and_drop(self):
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_extension_drop)
        self.add_controller(drop_target)

    def _on_extension_drop(self, _drop_target, value, _x, _y):
        """Install extension files dropped onto the page."""
        file_paths = []
        for file in value.get_files():
            path = file.get_path()
            if path is not None:
                file_paths.append(path)
        if file_paths:
            self._install_extension_files(file_paths)
        return True

    def _install_extension_files(self, file_paths):
        # Files already inside the extension directory are skipped to avoid
        # copying a file onto itself
        extension_dir = os.path.abspath(self.extension_path)
        candidates = [
            path
            for path in file_paths
            if path is not None
            and path.endswith(".py")
            and os.path.isfile(path)
            and os.path.dirname(os.path.abspath(path)) != extension_dir
        ]
        if not candidates:
            self._add_toast(_("This is not an extension or it is not correct"))
            return

        for path in candidates:
            self.extensionloader.add_extension(path)
        self._reload_user_extensions()

        filenames = {os.path.basename(path) for path in candidates}
        added_extensions = []
        for extension_id, extension_filename in self.extensionloader.filemap.items():
            if extension_filename not in filenames:
                continue
            extension = self.extensionloader.get_extension_by_id(extension_id)
            if extension is not None:
                added_extensions.append(extension)

        if not added_extensions:
            self._add_toast(_("This is not an extension or it is not correct"))
            self.update()
            return

        for added_extension in added_extensions:
            Thread(target=added_extension.install, daemon=True).start()
        self._add_toast(_("Extension added. New extensions will run"))
        self.update()


class Extension(Adw.Window):
    """Compatibility wrapper for callers that still open extensions directly."""

    def __init__(self, app):
        super().__init__(
            title=_("Extensions"),
            default_width=600,
            default_height=600,
            transient_for=app.win,
            modal=True,
        )
        overlay = Adw.ToastOverlay()
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        page = ExtensionPage(
            app,
            app.win.controller,
            toast_callback=overlay.add_toast,
        )
        toolbar_view.set_content(page)
        overlay.set_child(toolbar_view)
        self.set_content(overlay)
