"""One installation window for the local C++ backends."""

import threading
import traceback
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, Gtk

from ...utility.build_process import BuildCancelled
from ...utility.download_manager import DownloadCancelled
from ...utility.runtime_installer import get_runtime_installer
from ...utility.system import can_escape_sandbox
from ..build_dependency_warning import BuildDependencyWarning

SANDBOX_COMMAND = (
    "flatpak --user override --talk-name=org.freedesktop.Flatpak "
    "--filesystem=home io.github.qwersyk.Newelle"
)


def show_runtime_installer(
    handler, project, path, button=None, *, on_installed=None, selected_version=None
):
    """Present the handler's existing installer, or create a fresh one.

    ``project`` supplies build options, labels and discovery callbacks.
    ``on_installed(backend, version, prebuilt)`` runs on the GTK main loop so
    handlers can persist their own settings before the settings rows refresh.
    ``selected_version`` supplies the initial revision without prescribing a
    handler settings key.
    """
    window = getattr(handler, "_runtime_install_window", None)
    if window is None:
        window = RuntimeInstallerWindow(
            handler,
            project,
            path,
            on_installed=on_installed,
            selected_version=selected_version,
        )
        handler._runtime_install_window = window
    root = button.get_root() if button is not None else None
    if isinstance(root, Gtk.Window):
        window.set_transient_for(root)
    window.present()


class RuntimeInstallerWindow(Adw.Window):
    def __init__(
        self, handler, project, path, *, on_installed=None, selected_version=None
    ):
        super().__init__(
            title=_("Install {name}").format(name=project.name), modal=True
        )
        self.set_default_size(700, 760)
        self.handler = handler
        self.on_installed = on_installed
        self.selected_version = selected_version
        self.project = project
        self.installer = get_runtime_installer(project, path)
        self._running = False
        self._closed = False
        self._fetching = False
        self._assets = None
        self._selected_asset = None
        self._cancel_event = None
        self._versions = list(project.versions)
        self._has_versions = bool(
            self._versions or project.fetch_versions or selected_version
        )
        self.connect("close-request", self._on_close)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(Adw.HeaderBar())
        self.pages = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            vexpand=True,
            hexpand=True,
        )
        content.append(self.pages)
        self.set_content(content)

        if project.fetch_releases:
            self._add_method_page()
            self._add_prebuilt_page()
        self._add_hardware_page()
        self._add_progress_page()
        self._add_done_page()
        self._show_page("method" if project.fetch_releases else "hardware")

        if project.fetch_versions:
            threading.Thread(target=self._fetch_versions, daemon=True).start()

    @staticmethod
    def _box(spacing=12):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(24)
        return box

    @staticmethod
    def _button(box, label, callback, style=None):
        button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
        if style:
            button.add_css_class(style)
        button.connect("clicked", callback)
        box.append(button)
        return button

    def _status_page(self, name, title, description, icon):
        page = Adw.StatusPage(title=title, description=description, icon_name=icon)
        box = self._box()
        page.set_child(box)
        self.pages.add_named(page, name)
        return box

    def _show_page(self, name):
        self.pages.set_visible_child_name(name)

    def _add_method_page(self):
        box = self._status_page(
            "method",
            _("Choose Installation Method"),
            _("How would you like to install {name}?").format(name=self.project.name),
            "system-software-install-symbolic",
        )
        cards = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=20, homogeneous=True
        )
        for title, description, label, callback in (
            (
                _("Compile from Source"),
                _(
                    "Optimized for your hardware. Requires build tools and may take several minutes."
                ),
                _("Compile"),
                lambda _button: self._show_page("hardware"),
            ),
            (
                _("Download Pre-built"),
                _("Install a ready-to-use binary for your hardware."),
                _("Download"),
                self._show_prebuilt,
            ),
        ):
            card = self._box()
            card.add_css_class("card")
            heading = Gtk.Label(label=title)
            heading.add_css_class("title-4")
            card.append(heading)
            card.append(Gtk.Label(label=description, wrap=True, max_width_chars=26))
            self._button(card, label, callback, "suggested-action")
            cards.append(card)
        box.append(cards)
        self._button(box, _("Cancel"), lambda _button: self.close())

    def _add_hardware_page(self):
        box = self._status_page(
            "hardware",
            _("Select Hardware"),
            _("Choose your acceleration backend"),
            "tools-symbolic",
        )
        can_build = can_escape_sandbox()
        if not can_build:
            warning = Gtk.Label(
                label=_(
                    "To build {name} in Flatpak, grant sandbox escape permissions with this command:"
                ).format(name=self.project.name),
                wrap=True,
            )
            warning.add_css_class("warning")
            box.append(warning)
            box.append(Gtk.Entry(text=SANDBOX_COMMAND, editable=False))
            self._button(box, _("Copy Command"), self._copy_command)

        dependency_warning = BuildDependencyWarning()
        box.append(dependency_warning)
        options = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=24, halign=Gtk.Align.CENTER
        )
        hardware = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.hardware_options = {}
        group = None
        for backend in self.project.backend_flags:
            option = Gtk.CheckButton(
                label=self.project.backend_labels.get(backend, backend)
            )
            if group is None:
                group = option
                option.set_active(True)
            else:
                option.set_group(group)
            dependency_warning.watch_option(option, backend)
            self.hardware_options[backend] = option
            hardware.append(option)
        if self.hardware_options:
            dependency_warning.refresh(next(iter(self.hardware_options)))
        options.append(hardware)

        settings = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER
        )
        if self._has_versions:
            settings.append(Gtk.Label(label=_("Version"), xalign=0))
            installed = self.selected_version or next(iter(self._versions), None)
            if installed and installed not in self._versions:
                self._versions.insert(0, installed)
            self.version_dropdown = Gtk.DropDown()
            self.custom_version = Gtk.Entry(
                placeholder_text=_("Tag, branch or commit"), visible=False
            )
            self.version_dropdown.connect("notify::selected", self._version_changed)
            self._set_versions(self._versions, installed)
            settings.append(self.version_dropdown)
            settings.append(self.custom_version)
        settings.append(Gtk.Label(label=_("Custom CMake Flags (Optional)"), xalign=0))
        self.custom_flags = Gtk.Entry(placeholder_text="-DCMAKE_BUILD_TYPE=Release ...")
        settings.append(self.custom_flags)
        options.append(settings)
        box.append(options)
        start = self._button(
            box, _("Start Build"), self._start_build, "suggested-action"
        )
        start.set_sensitive(can_build)
        if not can_build:
            start.set_tooltip_text(_("Please run the Flatpak override command first"))
        if self.project.fetch_releases:
            self._button(box, _("Back"), lambda _button: self._show_page("method"))
        self._button(box, _("Cancel"), lambda _button: self.close())

    def _copy_command(self, _button):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(SANDBOX_COMMAND)

    def _version_changed(self, _dropdown, _pspec):
        self.custom_version.set_visible(
            self.version_dropdown.get_selected() == len(self._versions)
        )

    def _selected_version(self):
        if not self._has_versions:
            return None
        index = self.version_dropdown.get_selected()
        if index < len(self._versions):
            return self._versions[index]
        return self.custom_version.get_text().strip() or next(
            iter(self._versions), None
        )

    def _set_versions(self, versions, selected):
        self._versions = list(versions)
        if selected and selected not in self._versions:
            self._versions.insert(0, selected)
        self.version_dropdown.set_model(
            Gtk.StringList.new(self._versions + [_("Custom...")])
        )
        self.version_dropdown.set_selected(
            self._versions.index(selected) if selected in self._versions else 0
        )
        self._version_changed(self.version_dropdown, None)

    def _fetch_versions(self):
        try:
            versions = self.installer.fetch_versions()
        except Exception as error:  # noqa: BLE001 - keep fallback versions on discovery failure
            # The bundled versions and custom revision entry remain usable.
            print(f"Failed to fetch {self.project.name} versions: {error}")
            return
        GLib.idle_add(self._update_versions, versions)

    def _update_versions(self, versions):
        if not self._closed and not self._running:
            self._set_versions(versions, self._selected_version())
        return False

    def _add_prebuilt_page(self):
        box = self._status_page(
            "prebuilt",
            _("Select Pre-built Binary"),
            _("Choose the binary that matches your hardware"),
            "folder-download-symbolic",
        )
        self.prebuilt_spinner = Gtk.Spinner(halign=Gtk.Align.CENTER)
        box.append(self.prebuilt_spinner)
        self.prebuilt_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.prebuilt_list.add_css_class("boxed-list")
        box.append(self.prebuilt_list)
        self.prebuilt_error = Gtk.Label(wrap=True)
        box.append(self.prebuilt_error)
        self.download_button = self._button(
            box, _("Download & Install"), self._start_download, "suggested-action"
        )
        self.download_button.set_sensitive(False)
        self._button(box, _("Back"), lambda _button: self._show_page("method"))

    def _show_prebuilt(self, _button):
        self._show_page("prebuilt")
        if self._assets is None and not self._fetching:
            self._fetching = True
            self.prebuilt_spinner.set_visible(True)
            self.prebuilt_spinner.start()
            self.prebuilt_error.set_text("")
            threading.Thread(target=self._fetch_releases, daemon=True).start()

    def _fetch_releases(self):
        try:
            assets = self.installer.fetch_releases()
            error = (
                ""
                if assets
                else _("No compatible pre-built binaries found for your architecture.")
            )
        except Exception as exception:  # noqa: BLE001 - report worker failures in the window
            assets = []
            error = _("Failed to fetch releases: {error}").format(error=exception)
        GLib.idle_add(self._populate_releases, assets, error)

    def _populate_releases(self, assets, error):
        self._fetching = False
        if self._closed:
            return False
        self.prebuilt_spinner.stop()
        self.prebuilt_spinner.set_visible(False)
        self.prebuilt_error.set_text(error)
        if error:
            return False
        self._assets = assets
        group = None
        for index, asset in enumerate(assets):
            title = asset.get(
                "title",
                self.project.backend_labels.get(asset["backend"], asset["backend"]),
            )
            subtitle_parts = [asset["tag"], f"{asset['size'] / (1024 * 1024):.1f} MB"]
            if asset.get("source"):
                subtitle_parts.append(asset["source"])
            subtitle = "  |  ".join(subtitle_parts)
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            if asset.get("recommended") or asset.get("best_match"):
                recommended = Gtk.Label(
                    label=_("Best Match")
                    if asset.get("best_match")
                    else _("Recommended")
                )
                recommended.add_css_class("success")
                row.add_suffix(recommended)
            option = Gtk.CheckButton()
            if group is None:
                group = option
            else:
                option.set_group(group)
            option.connect("toggled", self._select_asset, asset)
            row.add_prefix(option)
            row.set_activatable_widget(option)
            self.prebuilt_list.append(row)
            if index == 0:
                option.set_active(True)
        self.download_button.set_sensitive(bool(assets))
        return False

    def _select_asset(self, option, asset):
        if option.get_active():
            self._selected_asset = asset

    def _add_progress_page(self):
        box = self._box()
        title = Gtk.Label(label=_("Installing"))
        title.add_css_class("title-1")
        box.append(title)
        self.progress_bar = Gtk.ProgressBar()
        box.append(self.progress_bar)
        self.log_view = Gtk.TextView(editable=False, monospace=True)
        box.append(Gtk.ScrolledWindow(child=self.log_view, hexpand=True, vexpand=True))
        self.stop_button = self._button(
            box, _("Cancel"), self._cancel, "destructive-action"
        )
        self.back_button = self._button(box, _("Back"), self._back_after_failure)
        self.back_button.set_visible(False)
        self.pages.add_named(box, "progress")

    def _add_done_page(self):
        box = self._status_page(
            "done",
            _("Completed"),
            _("Installation finished successfully"),
            "emblem-default-symbolic",
        )
        self._button(box, _("Close"), lambda _button: self.close())

    def _start_build(self, _button):
        backend = next(
            (
                key
                for key, option in self.hardware_options.items()
                if option.get_active()
            ),
            None,
        )
        self._start(backend, self.custom_flags.get_text(), self._selected_version())

    def _start_download(self, _button):
        if self._selected_asset is not None:
            self._start(self._selected_asset["backend"], asset=self._selected_asset)

    def _start(self, backend, flags="", version=None, asset=None):
        if self._running:
            return
        self._running = True
        self._cancel_event = threading.Event()
        self.progress_bar.set_fraction(0)
        self.log_view.get_buffer().set_text("")
        self.stop_button.set_visible(True)
        self.stop_button.set_sensitive(True)
        self.back_button.set_visible(False)
        self._show_page("progress")
        threading.Thread(
            target=self._install, args=(backend, flags, version, asset), daemon=True
        ).start()

    def _install(self, backend, flags, version, asset):
        try:
            callbacks = {
                "log": lambda text: GLib.idle_add(self._append_log, text),
                "progress": lambda value: GLib.idle_add(self._set_progress, value),
                "cancel_event": self._cancel_event,
            }
            if asset is None:
                self.installer.build(backend, flags, version, **callbacks)
            else:
                self.installer.install_prebuilt(
                    asset, f"runtime:{self.handler.key}:{asset['name']}", **callbacks
                )
        except (BuildCancelled, DownloadCancelled):
            GLib.idle_add(self._failed, _("Installation cancelled"))
        except Exception as error:  # noqa: BLE001 - report worker failures in the window
            GLib.idle_add(self._failed, str(error) + "\n" + traceback.format_exc())
        else:
            GLib.idle_add(self._completed, backend, version, asset is not None)

    def _append_log(self, text):
        if not self._closed:
            buffer = self.log_view.get_buffer()
            buffer.insert(buffer.get_end_iter(), text)
        return False

    def _set_progress(self, value):
        if not self._closed:
            self.progress_bar.set_fraction(value)
        return False

    def _completed(self, backend, version, prebuilt):
        self._running = False
        # Persist all settings before rebuilding settings rows on the main loop.
        if self.on_installed is not None:
            self.on_installed(backend, version, prebuilt)
        self.handler._is_installed_cache = None
        self.handler.settings_update()
        if not self._closed:
            self._show_page("done")
        return False

    def _failed(self, message):
        self._running = False
        self._append_log("\n" + message + "\n")
        if not self._closed:
            self.stop_button.set_visible(False)
            self.back_button.set_visible(True)
        return False

    def _back_after_failure(self, _button):
        self._show_page("method" if self.project.fetch_releases else "hardware")

    def _cancel(self, _button):
        if self._running:
            self.installer.cancel(self._cancel_event)
            self.stop_button.set_sensitive(False)

    def _on_close(self, _window):
        if self._running:
            self.installer.cancel(self._cancel_event)
        self._closed = True
        self.handler._runtime_install_window = None
        return False
