"""Reusable warning shown before compiling local C++ backends."""

from gettext import gettext as _

from gi.repository import Gdk, Gtk

from ..utility.build_dependencies import (
    detect_linux_distribution,
    find_missing_build_dependencies,
    suggest_install_command,
)
from ..utility.system import can_escape_sandbox


class BuildDependencyWarning(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("warning")
        self.set_margin_bottom(16)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self._cache = {}
        self._can_inspect_host = can_escape_sandbox()

        title = Gtk.Label(label=_("Missing Build Dependencies"))
        title.add_css_class("heading")
        title.set_halign(Gtk.Align.CENTER)
        self.append(title)

        self.description = Gtk.Label()
        self.description.set_halign(Gtk.Align.CENTER)
        self.description.set_wrap(True)
        self.append(self.description)

        self.command_description = Gtk.Label()
        self.command_description.set_halign(Gtk.Align.CENTER)
        self.command_description.set_wrap(True)
        self.append(self.command_description)

        self.command_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        self.command_box.set_halign(Gtk.Align.CENTER)

        self.command_entry = Gtk.Entry()
        self.command_entry.set_editable(False)
        self.command_entry.set_hexpand(True)
        self.command_entry.set_width_chars(52)
        self.command_box.append(self.command_entry)

        copy_button = Gtk.Button(label=_("Copy Command"))
        copy_button.connect("clicked", self._copy_command)
        self.command_box.append(copy_button)
        self.append(self.command_box)

        self.manual_description = Gtk.Label()
        self.manual_description.set_halign(Gtk.Align.CENTER)
        self.manual_description.set_wrap(True)
        self.append(self.manual_description)

        self.set_visible(False)

    def watch_option(self, option: Gtk.CheckButton, backend: str) -> None:
        option.connect("toggled", self._on_option_toggled, backend)

    def _on_option_toggled(self, option, backend: str) -> None:
        if option.get_active():
            self.refresh(backend)

    def refresh(self, backend: str) -> None:
        # The Flatpak permission warning already blocks the build when host
        # inspection is unavailable. Treating every host tool as missing in
        # that state would be misleading.
        if not self._can_inspect_host:
            self.set_visible(False)
            return

        result = self._cache.get(backend)
        if result is None:
            missing = find_missing_build_dependencies(backend)
            distribution = detect_linux_distribution() if missing else None
            command, unsupported = suggest_install_command(missing, distribution)
            result = (missing, distribution, command, unsupported)
            self._cache[backend] = result

        missing, distribution, command, unsupported = result
        if not missing:
            self.set_visible(False)
            return

        labels = ", ".join(dependency.label for dependency in missing)
        self.description.set_text(
            _("The build may fail because the host is missing: {dependencies}.").format(
                dependencies=labels,
            )
        )

        if command:
            self.command_description.set_text(
                _("Suggested command for {distribution}:").format(
                    distribution=distribution.name,
                )
            )
            self.command_entry.set_text(command)
            self.command_description.set_visible(True)
            self.command_box.set_visible(True)
        else:
            self.command_description.set_visible(False)
            self.command_box.set_visible(False)

        if unsupported:
            unsupported_labels = ", ".join(
                dependency.label for dependency in unsupported
            )
            self.manual_description.set_text(
                _("Install manually: {dependencies}.").format(
                    dependencies=unsupported_labels,
                )
            )
            self.manual_description.set_visible(True)
        elif not command:
            self.manual_description.set_text(
                _("Install the missing dependencies with your distribution's package manager before starting the build.")
            )
            self.manual_description.set_visible(True)
        else:
            self.manual_description.set_visible(False)

        self.set_visible(True)

    def _copy_command(self, _button) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(self.command_entry.get_text())
