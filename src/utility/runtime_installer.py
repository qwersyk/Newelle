"""Shared source and binary installation for the local C++ runtimes.

This module performs synchronous work. Callers choose the worker thread and
marshal the optional log/progress callbacks onto their UI's main loop.
"""

import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
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


MANIFEST_NAME = ".newelle-runtime.json"


def read_runtime_manifest(path):
    """Return how the runtime in ``path`` was installed, or ``None``.

    Runtimes installed before manifests existed have no manifest.
    """
    try:
        with open(os.path.join(path, MANIFEST_NAME), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _write_manifest(directory, **values):
    values["installed_at"] = time.time()
    with open(os.path.join(directory, MANIFEST_NAME), "w", encoding="utf-8") as handle:
        json.dump(values, handle, indent=2)


def _remove_except(root: Path, keep: set[Path]) -> None:
    for entry in list(root.iterdir()):
        if entry in keep:
            continue
        if any(entry in path.parents for path in keep):
            _remove_except(entry, keep)
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


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
                _write_manifest(
                    source,
                    method="source",
                    backend=backend,
                    version=version or self._git_output(source, "describe", "--tags", "--always"),
                    requested_version=version,
                    commit=self._git_output(source, "rev-parse", "HEAD"),
                    custom_flags=list(
                        shlex.split(custom_flags)
                        if isinstance(custom_flags, str)
                        else custom_flags
                    ),
                )
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
        manager = get_download_manager()
        # A caller that already tracks this work (such as a dependency update)
        # keeps a single entry in the downloads list.
        owns_task = manager.current_task() is None
        try:
            if owns_task:
                task = manager.create_task(
                    _("Install {name}").format(name=asset["name"]),
                    kind=DownloadKind.RUNTIME,
                    source_id=source_id,
                    phase=_("Downloading runtime"),
                    cancellable=True,
                )
            else:
                task = manager.current_task()
                task.update(phase=_("Downloading runtime"), reset_progress=True)
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
                _write_manifest(
                    source,
                    method="prebuilt",
                    backend=asset.get("backend"),
                    version=asset.get("tag"),
                    asset=asset.get("name"),
                    source=asset.get("source", "official"),
                    cuda_version=asset.get("cuda_version"),
                )
                self._check_cancelled(task)
                task.update(cancellable=False)
                self._publish(source, temporary)
            progress(1.0)
            log(_("Installation completed successfully!") + "\n")
            if owns_task:
                task.complete(_("Installed"))
        except (BuildCancelled, DownloadCancelled):
            if task is not None and owns_task:
                task.cancelled(_("Cancelled"))
            raise
        except Exception as error:
            if task is not None and owns_task:
                task.fail(str(error), _("Failed"))
            raise
        finally:
            self._lock.release()

    def _git_output(self, source, *arguments):
        """Return the last output line of a git command, or ``None`` on failure."""
        output = []
        try:
            succeeded = self.process.run(
                get_spawn_command() + ["git", *arguments],
                cwd=source,
                on_output=output.append,
            )
        except OSError:
            return None
        lines = "".join(output).strip().splitlines()
        return lines[-1].strip() if succeeded and lines else None

    def fetch_remote_commit(self):
        """Return the commit at the head of the default branch upstream."""
        # Not run through self.process: that runner tracks one build at a time.
        try:
            result = subprocess.run(
                get_spawn_command() + ["git", "ls-remote", self.project.repository, "HEAD"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = result.stdout.split() if result.returncode == 0 else []
        return fields[0] if fields else None

    def uninstall(self):
        """Delete the installed runtime, keeping ``preserve_patterns`` matches.

        Raises ``RuntimeError`` while an installation of this runtime runs.
        """
        if not self._lock.acquire(blocking=False):
            raise RuntimeError(_("An installation is already running"))
        try:
            root = Path(self.path)
            if not root.exists():
                return
            keep = set()
            for pattern in self.project.preserve_patterns:
                keep.update(root.glob(pattern))
            if keep:
                _remove_except(root, keep)
            else:
                shutil.rmtree(root)
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
