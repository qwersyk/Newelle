"""Components that Newelle installs at runtime and lets the user manage.

A :class:`Dependency` describes one installed component (a native runtime, a
downloaded tool, a data bundle, ...) and how to update and remove it. Handlers
and extensions return them from ``get_dependencies()``; other code can call
:func:`register_dependency`. The dependency manager window lists every
installed dependency next to the Python packages in the pip path.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from gettext import gettext as _

from .build_process import BuildCancelled
from .download_manager import DownloadCancelled, DownloadKind, DownloadTaskHandle
from .runtime_installer import (
    RuntimeProject,
    get_runtime_installer,
    read_runtime_manifest,
)

_BRANCHES = ("master", "main")


def directory_size(path: str | None) -> int:
    """Return the size in bytes of the files below ``path``, without following links."""
    if not path:
        return 0
    if os.path.isfile(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _run_on_main_loop(callback, *args):
    """Run a handler hook on the GTK main loop, where settings rows live."""

    def run():
        try:
            callback(*args)
        except Exception as error:  # noqa: BLE001 - one hook must not break the others
            print(f"Dependency hook {callback} failed: {error}")
        return False

    try:
        from gi.repository import GLib
    except ImportError:
        run()
        return
    GLib.idle_add(run)


class Dependency:
    """A component installed at runtime that the user can update or remove.

    Behaviour can be supplied either as constructor callables or by
    overriding the methods of the same name in a subclass. Every method may
    block: the dependency manager calls them on worker threads.

    Args:
        id: Stable unique identifier. Handlers that share a component must
            return dependencies with the same ID; the manager shows it once.
        title: Name shown to the user.
        description: One-line explanation of what the component is for.
        icon_name: Symbolic icon shown next to the title.
        location: File or directory where the component is installed. It is
            used by the default ``is_installed()`` and ``get_size()``.
        is_installed: ``() -> bool``. Defaults to checking that ``location`` exists.
        get_version: ``() -> str | None``, the installed version.
        get_size: ``() -> int | None``, bytes on disk. Defaults to the size of ``location``.
        get_details: ``() -> list[tuple[str, str]]`` of extra rows to show.
        check_update: ``() -> str | None``, the newer version available, if any.
        update: ``(task) -> None``. Install the newest version. ``task`` is a
            :class:`DownloadTaskHandle` for reporting progress.
        remove: ``() -> None``. Delete the component.
        removal_notice: Extra sentence shown when the user confirms removal.
        requires_restart: Whether changes only apply after restarting Newelle.
    """

    #: Category used for the progress entry in the downloads window.
    kind = DownloadKind.DEPENDENCY

    def __init__(
        self,
        id: str,
        title: str,
        description: str = "",
        *,
        icon_name: str = "application-x-addon-symbolic",
        location: str | None = None,
        is_installed: Callable[[], bool] | None = None,
        get_version: Callable[[], str | None] | None = None,
        get_size: Callable[[], int | None] | None = None,
        get_details: Callable[[], list[tuple[str, str]]] | None = None,
        check_update: Callable[[], str | None] | None = None,
        update: Callable[[DownloadTaskHandle], None] | None = None,
        remove: Callable[[], None] | None = None,
        removal_notice: str | None = None,
        requires_restart: bool = False,
    ):
        self.id = id
        self.title = title
        self.description = description
        self.icon_name = icon_name
        self.location = os.path.abspath(location) if location else None
        self.removal_notice = removal_notice
        self.requires_restart = requires_restart
        self._is_installed = is_installed
        self._get_version = get_version
        self._get_size = get_size
        self._get_details = get_details
        self._check_update = check_update
        self._update = update
        self._remove = remove

    def _overrides(self, name: str) -> bool:
        return getattr(type(self), name) is not getattr(Dependency, name)

    def is_installed(self) -> bool:
        if self._is_installed is not None:
            return bool(self._is_installed())
        return self.location is not None and os.path.exists(self.location)

    def get_version(self) -> str | None:
        return self._get_version() if self._get_version is not None else None

    def get_size(self) -> int | None:
        if self._get_size is not None:
            return self._get_size()
        return directory_size(self.location) if self.location else None

    def get_details(self) -> list[tuple[str, str]]:
        return list(self._get_details()) if self._get_details is not None else []

    @property
    def can_check_update(self) -> bool:
        return self._check_update is not None or self._overrides("check_update")

    def check_update(self) -> str | None:
        """Return the newer version available, or ``None`` when up to date or unknown."""
        return self._check_update() if self._check_update is not None else None

    @property
    def can_update(self) -> bool:
        return self._update is not None or self._overrides("update")

    def update(self, task: DownloadTaskHandle) -> None:
        """Install the newest version. Raise to report a failure."""
        if self._update is None:
            raise NotImplementedError(f"{self.id} cannot be updated")
        self._update(task)

    @property
    def can_remove(self) -> bool:
        return self._remove is not None or self._overrides("remove")

    def remove(self) -> None:
        """Delete the component. Raise to report a failure."""
        if self._remove is None:
            raise NotImplementedError(f"{self.id} cannot be removed")
        self._remove()

    def get_removal_notice(self) -> str | None:
        return self.removal_notice

    def merge(self, other: Dependency) -> None:
        """Absorb a duplicate declaration of this dependency from another owner.

        The first declaration is kept. Override to also run the hooks that
        the other owner attached, as :class:`RuntimeDependency` does.
        """


class RuntimeDependency(Dependency):
    """A native runtime installed with :class:`RuntimeInstaller`.

    Updates reuse the recorded installation: pre-built binaries are replaced
    by the latest release for the same backend, source builds are rebuilt with
    the same backend and CMake flags. Removal keeps the project's
    ``preserve_patterns`` (for example downloaded models).

    Args:
        project: The :class:`RuntimeProject` passed to the installer.
        path: The runtime directory passed to the installer.
        handler: Optional owning handler. Its ``on_runtime_installed`` is
            called after an update, and its settings rows are refreshed
            after updates and removals.
        installation_info: ``() -> dict`` describing runtimes installed
            before Newelle recorded a manifest, with ``backend`` and,
            optionally, ``version`` and ``method`` (``"prebuilt"`` or
            ``"source"``).
        on_installed: ``(backend, version, prebuilt) -> None`` run on the
            main loop after an update.
        before_remove: ``() -> None`` run on the worker before deleting files,
            for example to stop a server using the binaries.
        on_removed: ``() -> None`` run on the main loop after removal.
    """

    kind = DownloadKind.RUNTIME

    def __init__(
        self,
        project: RuntimeProject,
        path: str,
        *,
        title: str | None = None,
        description: str = "",
        icon_name: str = "system-run-symbolic",
        handler=None,
        installation_info: Callable[[], dict] | None = None,
        on_installed: Callable[[str, str | None, bool], None] | None = None,
        before_remove: Callable[[], None] | None = None,
        on_removed: Callable[[], None] | None = None,
    ):
        path = os.path.abspath(path)
        super().__init__(
            f"runtime:{project.name}:{path}",
            title or project.name,
            description,
            icon_name=icon_name,
            location=path,
            requires_restart=False,
        )
        self.project = project
        if on_installed is None and handler is not None:
            on_installed = getattr(handler, "on_runtime_installed", None)
        self._installation_info = [installation_info] if installation_info else []
        self._on_installed = [on_installed] if on_installed else []
        self._before_remove = [before_remove] if before_remove else []
        self._on_removed = [on_removed] if on_removed else []
        self._handlers = [handler] if handler is not None else []

    @property
    def installer(self):
        return get_runtime_installer(self.project, self.location)

    def is_installed(self) -> bool:
        binary_dir = os.path.join(self.location, "build", "bin")
        found = [os.path.isfile(os.path.join(binary_dir, name)) for name in self.project.binaries]
        return all(found) if self.project.require_all_binaries else any(found)

    def installation(self) -> dict:
        """Return how the runtime was installed, from its manifest or the owners."""
        info = {}
        for provider in self._installation_info:
            try:
                provided = provider() or {}
            except Exception as error:  # noqa: BLE001 - fall back to the other owners
                print(f"Could not read the {self.title} installation: {error}")
                continue
            for key, value in provided.items():
                if value is not None:
                    info.setdefault(key, value)
        info.update(read_runtime_manifest(self.location) or {})
        if not info.get("method"):
            is_checkout = os.path.isdir(os.path.join(self.location, ".git"))
            info["method"] = "source" if is_checkout else "prebuilt"
        return info

    def get_version(self) -> str | None:
        return self.installation().get("version")

    def get_details(self) -> list[tuple[str, str]]:
        info = self.installation()
        details = [(
            _("Installation"),
            _("Pre-built binary") if info["method"] == "prebuilt" else _("Compiled from source"),
        )]
        backend = info.get("backend")
        if backend:
            details.append((_("Backend"), self.project.backend_labels.get(backend, backend)))
        if info.get("source") and info["source"] != "official":
            details.append((_("Build provider"), info["source"]))
        if info.get("custom_flags"):
            details.append((_("Custom CMake flags"), " ".join(info["custom_flags"])))
        return details

    def get_removal_notice(self) -> str | None:
        if self.project.preserve_patterns:
            return _("Downloaded models stored inside it are kept.")
        return None

    def _latest_asset(self, info: dict) -> dict | None:
        assets = self.installer.fetch_releases()
        backend = info.get("backend")
        if not backend:
            return None
        candidates = [asset for asset in assets if asset.get("backend") == backend]
        for key, default in (("source", "official"), ("cuda_version", None)):
            if info.get(key) is None:
                continue
            same = [a for a in candidates if a.get(key, default) == info[key]]
            candidates = same or candidates
        return candidates[0] if candidates else None

    def _latest_source_version(self, info: dict) -> str | None:
        """Return the revision an update builds; ``None`` means the default branch."""
        requested = info.get("requested_version", info.get("version"))
        if requested in _BRANCHES:
            return requested
        if not (self.project.versions or self.project.fetch_versions):
            return None
        try:
            versions = self.installer.fetch_versions()
        except Exception as error:  # noqa: BLE001 - keep the bundled versions
            print(f"Failed to fetch {self.title} versions: {error}")
            versions = list(self.project.versions)
        tagged = [version for version in versions if version not in _BRANCHES]
        return tagged[0] if tagged else requested

    def check_update(self) -> str | None:
        info = self.installation()
        installed = info.get("version")
        if info["method"] == "prebuilt":
            if self.project.fetch_releases is None:
                return None
            asset = self._latest_asset(info)
            latest = asset.get("tag") if asset else None
        else:
            latest = self._latest_source_version(info)
            if latest in _BRANCHES or (latest is None and not info.get("commit")):
                return None
            if latest is None:
                remote = self.installer.fetch_remote_commit()
                if remote is None or remote == info["commit"]:
                    return None
                return remote[:8]
        if not latest:
            return None
        if installed and (installed == latest or installed.startswith(latest + "-")):
            return None
        return latest

    def update(self, task: DownloadTaskHandle) -> None:
        info = self.installation()
        backend = info.get("backend")
        installer = self.installer
        cancel_event = threading.Event()
        finished = threading.Event()

        def forward_cancellation():
            while not finished.wait(0.5):
                if task.cancel_requested:
                    installer.cancel(cancel_event)
                    return

        task.update(cancellable=True)
        threading.Thread(target=forward_cancellation, daemon=True).start()
        try:
            if info["method"] == "prebuilt":
                asset = self._latest_asset(info)
                if asset is None:
                    raise RuntimeError(
                        _("No pre-built {name} release is available for the installed backend").format(
                            name=self.title
                        )
                    )
                installer.install_prebuilt(
                    asset, f"dependency:{self.id}", cancel_event=cancel_event
                )
                backend, version, prebuilt = asset.get("backend"), asset.get("tag"), True
            else:
                version = self._latest_source_version(info)
                task.update(phase=_("Building {name}").format(name=self.title), reset_progress=True)
                installer.build(
                    backend,
                    info.get("custom_flags") or "",
                    version,
                    progress=lambda value: task.update(fraction=value),
                    cancel_event=cancel_event,
                )
                backend = backend or next(iter(self.project.backend_flags), None)
                prebuilt = False
        except BuildCancelled:
            raise DownloadCancelled() from None
        finally:
            finished.set()
        for hook in self._on_installed:
            _run_on_main_loop(hook, backend, version, prebuilt)
        self._refresh_handlers()

    def remove(self) -> None:
        for hook in self._before_remove:
            try:
                hook()
            except Exception as error:  # noqa: BLE001 - removal can still proceed
                print(f"Could not prepare {self.title} for removal: {error}")
        self.installer.uninstall()
        for hook in self._on_removed:
            _run_on_main_loop(hook)
        self._refresh_handlers()

    def _refresh_handlers(self):
        def refresh(handler):
            handler._is_installed_cache = None
            handler.settings_update()

        for handler in self._handlers:
            _run_on_main_loop(refresh, handler)

    def merge(self, other: Dependency) -> None:
        if not isinstance(other, RuntimeDependency):
            return
        self._installation_info.extend(other._installation_info)
        self._on_installed.extend(other._on_installed)
        self._before_remove.extend(other._before_remove)
        self._on_removed.extend(other._on_removed)
        self._handlers.extend(h for h in other._handlers if h not in self._handlers)


_registry: dict[str, tuple[Dependency, str | None]] = {}
_registry_lock = threading.Lock()


def register_dependency(dependency: Dependency, owner: str | None = None) -> None:
    """Make a dependency manageable without a handler declaring it.

    ``owner`` is shown as "Used by". Registering an existing ID replaces it.
    """
    with _registry_lock:
        _registry[dependency.id] = (dependency, owner)


def unregister_dependency(dependency_id: str) -> None:
    with _registry_lock:
        _registry.pop(dependency_id, None)


def _declares_dependencies(owner) -> bool:
    from ..handlers.handler import Handler

    owner_class = owner if isinstance(owner, type) else type(owner)
    method = getattr(owner_class, "get_dependencies", None)
    return method is not None and method is not Handler.get_dependencies


def collect_dependencies(controller=None) -> list[tuple[Dependency, list[str]]]:
    """Return every declared dependency with the labels of the owners using it.

    Handlers are instantiated through the controller's handler cache, so call
    this on the GTK main loop. Probing the returned dependencies may block and
    belongs on a worker.
    """
    from .. import constants

    collected: dict[str, tuple[Dependency, list[str]]] = {}

    def add(dependencies, label):
        for dependency in dependencies or []:
            if not isinstance(dependency, Dependency):
                continue
            if dependency.id in collected:
                existing, owners = collected[dependency.id]
                if existing is not dependency:
                    existing.merge(dependency)
            else:
                owners = []
                collected[dependency.id] = (dependency, owners)
            if label and label not in owners:
                owners.append(label)

    with _registry_lock:
        registered = list(_registry.values())
    for dependency, owner in registered:
        add([dependency], owner)

    categories = (
        ("AVAILABLE_LLMS", _("LLM")),
        ("AVAILABLE_TTS", _("Text to Speech")),
        ("AVAILABLE_STT", _("Speech to Text")),
        ("AVAILABLE_EMBEDDINGS", _("Embedding")),
        ("AVAILABLE_MEMORIES", _("Memory")),
        ("AVAILABLE_RAGS", _("Document Sources")),
        ("AVAILABLE_WEBSEARCH", _("Web Search")),
        ("AVAILABLE_IMAGE_GENERATORS", _("Image Generation")),
        ("AVAILABLE_INTERFACES", _("Interface")),
    )
    handlers_manager = getattr(controller, "handlers", None)
    for attribute, category in categories:
        registry = getattr(constants, attribute, {}) or {}
        for key, descriptor in list(registry.items()):
            handler_class = descriptor.get("class")
            if (
                handler_class is None
                or descriptor.get("duplicated", False)
                or handlers_manager is None
                or not _declares_dependencies(handler_class)
            ):
                continue
            try:
                handler = handlers_manager.get_object(registry, key)
                dependencies = handler.get_dependencies()
            except Exception as error:  # noqa: BLE001 - a broken provider must not hide the others
                print(f"Could not read the dependencies of {key}: {error}")
                continue
            title = descriptor.get("title") or key
            add(dependencies, f"{title} ({category})")

    for loader_name in ("extensionloader", "integrationsloader"):
        loader = getattr(controller, loader_name, None)
        if loader is None:
            continue
        for extension in list(loader.get_extensions()):
            if not _declares_dependencies(extension):
                continue
            try:
                dependencies = extension.get_dependencies()
            except Exception as error:  # noqa: BLE001
                print(f"Could not read the dependencies of {extension.id}: {error}")
                continue
            name = getattr(extension, "name", None) or getattr(extension, "id", "")
            add(dependencies, f"{name} ({_('Extension')})")
    return list(collected.values())
