#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
SEED_LIBRARIES = (
    "libgirepository-2.0.0.dylib",
    "libgtk-4.1.dylib",
    "libadwaita-1.0.dylib",
    "libgtksourceview-5.0.dylib",
    "libvte-2.91-gtk4.0.dylib",
    "libgdk_pixbuf-2.0.0.dylib",
    "librsvg-2.2.dylib",
)


def run(*args: str, capture: bool = False) -> str:
    if capture:
        return subprocess.check_output(args, text=True)
    subprocess.check_call(args)
    return ""


def is_macho(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACHO_MAGICS
    except OSError:
        return False


def otool_dependencies(path: Path) -> list[str]:
    output = run("otool", "-L", str(path), capture=True)
    dependencies: list[str] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        dependencies.append(line.split(" (", 1)[0])
    return dependencies


def otool_id(path: Path) -> str | None:
    try:
        output = run("otool", "-D", str(path), capture=True)
    except subprocess.CalledProcessError:
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    return lines[1]


def otool_rpaths(path: Path) -> list[str]:
    output = run("otool", "-l", str(path), capture=True)
    rpaths: list[str] = []
    current_cmd: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            current_cmd = stripped.split(None, 1)[1]
            continue
        if current_cmd == "LC_RPATH" and stripped.startswith("path "):
            rpaths.append(stripped.split(" (offset ", 1)[0].split("path ", 1)[1])
    return rpaths


def relative_loader_ref(binary: Path, target: Path) -> str:
    rel = os.path.relpath(target, binary.parent)
    return f"@loader_path/{rel}"


class RuntimeBundler:
    def __init__(self, app_dir: Path, brew_prefix: Path, python_version: str) -> None:
        self.app_dir = app_dir.resolve()
        self.brew_prefix = brew_prefix.resolve()
        self.python_version = python_version
        self.contents_dir = self.app_dir / "Contents"
        self.frameworks_dir = self.contents_dir / "Frameworks"
        self.resources_dir = self.contents_dir / "Resources"
        self.gdk_pixbuf_loaders_dir = self.resources_dir / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
        self.python_source_home = (
            self.brew_prefix / "opt" / f"python@{python_version}" / "Frameworks" / "Python.framework" / "Versions" / python_version
        ).resolve()
        self.python_dest_home = self.frameworks_dir / "Python.framework" / "Versions" / python_version
        self.python_dest_main = self.python_dest_home / "Python"
        self.python_dest_site_packages = self.python_dest_home / "lib" / f"python{python_version}" / "site-packages"
        self.copied_external: dict[Path, Path] = {}
        self._site_packages_filename_cache: dict[str, Path | None] = {}

    def ensure_seed_libraries(self) -> None:
        for name in SEED_LIBRARIES:
            source = self.resolve_brew_library(name)
            if source is None:
                raise FileNotFoundError(f"Unable to resolve required library: {name}")
            self.copy_external_library(source)

    def resolve_brew_library(self, name: str) -> Path | None:
        direct = (self.brew_prefix / "lib" / name)
        if direct.exists():
            return direct.resolve()
        for candidate in sorted((self.brew_prefix / "opt").glob(f"*/lib/{name}")):
            if candidate.exists():
                return candidate.resolve()
        return None

    def copy_external_library(self, source: Path) -> Path:
        source = source.resolve()
        if source in self.copied_external:
            return self.copied_external[source]
        destination = self.frameworks_dir / source.name
        shutil.copy2(source, destination, follow_symlinks=True)
        destination.chmod(0o755)
        self.copied_external[source] = destination
        return destination

    def _site_packages_relative_path(self, dependency: str) -> Path | None:
        marker = f"/lib/python{self.python_version}/site-packages/"
        if marker not in dependency:
            return None
        return Path(dependency.split(marker, 1)[1])

    def _candidate_site_package_names(self, filename: str) -> list[str]:
        parts = filename.split(".")
        candidates = [filename]
        for index in range(1, max(1, len(parts) - 2)):
            candidate = ".".join(parts[index:])
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _find_site_packages_by_filename(self, filename: str) -> Path | None:
        if filename not in self._site_packages_filename_cache:
            match = next(self.python_dest_site_packages.rglob(filename), None)
            self._site_packages_filename_cache[filename] = match
        return self._site_packages_filename_cache[filename]

    def resolve_site_packages_target(self, dependency: str) -> Path | None:
        relative_path = self._site_packages_relative_path(dependency)
        if relative_path is not None:
            direct_target = self.python_dest_site_packages / relative_path
            if direct_target.exists():
                return direct_target

            target_dir = direct_target.parent
            if target_dir.exists():
                for candidate_name in self._candidate_site_package_names(relative_path.name):
                    candidate = target_dir / candidate_name
                    if candidate.exists():
                        return candidate

        filename = dependency.rsplit("/", 1)[-1]
        for candidate_name in self._candidate_site_package_names(filename):
            candidate = self._find_site_packages_by_filename(candidate_name)
            if candidate is not None and candidate.exists():
                return candidate
        return None

    def site_packages_install_name(self, binary: Path) -> str | None:
        binary = binary.resolve()
        try:
            relative = binary.relative_to(self.python_dest_site_packages)
        except ValueError:
            return None

        package_parts = list(relative.parts[:-1])
        filename = relative.name
        package_prefix = ".".join(package_parts)
        if package_prefix and not filename.startswith(f"{package_prefix}."):
            filename = f"{package_prefix}.{filename}"
        return f"@rpath/{filename}"

    def resolve_dependency_target(self, dependency: str) -> Path | None:
        if dependency.startswith(SYSTEM_PREFIXES):
            return None

        site_packages_target = self.resolve_site_packages_target(dependency)
        if site_packages_target is not None:
            return site_packages_target

        if dependency.endswith(f"Python.framework/Versions/{self.python_version}/Python") or dependency == "@rpath/Python":
            return self.python_dest_main

        if dependency.startswith("@rpath/"):
            name = dependency.rsplit("/", 1)[-1]
            if name == "Python":
                return self.python_dest_main
            bundled = self.frameworks_dir / name
            if bundled.exists():
                return bundled
            source = self.resolve_brew_library(name)
            return self.copy_external_library(source) if source else None

        if dependency.startswith("@loader_path/") or dependency.startswith("@executable_path/"):
            name = dependency.rsplit("/", 1)[-1]
            if name == "Python":
                return self.python_dest_main
            bundled = self.frameworks_dir / name
            if bundled.exists():
                return bundled
            source = self.resolve_brew_library(name)
            return self.copy_external_library(source) if source else None

        source = Path(dependency)
        if not source.is_absolute():
            return None
        if source.exists():
            source = source.resolve()
        elif dependency.startswith(str(self.brew_prefix)):
            resolved = self.resolve_brew_library(source.name)
            source = resolved if resolved else source
        else:
            return None

        if not source.exists():
            return None

        source_str = str(source)
        if "/gdk-pixbuf-2.0/2.10.0/loaders/" in source_str:
            bundled_loader = self.gdk_pixbuf_loaders_dir / source.name
            if bundled_loader.exists():
                return bundled_loader

        try:
            relative_to_python = source.relative_to(self.python_source_home)
            return self.python_dest_home / relative_to_python
        except ValueError:
            pass

        if str(source).startswith(str(self.brew_prefix)):
            return self.copy_external_library(source)
        return None

    def desired_library_id(self, binary: Path) -> str | None:
        if binary == self.python_dest_main:
            return f"@rpath/Python.framework/Versions/{self.python_version}/Python"
        if binary.suffix == ".so" and binary.parent == self.gdk_pixbuf_loaders_dir:
            sibling_dylib = binary.with_suffix(".dylib")
            if sibling_dylib.exists():
                return f"@loader_path/{sibling_dylib.name}"
            return f"@loader_path/{binary.name}"
        if binary.suffix == ".so":
            site_packages_id = self.site_packages_install_name(binary)
            if site_packages_id is not None:
                return site_packages_id
        if binary.suffix != ".dylib":
            return None
        if binary.is_relative_to(self.frameworks_dir):
            return f"@rpath/{binary.name}"
        return f"@loader_path/{binary.name}"

    def patch_binary(self, binary: Path) -> bool:
        changed = False

        current_id = otool_id(binary)
        desired_id = self.desired_library_id(binary)
        if current_id and desired_id and current_id != desired_id:
            run("install_name_tool", "-id", desired_id, str(binary))
            changed = True

        for dependency in otool_dependencies(binary):
            if current_id and dependency == current_id:
                continue
            target = self.resolve_dependency_target(dependency)
            if target is None:
                continue
            new_ref = relative_loader_ref(binary, target)
            if dependency == new_ref:
                continue
            run("install_name_tool", "-change", dependency, new_ref, str(binary))
            changed = True

        for rpath in otool_rpaths(binary):
            if not rpath.startswith(str(self.brew_prefix)):
                continue
            run("install_name_tool", "-delete_rpath", rpath, str(binary))
            changed = True
        return changed

    def all_macho_files(self) -> list[Path]:
        return sorted(path for path in self.app_dir.rglob("*") if is_macho(path))

    def patch_all(self) -> None:
        self.ensure_seed_libraries()
        for _ in range(12):
            changed = False
            for binary in self.all_macho_files():
                changed = self.patch_binary(binary) or changed
            if not changed:
                break

    def remaining_homebrew_refs(self) -> list[tuple[Path, str]]:
        remaining: list[tuple[Path, str]] = []
        for binary in self.all_macho_files():
            current_id = otool_id(binary)
            if current_id and current_id.startswith(str(self.brew_prefix)):
                remaining.append((binary, current_id))
            for dependency in otool_dependencies(binary):
                if current_id and dependency == current_id:
                    continue
                if dependency.startswith(str(self.brew_prefix)):
                    remaining.append((binary, dependency))
            for rpath in otool_rpaths(binary):
                if rpath.startswith(str(self.brew_prefix)):
                    remaining.append((binary, rpath))
        return remaining


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: package_runtime.py <app-dir> <brew-prefix> <python-version>", file=sys.stderr)
        return 2

    app_dir = Path(sys.argv[1])
    brew_prefix = Path(sys.argv[2])
    python_version = sys.argv[3]

    bundler = RuntimeBundler(app_dir, brew_prefix, python_version)
    bundler.patch_all()
    remaining = bundler.remaining_homebrew_refs()
    if remaining:
        for binary, dependency in remaining:
            print(f"Unpatched dependency: {binary} -> {dependency}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
