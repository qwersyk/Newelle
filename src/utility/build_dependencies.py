"""Host build dependency checks for the bundled C++ backends.

The source builds run on the host when Newelle is packaged as a Flatpak, so
checking binaries inside the Flatpak runtime would produce misleading results.
This module keeps the checks independent from GTK so they can also be tested.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .system import get_spawn_command, is_flatpak


@dataclass(frozen=True)
class BuildDependency:
    key: str
    label: str
    probe: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class LinuxDistribution:
    family: str
    name: str


COMMON_DEPENDENCIES = (
    BuildDependency("git", "Git", "command", ("git",)),
    BuildDependency("cmake", "CMake", "command", ("cmake",)),
    BuildDependency(
        "c_compiler",
        "C compiler",
        "command",
        ("cc", "gcc", "clang", "icx"),
    ),
    BuildDependency(
        "cpp_compiler",
        "C++ compiler",
        "command",
        ("c++", "g++", "clang++", "icpx"),
    ),
    BuildDependency(
        "build_tool",
        "Make or Ninja",
        "command",
        ("make", "gmake", "ninja", "ninja-build"),
    ),
)

BACKEND_DEPENDENCIES = {
    "cpu": (),
    "cpu_openblas": (
        BuildDependency(
            "openblas",
            "OpenBLAS development files",
            "pkg-config",
            ("openblas",),
        ),
    ),
    "cuda": (
        BuildDependency(
            "cuda",
            "CUDA compiler (nvcc)",
            "command",
            ("nvcc", "/opt/cuda/bin/nvcc", "/usr/local/cuda/bin/nvcc"),
        ),
    ),
    "rocm": (
        BuildDependency(
            "rocm",
            "ROCm HIP compiler (hipcc)",
            "command",
            ("hipcc", "/opt/rocm/bin/hipcc"),
        ),
    ),
    "vulkan": (
        BuildDependency(
            "vulkan",
            "Vulkan development files",
            "pkg-config",
            ("vulkan",),
        ),
        BuildDependency(
            "glslc",
            "Vulkan shader compiler (glslc)",
            "command",
            ("glslc",),
        ),
    ),
    "openvino": (
        BuildDependency(
            "openvino",
            "OpenVINO toolkit",
            "command",
            ("benchmark_app", "compile_tool"),
        ),
    ),
    "sycl-fp32": (
        BuildDependency("sycl_c", "Intel oneAPI C compiler (icx)", "command", ("icx",)),
        BuildDependency("sycl_cpp", "Intel oneAPI C++ compiler (icpx)", "command", ("icpx",)),
    ),
    "sycl-fp16": (
        BuildDependency("sycl_c", "Intel oneAPI C compiler (icx)", "command", ("icx",)),
        BuildDependency("sycl_cpp", "Intel oneAPI C++ compiler (icpx)", "command", ("icpx",)),
    ),
}


_DISTRO_ALIASES = {
    "debian": {
        "debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin",
        "kali", "raspbian", "neon",
    },
    "fedora": {
        "fedora", "rhel", "centos", "rocky", "almalinux", "nobara",
    },
    "arch": {"arch", "manjaro", "endeavouros", "garuda"},
    "opensuse": {
        "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "suse",
    },
    "alpine": {"alpine"},
}

_INSTALL_COMMANDS = {
    "debian": ("sudo apt install", {
        "git": ("git",),
        "cmake": ("cmake",),
        "c_compiler": ("build-essential",),
        "cpp_compiler": ("build-essential",),
        "build_tool": ("build-essential",),
        "openblas": ("pkg-config", "libopenblas-dev"),
        "vulkan": ("pkg-config", "libvulkan-dev"),
        "glslc": ("glslc",),
        "cuda": ("nvidia-cuda-toolkit",),
    }),
    "fedora": ("sudo dnf install", {
        "git": ("git",),
        "cmake": ("cmake",),
        "c_compiler": ("gcc",),
        "cpp_compiler": ("gcc-c++",),
        "build_tool": ("make",),
        "openblas": ("pkgconf-pkg-config", "openblas-devel"),
        "vulkan": ("pkgconf-pkg-config", "vulkan-loader-devel"),
        "glslc": ("glslc",),
    }),
    "arch": ("sudo pacman -S --needed", {
        "git": ("git",),
        "cmake": ("cmake",),
        "c_compiler": ("base-devel",),
        "cpp_compiler": ("base-devel",),
        "build_tool": ("base-devel",),
        "openblas": ("pkgconf", "openblas"),
        "vulkan": ("pkgconf", "vulkan-headers", "vulkan-icd-loader"),
        "glslc": ("shaderc",),
        "cuda": ("cuda",),
        "rocm": ("hip-runtime-amd",),
    }),
    "opensuse": ("sudo zypper install", {
        "git": ("git",),
        "cmake": ("cmake",),
        "c_compiler": ("gcc",),
        "cpp_compiler": ("gcc-c++",),
        "build_tool": ("make",),
        "openblas": ("pkg-config", "openblas-devel"),
        "glslc": ("shaderc",),
    }),
    "alpine": ("sudo apk add", {
        "git": ("git",),
        "cmake": ("cmake",),
        "c_compiler": ("build-base",),
        "cpp_compiler": ("build-base",),
        "build_tool": ("build-base",),
        "openblas": ("pkgconf", "openblas-dev"),
        "vulkan": ("pkgconf", "vulkan-loader-dev"),
        "glslc": ("shaderc",),
    }),
}


def _run_host(command: list[str], *, timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        get_spawn_command() + command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        check=False,
    )


def _available_host_commands(commands: Iterable[str]) -> set[str]:
    commands = tuple(dict.fromkeys(commands))
    if not is_flatpak():
        return {command for command in commands if shutil.which(command)}

    try:
        # Arguments are passed separately and expanded by the fixed script, so
        # dependency names are never interpolated into shell source.
        result = _run_host([
            "sh",
            "-c",
            'for command do command -v "$command" >/dev/null 2>&1 && printf "%s\\n" "$command"; done; exit 0',
            "sh",
            *commands,
        ])
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return set(result.stdout.splitlines())


def _host_pkg_config_available(package: str) -> bool:
    try:
        return _run_host(["pkg-config", "--exists", package]).returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def find_missing_build_dependencies(
    backend: str = "cpu",
    *,
    command_available: Callable[[str], bool] | None = None,
    pkg_config_available: Callable[[str], bool] | None = None,
) -> tuple[BuildDependency, ...]:
    """Return source-build dependencies missing from the host.

    Optional probe callables make the platform-independent decision logic easy
    to test. A dependency with multiple candidates is satisfied by any one of
    them.
    """
    dependencies = COMMON_DEPENDENCIES + BACKEND_DEPENDENCIES.get(backend, ())

    if command_available is None:
        command_names = (
            candidate
            for dependency in dependencies
            if dependency.probe == "command"
            for candidate in dependency.candidates
        )
        available_commands = _available_host_commands(command_names)
        command_available = available_commands.__contains__
    if pkg_config_available is None:
        pkg_config_available = _host_pkg_config_available

    missing = []
    for dependency in dependencies:
        probe = (
            command_available
            if dependency.probe == "command"
            else pkg_config_available
        )
        if not any(probe(candidate) for candidate in dependency.candidates):
            missing.append(dependency)
    return tuple(missing)


def parse_os_release(text: str) -> dict[str, str]:
    values = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value.replace(r"\"", '"').replace(r"\\", "\\")
    return values


def _distribution_from_values(values: dict[str, str]) -> LinuxDistribution | None:
    distro_id = values.get("ID", "").lower()
    candidates = [distro_id]
    candidates.extend(values.get("ID_LIKE", "").lower().split())

    for candidate in candidates:
        for family, aliases in _DISTRO_ALIASES.items():
            if candidate in aliases:
                return LinuxDistribution(
                    family=family,
                    name=values.get("PRETTY_NAME") or values.get("NAME") or distro_id,
                )
    return None


def detect_linux_distribution() -> LinuxDistribution | None:
    """Detect a supported host distribution from ``/etc/os-release``."""
    try:
        if is_flatpak():
            result = _run_host(["cat", "/etc/os-release"])
            if result.returncode != 0:
                return None
            text = result.stdout
        else:
            with open("/etc/os-release", encoding="utf-8") as release_file:
                text = release_file.read()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return _distribution_from_values(parse_os_release(text))


def suggest_install_command(
    missing: Iterable[BuildDependency],
    distribution: LinuxDistribution | None,
) -> tuple[str | None, tuple[BuildDependency, ...]]:
    """Build a package-manager command and return dependencies it cannot cover."""
    missing = tuple(missing)
    if distribution is None or distribution.family not in _INSTALL_COMMANDS:
        return None, missing

    command, package_map = _INSTALL_COMMANDS[distribution.family]
    packages = []
    unsupported = []
    for dependency in missing:
        dependency_packages = package_map.get(dependency.key)
        if dependency_packages is None:
            unsupported.append(dependency)
            continue
        for package in dependency_packages:
            if package not in packages:
                packages.append(package)

    if not packages:
        return None, tuple(unsupported)
    return f"{command} {' '.join(packages)}", tuple(unsupported)
