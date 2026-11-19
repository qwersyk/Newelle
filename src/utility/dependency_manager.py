"""Inspect and maintain the optional Python packages installed in the pip path.

Newelle installs optional packages with ``pip install --target``. pip can
neither list outdated packages nor uninstall from a target directory, and
``--upgrade`` leaves the previous ``*.dist-info`` folders behind, so this module
reads and edits the package metadata directly.
"""

from __future__ import annotations

import csv
import gettext
import importlib
import inspect
import json
import os
import re
import shlex
import shutil
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.parser import HeaderParser

from .download_manager import DownloadKind, get_download_manager
from .pip import install_module, pip_path_lock

_ = gettext.gettext

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
_NAME_PATTERN = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_EGG_PATTERN = re.compile(r"#egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
_PACKAGING = None


def normalize_name(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(requirements: str) -> list[str]:
    """Extract normalized distribution names from a pip requirement string.

    The string may hold several space separated requirements, as accepted by
    :func:`install_module`. Options and unnamed URLs are ignored.
    """
    try:
        tokens = shlex.split(requirements)
    except ValueError:
        tokens = requirements.split()
    names = []
    for token in tokens:
        if token.startswith("-"):
            continue
        if " @ " in token:
            token = token.split(" @ ", 1)[0]
        elif "://" in token or token.startswith(("git+", "file:")):
            match = _EGG_PATTERN.search(token)
            if match:
                names.append(normalize_name(match.group(1)))
            continue
        match = _NAME_PATTERN.match(token)
        if match:
            names.append(normalize_name(match.group(1)))
    return names


def _packaging():
    """Return the ``(requirements, version)`` modules of packaging, if any."""
    global _PACKAGING
    if _PACKAGING is None:
        _PACKAGING = False
        for prefix in ("packaging", "pip._vendor.packaging"):
            try:
                _PACKAGING = (
                    importlib.import_module(prefix + ".requirements"),
                    importlib.import_module(prefix + ".version"),
                )
                break
            except Exception:  # noqa: BLE001, S112 - any import failure means unavailable
                continue
    return _PACKAGING or None


def _parse_version(value: str):
    packaging = _packaging()
    if packaging is None or not value:
        return None
    try:
        return packaging[1].Version(value)
    except Exception:  # noqa: BLE001 - InvalidVersion differs between vendored copies
        return None


def is_newer_version(candidate: str | None, current: str | None) -> bool:
    """Return whether ``candidate`` is a newer release than ``current``."""
    if not candidate or not current:
        return False
    parsed_candidate = _parse_version(candidate)
    parsed_current = _parse_version(current)
    if parsed_candidate is not None and parsed_current is not None:
        return parsed_candidate > parsed_current
    return candidate != current


def _required_name(line: str, extras: list[str]) -> str | None:
    """Return the dependency named by a ``Requires-Dist`` line, if it applies.

    Which extras were requested at install time is not recorded, so a
    dependency behind any extra counts as required. This keeps unused-package
    detection from ever suggesting a package that might still be needed.
    """
    packaging = _packaging()
    if packaging is None:
        names = requirement_names(line.split(";", 1)[0])
        return names[0] if names else None
    try:
        requirement = packaging[0].Requirement(line)
    except Exception:  # noqa: BLE001 - malformed metadata must not break scanning
        names = requirement_names(line.split(";", 1)[0])
        return names[0] if names else None
    if requirement.marker is not None:
        applies = False
        for extra in ["", *extras]:
            try:
                if requirement.marker.evaluate({"extra": extra}):
                    applies = True
                    break
            except Exception:  # noqa: BLE001 - undefined marker names
                applies = True
                break
        if not applies:
            return None
    return normalize_name(requirement.name)


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root and path != root
    except ValueError:
        return False


def _resolve_record_path(root: str, relative: str) -> str | None:
    """Map a RECORD entry to an absolute path inside ``root``.

    ``pip --target`` builds into a temporary prefix, so console scripts are
    recorded as ``../../bin/<name>`` although they end up in ``<root>/bin``.
    """
    candidate = os.path.normpath(os.path.join(root, relative))
    if not _is_within(candidate, root):
        parts = relative.replace("\\", "/").split("/")
        while parts and parts[0] in ("..", "."):
            parts.pop(0)
        if not parts or parts[0] != "bin":
            return None
        candidate = os.path.normpath(os.path.join(root, *parts))
        if not _is_within(candidate, root):
            return None
    real_parent = os.path.realpath(os.path.dirname(candidate))
    real_root = os.path.realpath(root)
    if real_parent != real_root and not _is_within(real_parent, real_root):
        return None
    return candidate


@dataclass
class DistInfo:
    """One ``*.dist-info`` folder found in the pip path."""

    path: str
    name: str
    version: str
    summary: str
    requested: bool
    requires: set[str]
    files: set[str]
    installed_at: float


@dataclass
class InstalledPackage:
    """A distribution in the pip path, merged across leftover metadata."""

    key: str
    name: str
    version: str
    summary: str
    current: DistInfo
    stale: list[DistInfo] = field(default_factory=list)
    requested: bool = False
    requires: set[str] = field(default_factory=set)
    required_by: set[str] = field(default_factory=set)
    used_by: list[str] = field(default_factory=list)
    size: int = 0

    @property
    def is_top_level(self) -> bool:
        """Whether the package was asked for, not pulled in as a dependency."""
        return self.requested or bool(self.used_by)

    @property
    def files(self) -> set[str]:
        files = set(self.current.files)
        for dist in self.stale:
            files |= dist.files
        return files

    @property
    def dist_info_paths(self) -> list[str]:
        return [self.current.path, *(dist.path for dist in self.stale)]


@dataclass
class DependencySnapshot:
    """Point-in-time view of the packages installed in a pip path."""

    path: str
    packages: dict[str, InstalledPackage]

    def sorted_packages(self) -> list[InstalledPackage]:
        return sorted(self.packages.values(), key=lambda p: p.name.lower())

    def top_level(self) -> list[InstalledPackage]:
        return [p for p in self.sorted_packages() if p.is_top_level]

    def dependencies(self) -> list[InstalledPackage]:
        return [p for p in self.sorted_packages() if not p.is_top_level]

    @property
    def total_size(self) -> int:
        return sum(package.size for package in self.packages.values())

    def stale_metadata(self) -> list[str]:
        return [dist.path for p in self.packages.values() for dist in p.stale]

    def _reachable(self, roots, available) -> set[str]:
        reachable = set()
        pending = [key for key in roots if key in available]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            pending.extend(
                dep for dep in self.packages[key].requires
                if dep in available and dep not in reachable
            )
        return reachable

    def unused(self) -> list[InstalledPackage]:
        """Dependencies that no requested package needs anymore."""
        available = set(self.packages)
        roots = [p.key for p in self.packages.values() if p.is_top_level]
        reachable = self._reachable(roots, available)
        return [p for p in self.sorted_packages() if p.key not in reachable]

    def dependents(self, keys: set[str]) -> list[InstalledPackage]:
        """Installed packages, outside ``keys``, that require one of ``keys``."""
        return [
            p for p in self.sorted_packages()
            if p.key not in keys and p.requires & keys
        ]

    def orphaned_by_removal(self, keys: set[str]) -> list[InstalledPackage]:
        """Dependencies of ``keys`` that nothing else needs once they are gone."""
        remaining = set(self.packages) - keys
        roots = [k for k in remaining if self.packages[k].is_top_level]
        reachable = self._reachable(roots, remaining)
        pulled_in = self._reachable(keys, set(self.packages)) - keys
        return [
            p for p in self.sorted_packages()
            if p.key in pulled_in and p.key not in reachable and not p.is_top_level
        ]


def _read_record(dist_path: str, root: str) -> set[str]:
    files = set()
    record = os.path.join(dist_path, "RECORD")
    try:
        with open(record, newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if not row:
                    continue
                resolved = _resolve_record_path(root, row[0])
                if resolved is not None and not _is_within(resolved, dist_path):
                    files.add(resolved)
    except OSError:
        pass
    return files


def _read_dist_info(dist_path: str, root: str) -> DistInfo | None:
    try:
        with open(os.path.join(dist_path, "METADATA"), encoding="utf-8", errors="replace") as handle:
            metadata = HeaderParser().parsestr(handle.read())
    except OSError:
        return None
    name = metadata.get("Name")
    if not name:
        return None
    extras = [extra.strip() for extra in metadata.get_all("Provides-Extra") or []]
    requires = set()
    for line in metadata.get_all("Requires-Dist") or []:
        required = _required_name(line, extras)
        if required:
            requires.add(required)
    record = os.path.join(dist_path, "RECORD")
    try:
        installed_at = os.stat(record if os.path.exists(record) else dist_path).st_mtime
    except OSError:
        installed_at = 0.0
    return DistInfo(
        path=dist_path,
        name=name.strip(),
        version=(metadata.get("Version") or "").strip(),
        summary=(metadata.get("Summary") or "").strip(),
        requested=os.path.exists(os.path.join(dist_path, "REQUESTED")),
        requires=requires,
        files=_read_record(dist_path, root),
        installed_at=installed_at,
    )


def _files_size(files) -> int:
    size = 0
    for path in files:
        try:
            size += os.lstat(path).st_size
        except OSError:
            continue
    return size


def scan(path: str, used_by: dict[str, list[str]] | None = None,
         with_sizes: bool = True) -> DependencySnapshot:
    """Read every package installed in ``path``."""
    root = os.path.abspath(path)
    used_by = used_by or {}
    grouped: dict[str, list[DistInfo]] = {}
    try:
        entries = os.listdir(root)
    except OSError:
        entries = []
    for entry in entries:
        dist_path = os.path.join(root, entry)
        if not entry.endswith(".dist-info") or not os.path.isdir(dist_path):
            continue
        dist = _read_dist_info(dist_path, root)
        if dist is not None:
            grouped.setdefault(normalize_name(dist.name), []).append(dist)

    packages = {}
    for key, dists in grouped.items():
        # pip --target --upgrade replaces the package files but keeps the old
        # dist-info folder, so the most recently written metadata is current.
        dists.sort(key=lambda d: d.installed_at, reverse=True)
        current = dists[0]
        package = InstalledPackage(
            key=key,
            name=current.name,
            version=current.version,
            summary=current.summary,
            current=current,
            stale=dists[1:],
            requested=any(d.requested for d in dists),
            requires=set(current.requires) - {key},
            used_by=list(used_by.get(key, [])),
        )
        if with_sizes:
            package.size = _files_size(current.files) + _files_size(
                os.path.join(current.path, name) for name in _safe_listdir(current.path)
            )
        packages[key] = package

    for package in packages.values():
        for dependency in package.requires:
            if dependency in packages:
                packages[dependency].required_by.add(package.key)
    return DependencySnapshot(path=root, packages=packages)


def _safe_listdir(path: str) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def _remove_file(path: str, touched: set[str]) -> None:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            return
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"Could not remove {path}: {error}")
        return
    parent = os.path.dirname(path)
    touched.add(parent)
    stem, extension = os.path.splitext(os.path.basename(path))
    if extension == ".py":
        cache = os.path.join(parent, "__pycache__")
        for cached in _safe_listdir(cache):
            if cached.startswith(stem + ".") and cached.endswith((".pyc", ".pyo")):
                try:
                    os.unlink(os.path.join(cache, cached))
                except OSError:
                    pass
        touched.add(cache)


def _prune_directories(root: str, touched: set[str]) -> None:
    for directory in sorted(touched, key=lambda d: d.count(os.sep), reverse=True):
        current = directory
        while _is_within(current, root):
            cache = os.path.join(current, "__pycache__")
            if os.path.isdir(cache) and not _safe_listdir(cache):
                try:
                    os.rmdir(cache)
                except OSError:
                    pass
            try:
                os.rmdir(current)
            except OSError:
                break
            current = os.path.dirname(current)


def _remove_dist_info(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def remove_packages(path: str, keys) -> list[str]:
    """Uninstall the packages with the given normalized names from ``path``.

    Files that another remaining package also lists are kept. Returns the
    display names of the removed packages.
    """
    root = os.path.abspath(path)
    keys = {normalize_name(key) for key in keys}
    with pip_path_lock(root):
        snapshot = scan(root, with_sizes=False)
        targets = [snapshot.packages[key] for key in keys if key in snapshot.packages]
        kept_files = set()
        for package in snapshot.packages.values():
            if package.key not in keys:
                kept_files |= package.files
        touched = set()
        for package in targets:
            for file_path in package.files - kept_files:
                _remove_file(file_path, touched)
            for dist_path in package.dist_info_paths:
                _remove_dist_info(dist_path)
        _prune_directories(root, touched)
    importlib.invalidate_caches()
    return [package.name for package in targets]


def clean_stale_metadata(path: str) -> int:
    """Delete metadata and files left behind by replaced package versions.

    Returns the number of removed metadata folders.
    """
    root = os.path.abspath(path)
    with pip_path_lock(root):
        snapshot = scan(root, with_sizes=False)
        current_files = set()
        for package in snapshot.packages.values():
            current_files |= package.current.files
        touched = set()
        removed = 0
        for package in snapshot.packages.values():
            if not package.stale:
                continue
            if package.requested and not package.current.requested:
                try:
                    open(os.path.join(package.current.path, "REQUESTED"), "a").close()
                except OSError:
                    pass
            for dist in package.stale:
                for file_path in dist.files - current_files:
                    _remove_file(file_path, touched)
                _remove_dist_info(dist.path)
                removed += 1
        _prune_directories(root, touched)
    importlib.invalidate_caches()
    return removed


def remove_all(path: str) -> None:
    """Delete every package installed in ``path``, keeping the empty folder."""
    root = os.path.abspath(path)
    with pip_path_lock(root):
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
    importlib.invalidate_caches()


def install_requirements(path: str, requirements: str, title: str | None = None) -> None:
    """Install or upgrade ``requirements`` in ``path`` as a tracked download.

    Raises ``RuntimeError`` with pip's output tail when the installation fails.
    """
    manager = get_download_manager()
    with manager.operation(
        title or _("Install {name}").format(name=requirements),
        kind=DownloadKind.DEPENDENCY,
        source_id=f"dependency-manager:{os.path.abspath(path)}:{requirements}",
        phase=_("Waiting to install"),
    ) as task:
        result = install_module(requirements, path, update=True, task=task)
        if result is None or result.returncode != 0:
            detail = (getattr(result, "stdout", "") or "").strip()
            raise RuntimeError(detail[-1000:] or _("pip could not install the package"))
        task.update(phase=_("Cleaning up old versions"), reset_progress=True)
        clean_stale_metadata(path)
    importlib.invalidate_caches()


def update_packages(path: str, names: list[str]) -> None:
    """Upgrade the given distributions to their latest compatible versions."""
    if not names:
        return
    title = (
        _("Update {name}").format(name=names[0])
        if len(names) == 1
        else _("Update {count} packages").format(count=len(names))
    )
    install_requirements(path, shlex.join(names), title)


def _latest_version(name: str, timeout: float) -> str | None:
    url = PYPI_JSON_URL.format(name=urllib.parse.quote(name))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Newelle"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except Exception:  # noqa: BLE001 - offline or unknown package
        return None
    version = data.get("info", {}).get("version")
    return version if isinstance(version, str) and version else None


def fetch_latest_versions(names: list[str], timeout: float = 10) -> dict[str, str]:
    """Return the latest PyPI release for each name, keyed by normalized name.

    Packages that could not be queried are missing from the result.
    """
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        versions = pool.map(lambda name: _latest_version(name, timeout), names)
        return {
            normalize_name(name): version
            for name, version in zip(names, versions)
            if version is not None
        }


def _declared_requirements(owner) -> list[str]:
    try:
        if isinstance(owner, type):
            method = inspect.getattr_static(owner, "get_extra_requirements", None)
            if method is None:
                return []
            if isinstance(method, (staticmethod, classmethod)):
                requirements = owner.get_extra_requirements()
            else:
                # Some handlers declare it as an instance method that ignores self.
                requirements = owner.get_extra_requirements(owner.__new__(owner))
        else:
            requirements = owner.get_extra_requirements()
    except Exception:  # noqa: BLE001 - third-party handlers may misbehave
        return []
    names = []
    for requirement in requirements or []:
        if isinstance(requirement, str):
            names.extend(requirement_names(requirement))
    return names


def collect_requirement_owners(controller=None) -> dict[str, list[str]]:
    """Map normalized package names to the providers and extensions that declare them."""
    from .. import constants

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
    owners: dict[str, list[str]] = {}

    def add(names, label):
        for name in names:
            labels = owners.setdefault(name, [])
            if label not in labels:
                labels.append(label)

    for attribute, category in categories:
        handlers = getattr(constants, attribute, {}) or {}
        for descriptor in list(handlers.values()):
            handler_class = descriptor.get("class")
            if handler_class is None or descriptor.get("duplicated", False):
                continue
            title = descriptor.get("title") or descriptor.get("key", "")
            add(_declared_requirements(handler_class), f"{title} ({category})")

    loaders = [
        getattr(controller, "extensionloader", None),
        getattr(controller, "integrationsloader", None),
    ]
    for loader in loaders:
        if loader is None:
            continue
        try:
            extensions = list(loader.get_extensions())
        except Exception:  # noqa: BLE001, S112 - a broken loader just contributes nothing
            continue
        for extension in extensions:
            name = getattr(extension, "name", None) or getattr(extension, "id", "")
            add(_declared_requirements(extension), f"{name} ({_('Extension')})")
    return owners
