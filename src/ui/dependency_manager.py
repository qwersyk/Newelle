import threading
from dataclasses import dataclass, field
from gettext import gettext as _

from gi.repository import Adw, GLib, GObject, Gtk

from ..utility.dependencies import Dependency, collect_dependencies
from ..utility.dependency_manager import (
    clean_stale_metadata,
    collect_requirement_owners,
    fetch_latest_versions,
    install_requirements,
    is_newer_version,
    remove_all,
    remove_packages,
    scan,
    update_packages,
)
from ..utility.download_manager import (
    DownloadCancelled,
    DownloadKind,
    get_download_manager,
)
from ..utility.system import open_folder


@dataclass
class _Component:
    """An installed :class:`Dependency` with the values probed on a worker."""

    dependency: Dependency
    owners: list[str]
    version: str | None = None
    size: int | None = None
    details: list[tuple[str, str]] = field(default_factory=list)


class DependencyManagerWindow(Adw.Window):
    """Browse, install, update, and remove optional packages and components."""

    PROGRESS_INTERVAL_MS = 400

    def __init__(self, app, controller, parent=None, **kwargs):
        super().__init__(**kwargs, title=_("Dependencies"))
        self.app = app
        self.controller = controller
        self.path = controller.pip_path
        self.set_default_size(760, 680)
        if parent is not None:
            self.set_transient_for(parent)
            self.set_modal(True)

        self.snapshot = None
        self.components = []
        # Component ID -> (version checked, newer version or None)
        self.component_updates = {}
        self._tracked_task_id = None
        self.latest_versions = {}
        self.updates_checked = False
        self.busy = False
        self.query = ""
        self._progress_source = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text=_("Reload"),
        )
        self.refresh_button.connect("clicked", lambda _button: self.reload())
        header.pack_start(self.refresh_button)

        self.search_button = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text=_("Search packages"),
        )
        header.pack_end(self.search_button)
        self.updates_button = Gtk.Button(
            icon_name="software-update-available-symbolic",
            tooltip_text=_("Check for updates"),
        )
        self.updates_button.connect("clicked", lambda _button: self.check_updates())
        header.pack_end(self.updates_button)
        self.spinner = Gtk.Spinner(visible=False)
        header.pack_end(self.spinner)
        toolbar.add_top_bar(header)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search packages…"),
            hexpand=True,
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_bar = Gtk.SearchBar(show_close_button=True)
        clamp = Adw.Clamp(maximum_size=650, hexpand=True)
        clamp.set_child(self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_button.bind_property(
            "active",
            self.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        toolbar.add_top_bar(self.search_bar)

        self.banner = Adw.Banner(
            title=_("Restart Newelle to apply the dependency changes"),
            revealed=False,
        )
        toolbar.add_top_bar(self.banner)

        self.toast_overlay = Adw.ToastOverlay()
        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.toast_overlay.set_child(self.scrolled)
        toolbar.set_content(self.toast_overlay)
        self.set_content(toolbar)

        self.progress_row = Adw.ActionRow(
            title=_("Working…"),
            use_markup=False,
            activatable=False,
        )
        self.progress_bar = Gtk.ProgressBar(
            valign=Gtk.Align.CENTER,
            width_request=150,
            pulse_step=0.08,
        )
        self.progress_row.add_suffix(self.progress_bar)
        self.cancel_button = Gtk.Button(
            icon_name="process-stop-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text=_("Cancel"),
            visible=False,
        )
        self.cancel_button.connect("clicked", lambda _button: self._cancel_tracked_task())
        self.progress_row.add_suffix(self.cancel_button)

        self.connect("close-request", self._on_close_request)
        self.reload()

    # Data loading

    def reload(self):
        self._set_busy(True, _("Reading installed packages"))
        # Handlers are instantiated through the controller, so collect here on
        # the main loop and only probe the components on the worker.
        try:
            declared = collect_dependencies(self.controller)
        except Exception as exception:  # noqa: BLE001 - keep the Python packages usable
            print(f"Could not collect the managed dependencies: {exception}")
            declared = []

        def worker():
            try:
                owners = collect_requirement_owners(self.controller)
                snapshot = scan(self.path, owners)
                error = None
            except Exception as exception:  # noqa: BLE001 - shown to the user
                snapshot, error = None, exception
            components = self._probe_components(declared)
            GLib.idle_add(self._on_reloaded, snapshot, components, error)

        threading.Thread(target=worker, name="dependency-scan", daemon=True).start()

    @staticmethod
    def _probe_components(declared):
        components = []
        for dependency, owners in declared:
            try:
                if not dependency.is_installed():
                    continue
            except Exception as exception:  # noqa: BLE001 - one broken component must not hide the others
                print(f"Could not check {dependency.id}: {exception}")
                continue
            component = _Component(dependency, owners)
            for attribute, probe in (
                ("version", dependency.get_version),
                ("size", dependency.get_size),
                ("details", dependency.get_details),
            ):
                try:
                    value = probe()
                except Exception as exception:  # noqa: BLE001
                    print(f"Could not read {attribute} of {dependency.id}: {exception}")
                    continue
                if value is not None:
                    setattr(component, attribute, value)
            components.append(component)
        components.sort(key=lambda component: component.dependency.title.casefold())
        return components

    def _on_reloaded(self, snapshot, components, error):
        self._set_busy(False)
        self.components = components
        if error is not None:
            self._toast(_("Could not read the installed packages: {error}").format(error=error))
        else:
            self.snapshot = snapshot
        self._render()
        return False

    def check_updates(self):
        if self.busy:
            return
        names = [package.name for package in self.snapshot.top_level()] if self.snapshot else []
        components = [c for c in self.components if c.dependency.can_check_update]
        if not names and not components:
            return
        self._set_busy(True, _("Checking for updates"))

        def worker():
            versions = fetch_latest_versions(names) if names else {}
            updates, failed = {}, []
            for component in components:
                try:
                    latest = component.dependency.check_update()
                except Exception as exception:  # noqa: BLE001 - reported as a failed check
                    print(f"Could not check updates for {component.dependency.id}: {exception}")
                    failed.append(component.dependency.title)
                    continue
                updates[component.dependency.id] = (component.version, latest)
            GLib.idle_add(self._on_updates_checked, bool(names), versions, updates, failed)

        threading.Thread(target=worker, name="dependency-updates", daemon=True).start()

    def _on_updates_checked(self, checked_packages, versions, updates, failed):
        self._set_busy(False)
        self.component_updates.update(updates)
        packages_failed = checked_packages and not versions
        if not packages_failed:
            self.latest_versions.update(versions)
            self.updates_checked = True
        count = len(self._updatable()) + len(self._updatable_components())
        if packages_failed or failed:
            self._toast(_("Could not check for updates. Check your internet connection."))
        elif count == 0:
            self._toast(_("Everything is up to date"))
        elif count == 1:
            self._toast(_("1 update available"))
        else:
            self._toast(_("{count} updates available").format(count=count))
        self._render()
        return False

    def _component_latest(self, component):
        checked = self.component_updates.get(component.dependency.id)
        if checked is None or checked[0] != component.version:
            return None
        return checked[1]

    def _updatable_components(self):
        return [c for c in self.components if c.dependency.can_update and self._component_latest(c)]

    def _latest(self, package):
        latest = self.latest_versions.get(package.key)
        return latest if is_newer_version(latest, package.version) else None

    def _updatable(self):
        if self.snapshot is None:
            return []
        return [p for p in self.snapshot.top_level() if self._latest(p)]

    # Operations

    def _run(self, description, work, success_message, changes_runtime=True):
        if self.busy:
            return
        self._set_busy(True, description)

        def worker():
            try:
                result = work()
                error = None
            except Exception as exception:  # noqa: BLE001 - shown to the user
                result, error = None, exception
            GLib.idle_add(self._on_operation_finished, result, error, success_message, changes_runtime)

        threading.Thread(target=worker, name="dependency-operation", daemon=True).start()

    def _on_operation_finished(self, result, error, success_message, changes_runtime):
        self._tracked_task_id = None
        self._set_busy(False)
        if changes_runtime:
            self.banner.set_revealed(True)
        self._invalidate_handler_caches()
        if isinstance(error, DownloadCancelled):
            self._toast(_("Cancelled"))
        elif error is not None:
            message = str(error).strip()
            reason = message.splitlines()[-1] if message else type(error).__name__
            self._toast(_("Operation failed: {error}").format(error=reason))
        elif success_message:
            message = success_message(result) if callable(success_message) else success_message
            self._toast(message)
        self.reload()
        return False

    def _invalidate_handler_caches(self):
        handlers = getattr(getattr(self.controller, "handlers", None), "handlers", {}) or {}
        for handler in list(handlers.values()):
            if hasattr(handler, "_is_installed_cache"):
                handler._is_installed_cache = None

    def install(self, requirement):
        requirement = requirement.strip()
        if not requirement:
            return
        self._run(
            _("Installing {name}").format(name=requirement),
            lambda: install_requirements(self.path, requirement),
            _("{name} installed").format(name=requirement),
        )

    def update(self, packages):
        names = [package.name for package in packages]
        if not names:
            return
        self._run(
            _("Updating packages"),
            lambda: update_packages(self.path, names),
            _("Packages updated"),
        )

    def remove(self, keys):
        self._run(
            _("Removing packages"),
            lambda: remove_packages(self.path, keys),
            lambda removed: _("Removed {names}").format(names=", ".join(sorted(removed))) if removed else _("Nothing to remove"),
        )

    def update_component(self, component):
        dependency = component.dependency

        def work():
            with get_download_manager().operation(
                _("Update {name}").format(name=dependency.title),
                kind=dependency.kind,
                source_id=f"dependency:{dependency.id}",
            ) as task:
                self._tracked_task_id = task.task_id
                dependency.update(task)

        self._run(
            _("Updating {name}").format(name=dependency.title),
            work,
            _("{name} updated").format(name=dependency.title),
            changes_runtime=dependency.requires_restart,
        )

    def remove_component(self, component):
        dependency = component.dependency
        self._run(
            _("Removing {name}").format(name=dependency.title),
            dependency.remove,
            _("{name} removed").format(name=dependency.title),
            changes_runtime=dependency.requires_restart,
        )

    def clean_metadata(self):
        self._run(
            _("Cleaning up old versions"),
            lambda: clean_stale_metadata(self.path),
            lambda count: _("Removed {count} leftover metadata folders").format(count=count),
            changes_runtime=False,
        )

    # Confirmation dialogs

    def _confirm(self, heading, body, responses, callback):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", _("Cancel"))
        for response_id, label, appearance in responses:
            dialog.add_response(response_id, label)
            dialog.set_response_appearance(response_id, appearance)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: callback(response) if response != "cancel" else None)
        dialog.present(self)

    @staticmethod
    def _short_list(names, limit=3):
        names = list(names)
        if len(names) > limit:
            return ", ".join(names[:limit]) + " " + _("and {count} more").format(count=len(names) - limit)
        return ", ".join(names)

    @classmethod
    def _names(cls, packages, limit=12):
        return cls._short_list((package.name for package in packages), limit)

    def confirm_remove(self, package):
        keys = {package.key}
        dependents = self.snapshot.dependents(keys)
        orphans = self.snapshot.orphaned_by_removal(keys)
        paragraphs = []
        if package.used_by:
            paragraphs.append(
                _("It is used by {owners}. They will offer to install it again when needed.").format(
                    owners=", ".join(package.used_by)
                )
            )
        if dependents:
            paragraphs.append(
                _("These packages depend on it and may stop working: {names}").format(
                    names=self._names(dependents)
                )
            )
        if orphans:
            paragraphs.append(
                _("These dependencies would no longer be needed: {names}").format(
                    names=self._names(orphans)
                )
            )
        responses = []
        if orphans:
            responses.append((
                "remove-with-dependencies",
                _("Remove With Dependencies"),
                Adw.ResponseAppearance.DESTRUCTIVE,
            ))
        responses.append(("remove", _("Remove"), Adw.ResponseAppearance.DESTRUCTIVE))

        def on_response(response):
            if response == "remove-with-dependencies":
                self.remove(keys | {p.key for p in orphans})
            elif response == "remove":
                self.remove(keys)

        self._confirm(
            _("Remove {name}?").format(name=package.name),
            "\n\n".join(paragraphs) or _("The package will be deleted from the pip path."),
            responses,
            on_response,
        )

    def confirm_remove_component(self, component):
        dependency = component.dependency
        paragraphs = []
        if component.owners:
            paragraphs.append(
                _("It is used by {owners}. You can install it again from their settings.").format(
                    owners=", ".join(component.owners)
                )
            )
        try:
            notice = dependency.get_removal_notice()
        except Exception as exception:  # noqa: BLE001
            print(f"Could not read the removal notice of {dependency.id}: {exception}")
            notice = None
        if notice:
            paragraphs.append(notice)
        self._confirm(
            _("Remove {name}?").format(name=dependency.title),
            "\n\n".join(paragraphs) or _("The component will be deleted."),
            [("remove", _("Remove"), Adw.ResponseAppearance.DESTRUCTIVE)],
            lambda _response: self.remove_component(component),
        )

    def confirm_remove_unused(self):
        unused = self.snapshot.unused()
        if not unused:
            return
        self._confirm(
            _("Remove Unused Dependencies?"),
            _("No installed package needs these anymore: {names}").format(names=self._names(unused, 30)),
            [("remove", _("Remove"), Adw.ResponseAppearance.DESTRUCTIVE)],
            lambda _response: self.remove({p.key for p in unused}),
        )

    def confirm_update_all(self):
        packages = self._updatable()
        if not packages:
            return
        self._confirm(
            _("Update All Packages?"),
            _("These packages will be updated to their latest compatible versions, together with their dependencies: {names}").format(
                names=self._names(packages, 30)
            ),
            [("update", _("Update"), Adw.ResponseAppearance.SUGGESTED)],
            lambda _response: self.update(packages),
        )

    def confirm_remove_all(self):
        self._confirm(
            _("Remove All Dependencies?"),
            _("Every optional package will be deleted. Providers and extensions will ask to install them again when needed."),
            [("remove", _("Remove All"), Adw.ResponseAppearance.DESTRUCTIVE)],
            lambda _response: self._run(
                _("Removing all packages"),
                lambda: remove_all(self.path),
                _("All dependencies removed"),
            ),
        )

    # Rendering

    def _on_search_changed(self, entry):
        self.query = entry.get_text().strip().casefold()
        self._render()

    def _matches(self, package):
        if not self.query:
            return True
        haystack = " ".join([package.name, package.summary, *package.used_by]).casefold()
        return self.query in haystack

    def _component_matches(self, component):
        if not self.query:
            return True
        dependency = component.dependency
        haystack = " ".join([dependency.title, dependency.description, *component.owners]).casefold()
        return self.query in haystack

    def _render(self):
        main = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=18,
            margin_bottom=18,
            margin_start=12,
            margin_end=12,
        )
        clamp = Adw.Clamp(maximum_size=800, child=main)
        self._content_box = main

        progress_group = Adw.PreferencesGroup(visible=self.busy)
        if self.progress_row.get_parent() is not None:
            self.progress_row.get_parent().remove(self.progress_row)
        progress_group.add(self.progress_row)
        main.append(progress_group)
        self.progress_group = progress_group

        main.append(self._build_overview())
        if self.snapshot is not None:
            main.append(self._build_maintenance())
        components = [c for c in self.components if self._component_matches(c)]
        if components:
            group = Adw.PreferencesGroup(
                title=_("Components"),
                description=_("Runtimes and tools installed by providers and extensions"),
            )
            for component in components:
                group.add(self._build_component_row(component))
            main.append(group)
        if self.snapshot is not None:
            top_level = [p for p in self.snapshot.top_level() if self._matches(p)]
            dependencies = [p for p in self.snapshot.dependencies() if self._matches(p)]
            if top_level:
                group = Adw.PreferencesGroup(
                    title=_("Installed Packages"),
                    description=_("Packages requested by providers, extensions, or installed manually"),
                )
                for package in top_level:
                    group.add(self._build_package_row(package))
                main.append(group)
            if dependencies:
                group = Adw.PreferencesGroup(
                    title=_("Dependencies"),
                    description=_("Packages installed because other packages need them"),
                )
                for package in dependencies:
                    group.add(self._build_package_row(package))
                main.append(group)
            if not self.snapshot.packages and not self.components:
                main.append(Adw.StatusPage(
                    icon_name="application-x-addon-symbolic",
                    title=_("No Dependencies Installed"),
                    description=_("Optional packages installed by providers and extensions will appear here."),
                ))
            elif not top_level and not dependencies and not components:
                main.append(Adw.StatusPage(
                    icon_name="system-search-symbolic",
                    title=_("No Results"),
                ))

        adjustment = self.scrolled.get_vadjustment()
        scroll = adjustment.get_value() if adjustment is not None else 0
        self.scrolled.set_child(clamp)
        if adjustment is not None:
            GLib.idle_add(lambda: adjustment.set_value(scroll) and False)
        self._apply_busy_state()

    def _build_overview(self):
        group = Adw.PreferencesGroup()
        location = Adw.ActionRow(
            title=_("Location"),
            subtitle=self.path,
            subtitle_selectable=True,
            use_markup=False,
        )
        open_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text=_("Open folder"),
        )
        open_button.connect("clicked", lambda _button: open_folder(self.path))
        location.add_suffix(open_button)
        group.add(location)

        if self.snapshot is not None:
            count = len(self.snapshot.packages)
            summary = Adw.ActionRow(
                title=_("Installed"),
                subtitle=_("{count} packages · {size}").format(
                    count=count, size=GLib.format_size(self.snapshot.total_size)
                ),
                use_markup=False,
            )
            group.add(summary)

        install_row = Adw.EntryRow(
            title=_("Install a package (for example requests or numpy==2.0)"),
            show_apply_button=True,
        )
        install_row.connect("apply", lambda row: self.install(row.get_text()))
        install_row.connect("entry-activated", lambda row: self.install(row.get_text()))
        group.add(install_row)
        return group

    def _build_maintenance(self):
        group = Adw.PreferencesGroup(title=_("Maintenance"))

        updatable = self._updatable()
        if updatable:
            group.add(self._action_row(
                _("Updates Available"),
                _("{count} packages can be updated").format(count=len(updatable))
                if len(updatable) != 1 else _("1 package can be updated"),
                _("Update All"),
                ["suggested-action"],
                self.confirm_update_all,
            ))
        elif self.updates_checked:
            group.add(Adw.ActionRow(
                title=_("Updates"),
                subtitle=_("All packages are up to date"),
            ))
        else:
            group.add(self._action_row(
                _("Updates"),
                _("Look for newer versions of the installed packages"),
                _("Check"),
                [],
                self.check_updates,
            ))

        unused = self.snapshot.unused()
        if unused:
            group.add(self._action_row(
                _("Unused Dependencies"),
                _("{count} packages are no longer needed by anything").format(count=len(unused))
                if len(unused) != 1 else _("1 package is no longer needed by anything"),
                _("Remove"),
                [],
                self.confirm_remove_unused,
            ))

        stale = self.snapshot.stale_metadata()
        if stale:
            group.add(self._action_row(
                _("Old Versions"),
                _("{count} leftover metadata folders from replaced versions").format(count=len(stale)),
                _("Clean Up"),
                [],
                self.clean_metadata,
            ))

        group.add(self._action_row(
            _("Remove All Dependencies"),
            _("Delete every optional package installed by Newelle"),
            _("Remove All"),
            ["destructive-action"],
            self.confirm_remove_all,
        ))
        return group

    @staticmethod
    def _action_row(title, subtitle, label, css_classes, callback):
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=css_classes)
        button.connect("clicked", lambda _button: callback())
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _package_subtitle(self, package):
        latest = self._latest(package)
        version = package.version
        if latest:
            version = _("{current} → {latest}").format(current=package.version, latest=latest)
        details = [version, GLib.format_size(package.size)]
        if package.used_by:
            details.append(_("Used by {owners}").format(owners=self._short_list(package.used_by)))
        elif package.is_top_level:
            details.append(_("Installed manually"))
        elif package.required_by:
            details.append(_("Required by {names}").format(
                names=self._short_list(sorted(self.snapshot.packages[k].name for k in package.required_by))
            ))
        else:
            details.append(_("Unused"))
        return " · ".join(details)

    def _build_component_row(self, component):
        dependency = component.dependency
        latest = self._component_latest(component)
        version = component.version or _("Unknown version")
        if latest:
            version = _("{current} → {latest}").format(current=version, latest=latest)
        subtitle = [version]
        if component.size is not None:
            subtitle.append(GLib.format_size(component.size))
        if component.owners:
            subtitle.append(_("Used by {owners}").format(owners=self._short_list(component.owners)))
        row = Adw.ExpanderRow(
            title=dependency.title,
            subtitle=" · ".join(subtitle),
            use_markup=False,
        )
        row.add_prefix(Gtk.Image(icon_name=dependency.icon_name))
        if latest and dependency.can_update:
            update_button = Gtk.Button(
                icon_name="software-update-available-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Update to {version}").format(version=latest),
            )
            update_button.connect("clicked", lambda _button: self.update_component(component))
            row.add_suffix(update_button)
        if dependency.can_remove:
            remove_button = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Remove"),
            )
            remove_button.connect("clicked", lambda _button: self.confirm_remove_component(component))
            row.add_suffix(remove_button)

        details = []
        if dependency.description:
            details.append((_("Description"), dependency.description))
        details.extend(component.details)
        if component.owners:
            details.append((_("Used by"), ", ".join(component.owners)))
        for title, subtitle in details:
            detail = Adw.ActionRow(
                title=title,
                subtitle=subtitle,
                use_markup=False,
                subtitle_selectable=True,
            )
            detail.add_css_class("property")
            row.add_row(detail)
        if dependency.location:
            location = Adw.ActionRow(
                title=_("Location"),
                subtitle=dependency.location,
                use_markup=False,
                subtitle_selectable=True,
            )
            location.add_css_class("property")
            open_button = Gtk.Button(
                icon_name="folder-open-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Open folder"),
            )
            open_button.connect("clicked", lambda _button: open_folder(dependency.location))
            location.add_suffix(open_button)
            row.add_row(location)
        return row

    def _build_package_row(self, package):
        row = Adw.ExpanderRow(
            title=package.name,
            subtitle=self._package_subtitle(package),
            use_markup=False,
        )
        latest = self._latest(package)
        if latest:
            update_button = Gtk.Button(
                icon_name="software-update-available-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=_("Update to {version}").format(version=latest),
            )
            update_button.connect("clicked", lambda _button: self.update([package]))
            row.add_suffix(update_button)
        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text=_("Remove"),
        )
        remove_button.connect("clicked", lambda _button: self.confirm_remove(package))
        row.add_suffix(remove_button)

        packages = self.snapshot.packages
        details = []
        if package.summary:
            details.append((_("Description"), package.summary))
        if package.used_by:
            details.append((_("Used by"), ", ".join(package.used_by)))
        requires = sorted(packages[k].name for k in package.requires if k in packages)
        if requires:
            details.append((_("Depends on"), ", ".join(requires)))
        required_by = sorted(packages[k].name for k in package.required_by)
        if required_by:
            details.append((_("Required by"), ", ".join(required_by)))
        if package.stale:
            details.append((
                _("Old versions"),
                ", ".join(dist.version for dist in package.stale),
            ))
        for title, subtitle in details:
            detail = Adw.ActionRow(
                title=title,
                subtitle=subtitle,
                use_markup=False,
                subtitle_selectable=True,
            )
            detail.add_css_class("property")
            row.add_row(detail)
        return row

    # Busy state

    def _set_busy(self, busy, description=None):
        self.busy = busy
        if description:
            self.progress_row.set_title(description)
            self.progress_row.set_subtitle("")
        self._apply_busy_state()
        if busy and self._progress_source is None:
            self._progress_source = GLib.timeout_add(self.PROGRESS_INTERVAL_MS, self._poll_progress)
        elif not busy and self._progress_source is not None:
            GLib.source_remove(self._progress_source)
            self._progress_source = None

    def _apply_busy_state(self):
        busy = self.busy
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        progress_group = getattr(self, "progress_group", None)
        if progress_group is not None:
            progress_group.set_visible(busy)
        content = getattr(self, "_content_box", None)
        widget = content.get_first_child() if content is not None else None
        while widget is not None:
            # Keep the progress row readable while blocking every other action.
            if widget is not progress_group:
                widget.set_sensitive(not busy)
            widget = widget.get_next_sibling()
        self._update_header()

    def _update_header(self):
        has_packages = self.snapshot is not None and bool(self.snapshot.top_level())
        has_components = any(c.dependency.can_check_update for c in self.components)
        self.refresh_button.set_sensitive(not self.busy)
        self.updates_button.set_sensitive(not self.busy and (has_packages or has_components))

    def _poll_progress(self):
        manager = get_download_manager()
        task = None
        if self._tracked_task_id is not None:
            try:
                task = manager.get(self._tracked_task_id)
            except KeyError:
                task = None
        if task is None:
            tasks = [t for t in manager.list(active=True) if t.kind == DownloadKind.DEPENDENCY]
            task = tasks[-1] if tasks else None
        self.cancel_button.set_visible(
            task is not None and task.task_id == self._tracked_task_id and task.cancellable
        )
        if task is not None:
            self.progress_row.set_subtitle(_(task.phase) if task.phase else "")
            if task.fraction is None:
                self.progress_bar.pulse()
            else:
                self.progress_bar.set_fraction(task.fraction)
        else:
            self.progress_bar.pulse()
        return True

    def _cancel_tracked_task(self):
        if self._tracked_task_id is not None:
            get_download_manager().cancel(self._tracked_task_id)
        self.cancel_button.set_visible(False)

    def _on_close_request(self, *_args):
        if self._progress_source is not None:
            GLib.source_remove(self._progress_source)
            self._progress_source = None
        return False

    def _toast(self, message):
        toast = Adw.Toast(title=message, timeout=4)
        toast.set_use_markup(False)
        self.toast_overlay.add_toast(toast)
