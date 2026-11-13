"""Installation policy shared by the llama.cpp chat and embedding handlers."""

import os
import platform
import re
from gettext import gettext as _

import requests

from ..utility.runtime_installer import RuntimeProject, fetch_json
from ..utility.system import detect_cuda_version, has_backend

LLAMA_BACKEND_LABELS = {
    "cpu": _("CPU"),
    "cpu_openblas": _("CPU (OpenBLAS)"),
    "cuda": _("Nvidia (CUDA)"),
    "rocm": _("AMD (ROCm)"),
    "vulkan": _("Any GPU (Vulkan)"),
    "openvino": _("Intel (OpenVINO)"),
    "sycl-fp32": _("Intel (SYCL FP32)"),
    "sycl-fp16": _("Intel (SYCL FP16)"),
}


class _LlamaReleaseDiscovery:
    RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
    CUDA_RELEASES = (
        (
            "ai-dock",
            "https://api.github.com/repos/ai-dock/llama.cpp-cuda/releases/latest",
        ),
        (
            "syrunekai-cuda13",
            "https://api.github.com/repos/Syrunekai/llama.cpp-cuda/releases/latest",
        ),
        (
            "dramaturg-cuda11",
            "https://api.github.com/repos/dramaturg/llama.cpp-cuda/releases/latest",
        ),
    )

    def fetch_releases(self):
        machine = platform.machine().lower()
        arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(
            machine, machine
        )
        releases = fetch_json(self.RELEASE_API)
        if isinstance(releases, dict):
            releases = [releases]
        release = next(
            (
                item
                for item in releases
                if any(
                    self._matches_asset(asset["name"], arch)
                    for asset in item.get("assets", [])
                )
            ),
            {},
        )
        available = self._release_assets(release, arch)
        for source, url in self.CUDA_RELEASES:
            try:
                available.extend(self._release_assets(fetch_json(url), arch, source))
            except (requests.RequestException, ValueError):
                # Third-party CUDA builds supplement the official release.
                continue
        backend_checks = {
            backend: has_backend(backend)
            for backend in ("cuda", "rocm", "vulkan", "openvino", "sycl")
        }
        system_cuda = detect_cuda_version()
        for asset in available:
            backend = asset["backend"].split("-")[0]
            asset["compatible"] = backend == "cpu" or backend_checks.get(backend, False)
            cuda = asset.get("cuda_version")
            asset["cuda_match"] = (
                system_cuda is not None and cuda is not None and cuda <= system_cuda
            )
        priority = {
            "cuda": 0,
            "rocm": 1,
            "vulkan": 2,
            "openvino": 3,
            "sycl-fp16": 4,
            "sycl-fp32": 5,
            "cpu": 6,
        }
        available.sort(
            key=lambda asset: (
                not asset["compatible"],
                0 if asset["cuda_match"] else 1,
                priority.get(asset["backend"], 99),
                -(asset.get("cuda_version") or 0),
            )
        )
        for asset in available:
            title = LLAMA_BACKEND_LABELS.get(asset["backend"], asset["backend"])
            if asset.get("cuda_version") is not None:
                title += f" {asset['cuda_version']:.1f}"
            asset["title"] = title
            asset["recommended"] = asset["compatible"] and asset["backend"] != "cpu"
            asset["best_match"] = asset["recommended"] and asset["cuda_match"]
        return available

    def _matches_asset(self, name, arch, third_party=False):
        if not name.endswith(".tar.gz"):
            return False
        if not third_party and "-bin-ubuntu-" not in name:
            return False
        arm = "arm64" in name.lower() or "aarch64" in name.lower()
        return (arch == "arm64" and arm) or (arch == "x64" and not arm)

    def _release_assets(self, release, arch, source="official"):
        available = []
        for asset in release.get("assets", []):
            name = asset["name"]
            if not self._matches_asset(name, arch, source != "official"):
                continue
            backend = next(
                (
                    key
                    for key in ("rocm", "cuda", "vulkan", "openvino", "sycl")
                    if key in name.lower()
                ),
                "cpu",
            )
            if source != "official" and backend != "cuda":
                continue
            if backend == "sycl":
                backend = "sycl-fp16" if "fp16" in name.lower() else "sycl-fp32"
            cuda = re.search(r"cuda[-_.]?(\d+)\.(\d+)", name.lower())
            available.append(
                {
                    "name": name,
                    "url": asset["browser_download_url"],
                    "size": asset.get("size", 0),
                    "backend": backend,
                    "cuda_version": float(f"{cuda[1]}.{cuda[2]}") if cuda else None,
                    "tag": release.get("tag_name", "unknown"),
                    "source": source,
                }
            )
        return available


def fetch_llama_cpp_releases():
    return _LlamaReleaseDiscovery().fetch_releases()


GGML_FLAGS = (
    "-DGGML_NATIVE=ON",
    "-DGGML_AVX2=ON",
    "-DGGML_FMA=ON",
    "-DGGML_AVX512=OFF",
)


GGML_BACKENDS = {
    "cpu": (),
    "cpu_openblas": ("-DGGML_BLAS=ON", "-DGGML_BLAS_VENDOR=OpenBLAS"),
    "cuda": ("-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=native"),
    "rocm": ("-DGGML_HIPBLAS=ON", "-DAMDGPU_TARGETS=native"),
    "vulkan": ("-DGGML_VULKAN=ON",),
}


LLAMA_CPP = RuntimeProject(
    name="llama.cpp",
    repository="https://github.com/ggml-org/llama.cpp.git",
    binaries=("llama-server",),
    cmake_flags=GGML_FLAGS,
    backend_flags={
        **GGML_BACKENDS,
        "cuda": GGML_BACKENDS["cuda"] + ("-DGGML_CUDA_F16=ON", "-DGGML_CUDA_GRAPHS=ON"),
        "vulkan": GGML_BACKENDS["vulkan"] + ("-DGGML_VULKAN_F16=ON",),
        "openvino": ("-DGGML_OPENVINO=ON",),
        "sycl-fp32": (
            "-DGGML_SYCL=ON",
            "-DCMAKE_CXX_COMPILER=icpx",
            "-DCMAKE_C_COMPILER=icx",
        ),
        "sycl-fp16": (
            "-DGGML_SYCL=ON",
            "-DCMAKE_CXX_COMPILER=icpx",
            "-DCMAKE_C_COMPILER=icx",
            "-DGGML_SYCL_F16=ON",
        ),
    },
    backend_labels=LLAMA_BACKEND_LABELS,
    fetch_releases=fetch_llama_cpp_releases,
)


class LlamaCPPInstallerMixin:
    """Keep llama.cpp configuration and settings out of the shared installer."""

    runtime_project = LLAMA_CPP

    def show_install_dialog(self, button):
        from ..ui.widgets.runtime_installer import show_runtime_installer

        show_runtime_installer(
            self,
            self.runtime_project,
            self.llama_cpp_path,
            button,
            on_installed=self.on_runtime_installed,
        )

    def on_runtime_installed(self, backend, version=None, prebuilt=False):
        self.set_setting("prebuilt", prebuilt)
        self.set_setting("prebuilt_backend", backend)
        self.set_setting("prebuilt_cuda", prebuilt and backend == "cuda")
        self.set_setting("gpu_acceleration", True)
        if prebuilt:
            self.set_setting(
                "prebuilt_so_path", os.path.join(self.llama_cpp_path, "build", "bin")
            )
