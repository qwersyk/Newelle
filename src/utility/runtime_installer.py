"""Shared source and binary installation for the local C++ runtimes.

This module performs synchronous work. Callers choose the worker thread and
marshal the optional log/progress callbacks onto their UI's main loop.
"""

import os
import shlex
import shutil
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from gettext import gettext as _
from pathlib import Path

import requests

from .build_process import BuildCancelled, BuildProcess
from .download_manager import DownloadCancelled, DownloadKind, get_download_manager
from .system import can_escape_sandbox, get_spawn_command


@dataclass(frozen=True)
class RuntimeProject:
    """Handler-provided configuration for a CMake runtime.

    Discovery callbacks run on worker threads. Release dictionaries contain
    name, url, size, backend and tag; optional title, source, recommended and
    best_match fields customize their presentation without installer changes.
    Preservation patterns are paths relative to the runtime directory.
    """

    name: str
    repository: str
    binaries: tuple[str, ...]
    backend_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    backend_labels: dict[str, str] = field(default_factory=dict)
    cmake_flags: tuple[str, ...] = ()
    binary_aliases: tuple[tuple[str, str], ...] = ()
    require_all_binaries: bool = True
    submodules: bool = False
    fetch_releases: Callable[[], list[dict]] | None = None
    versions: tuple[str, ...] = ()
    fetch_versions: Callable[[], list[str]] | None = None
    preserve_patterns: tuple[str, ...] = ()


def fetch_json(url):
    """Fetch release metadata for a handler's discovery callback."""
    with requests.get(url, timeout=15) as response:
        response.raise_for_status()
        return response.json()


_installers = {}
_installation_locks = {}
_installers_lock = threading.Lock()


def get_runtime_installer(project, path):
    """Share the installation lock when handlers use the same runtime path."""
    path = os.path.abspath(path)
    with _installers_lock:
        key = (path, id(project))
        if key not in _installers:
            lock = _installation_locks.setdefault(path, threading.Lock())
            _installers[key] = RuntimeInstaller(project, path, installation_lock=lock)
        return _installers[key]


class RuntimeInstaller:
    def __init__(self, project: RuntimeProject, path: str, *, installation_lock=None):
        self.project = project
        self.path = os.path.abspath(path)
        self.process = BuildProcess(project.name + "-build")
        self.process.register_atexit()
        self._lock = (
            installation_lock if installation_lock is not None else threading.Lock()
        )
        self._cancel = threading.Event()

    def cancel(self, cancel_event=None):
        if cancel_event is not None and cancel_event is not self._cancel:
            cancel_event.set()
            return
        self._cancel.set()
        self.process.cancel()

    def _check_cancelled(self, task=None):
        if self._cancel.is_set():
            raise BuildCancelled()
        if task is not None:
            task.check_cancelled()

    def _begin(self, cancel_event=None):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError(_("An installation is already running"))
        try:
            self._cancel = (
                cancel_event if cancel_event is not None else threading.Event()
            )
            self.process.begin()
            self._check_cancelled()
        except Exception:
            self._lock.release()
            raise

    def _run(self, command, log, cwd=None):
        self._check_cancelled()
        if not self.process.run(get_spawn_command() + command, cwd=cwd, on_output=log):
            self._check_cancelled()
            raise RuntimeError(
                _("Command failed: {command}").format(command=shlex.join(command))
            )

    def build(
        self,
        backend=None,
        custom_flags="",
        version=None,
        *,
        log=lambda text: None,
        progress=lambda value: None,
        cancel_event=None,
    ):
        """Build in a sibling directory, then replace the installed runtime."""
        self._begin(cancel_event)
        try:
            if not can_escape_sandbox():
                raise RuntimeError(_("Please run the Flatpak override command first"))
            if backend is None:
                backend = next(iter(self.project.backend_flags), None)
            flags = list(self.project.cmake_flags) + list(
                self.project.backend_flags.get(backend, ())
            )
            flags.extend(
                shlex.split(custom_flags)
                if isinstance(custom_flags, str)
                else custom_flags
            )
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".runtime-build-", dir=os.path.dirname(self.path)
            ) as temporary:
                source = os.path.join(temporary, "source")
                progress(0.1)
                log(
                    _("Cloning {name} repository...").format(name=self.project.name)
                    + "\n"
                )
                clone = ["git", "clone"]
                if version is None:
                    clone += ["--depth", "1"]
                self._run(clone + [self.project.repository, source], log)
                if version:
                    self._run(["git", "checkout", version], log, source)
                if self.project.submodules:
                    log(_("Initializing submodules...") + "\n")
                    self._run(
                        ["git", "submodule", "update", "--init", "--recursive"],
                        log,
                        source,
                    )
                progress(0.25)
                log(_("Configuring CMake build...") + "\n")
                self._run(
                    [
                        "cmake",
                        "-B",
                        "build",
                        "-DCMAKE_BUILD_TYPE=Release",
                        "-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON",
                    ]
                    + flags,
                    log,
                    source,
                )
                progress(0.4)
                log(
                    _("Building {name} (Backend: {backend})...").format(
                        name=self.project.name, backend=backend
                    )
                    + "\n"
                )
                self._run(
                    [
                        "cmake",
                        "--build",
                        "build",
                        "--config",
                        "Release",
                        "-j",
                        str(os.cpu_count() or 1),
                    ],
                    log,
                    source,
                )
                self._check_cancelled()
                self._validate_binaries(os.path.join(source, "build", "bin"))
                self._publish(source, temporary)
            progress(1.0)
            log(_("Build completed successfully!") + "\n")
        finally:
            self._lock.release()

    def install_prebuilt(
        self,
        asset,
        source_id,
        *,
        log=lambda text: None,
        progress=lambda value: None,
        cancel_event=None,
    ):
        self._begin(cancel_event)
        task = None
        try:
            task = get_download_manager().create_task(
                _("Install {name}").format(name=asset["name"]),
                kind=DownloadKind.RUNTIME,
                source_id=source_id,
                phase=_("Downloading runtime"),
                cancellable=True,
            )
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".runtime-download-", dir=os.path.dirname(self.path)
            ) as temporary:
                archive = os.path.join(temporary, os.path.basename(asset["name"]))
                log(_("Downloading {name}").format(name=asset["name"]) + "\n")
                self._check_cancelled(task)
                with requests.get(asset["url"], stream=True, timeout=300) as response:
                    response.raise_for_status()
                    total = int(
                        response.headers.get("content-length", asset.get("size", 0))
                    )
                    downloaded = 0
                    with open(archive, "wb") as output:
                        for chunk in response.iter_content(chunk_size=8192):
                            self._check_cancelled(task)
                            output.write(chunk)
                            downloaded += len(chunk)
                            task.update(
                                fraction=downloaded / total if total else None,
                                transferred_bytes=downloaded,
                                total_bytes=total or None,
                            )
                            if total:
                                progress(min(downloaded / total, 1.0) * 0.7)
                self._check_cancelled(task)
                progress(0.7)
                log(_("Download complete. Extracting...") + "\n")
                task.update(phase=_("Installing runtime"), reset_progress=True)
                extracted = os.path.join(temporary, "extracted")
                os.makedirs(extracted)
                self._extract(archive, extracted)
                source = os.path.join(temporary, "runtime")
                binary_dir = os.path.join(source, "build", "bin")
                os.makedirs(binary_dir)
                self._copy_binaries(extracted, binary_dir)
                self._validate_binaries(binary_dir)
                self._check_cancelled(task)
                task.update(cancellable=False)
                self._publish(source, temporary)
            progress(1.0)
            log(_("Installation completed successfully!") + "\n")
            task.complete(_("Installed"))
        except (BuildCancelled, DownloadCancelled):
            if task is not None:
                task.cancelled(_("Cancelled"))
            raise
        except Exception as error:
            if task is not None:
                task.fail(str(error), _("Failed"))
            raise
        finally:
            self._lock.release()

    def _validate_binaries(self, binary_dir):
        for original, target in self.project.binary_aliases:
            old_path = os.path.join(binary_dir, original)
            new_path = os.path.join(binary_dir, target)
            if os.path.isfile(old_path) and not os.path.exists(new_path):
                os.rename(old_path, new_path)
        found = [
            os.path.isfile(os.path.join(binary_dir, name))
            for name in self.project.binaries
        ]
        if not (all(found) if self.project.require_all_binaries else any(found)):
            raise RuntimeError(
                _("Required binaries not found: {binaries}").format(
                    binaries=", ".join(self.project.binaries)
                )
            )
        for name, exists in zip(self.project.binaries, found):
            if exists:
                target = os.path.join(binary_dir, name)
                os.chmod(target, os.stat(target).st_mode | 0o111)

    def _publish(self, source, temporary):
        """Keep the previous runtime until the replacement has been validated."""
        previous = os.path.join(temporary, "previous")
        preserved_paths = []
        if os.path.exists(self.path):
            os.rename(self.path, previous)
        try:
            for pattern in self.project.preserve_patterns:
                for old_path in Path(previous).glob(pattern):
                    new_path = Path(source) / old_path.relative_to(previous)
                    if any(old_path == old for old, _new in preserved_paths):
                        continue
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(old_path, new_path)
                    preserved_paths.append((old_path, new_path))
            os.rename(source, self.path)
        except OSError:
            for old_path, new_path in reversed(preserved_paths):
                shutil.move(new_path, old_path)
            if os.path.exists(previous):
                os.rename(previous, self.path)
            raise

    @staticmethod
    def _extract(archive, destination):
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as contents:
                root = os.path.realpath(destination)
                for member in contents.infolist():
                    target = os.path.realpath(os.path.join(root, member.filename))
                    if os.path.commonpath((root, target)) != root:
                        raise ValueError(_("Invalid path in runtime archive"))
                contents.extractall(destination)
        else:
            with tarfile.open(archive) as contents:
                contents.extractall(destination, filter="data")

    def _copy_binaries(self, source, destination):
        # Preserve the complete binary directory, including shared libraries,
        # symlinks, and backend resources shipped beside the executable.
        names = self.project.binaries + tuple(
            original for original, _target in self.project.binary_aliases
        )
        found = False
        libraries = []
        for root, _dirs, files in os.walk(source):
            if any(name in files for name in names):
                shutil.copytree(root, destination, dirs_exist_ok=True, symlinks=True)
                found = True
            libraries.extend(
                os.path.join(root, name)
                for name in files
                if name.endswith(".so") or ".so." in name
            )
        if not found:
            raise RuntimeError(_("No runtime binary found in the archive"))
        for library in libraries:
            target = os.path.join(destination, os.path.basename(library))
            if not os.path.lexists(target):
                shutil.copy2(library, target, follow_symlinks=False)

    def fetch_versions(self):
        if self.project.fetch_versions is not None:
            return self.project.fetch_versions()
        return list(self.project.versions)

    def fetch_releases(self):
        if self.project.fetch_releases is not None:
            return self.project.fetch_releases()
        return []
