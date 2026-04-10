#!/usr/bin/env python3
import gettext
import importlib.util
import locale
import os
import re
import signal
import subprocess
import sys
import ctypes
import ctypes.util


ROOT = os.getenv("NEWELLE_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD_DIR = os.getenv("NEWELLE_BUILD_DIR", os.path.join(ROOT, ".macos-build"))
PKG_DATA_DIR = os.getenv("NEWELLE_PKG_DATA_DIR", os.path.join(BUILD_DIR, "share", "newelle"))
LOCALE_DIR = os.getenv("NEWELLE_LOCALE_DIR", os.path.join(ROOT, "po"))
SOURCE_DIR = os.path.join(ROOT, "src")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENTS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
DEFAULT_FRAMEWORKS_DIR = os.path.join(CONTENTS_DIR, "Frameworks")


def preload_macos_gtk_libraries() -> None:
    if sys.platform != "darwin":
        return

    lib_dir = os.getenv("NEWELLE_LIB_DIR")
    if not lib_dir and os.path.isdir(DEFAULT_FRAMEWORKS_DIR):
        lib_dir = DEFAULT_FRAMEWORKS_DIR
    if not lib_dir:
        brew_prefix = os.getenv("HOMEBREW_PREFIX")
        if brew_prefix:
            lib_dir = os.path.join(brew_prefix, "lib")
        else:
            return
    dlopen_mode = getattr(os, "RTLD_NOW", 0) | getattr(ctypes, "RTLD_GLOBAL", 0)
    libraries = (
        "libglib-2.0.0.dylib",
        "libgmodule-2.0.0.dylib",
        "libgobject-2.0.0.dylib",
        "libgio-2.0.0.dylib",
        "libgirepository-2.0.0.dylib",
        "libgdk_pixbuf-2.0.0.dylib",
        "libpango-1.0.0.dylib",
        "libpangocairo-1.0.0.dylib",
        "libcairo.2.dylib",
        "libgtk-4.1.dylib",
        "libadwaita-1.0.dylib",
        "libgtksourceview-5.0.dylib",
        "librsvg-2.2.dylib",
    )

    for library in libraries:
        path = os.path.join(lib_dir, library)
        if not os.path.exists(path):
            continue
        try:
            ctypes.CDLL(path, mode=dlopen_mode)
        except OSError as exc:
            if os.getenv("NEWELLE_DEBUG_IMPORTS") == "1":
                print(f"Failed to preload {library}: {exc}")


def configure_macos_process_identity(name: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p

        def cls(value: str):
            return objc.objc_getClass(value.encode("utf-8"))

        def sel(value: str):
            return objc.sel_registerName(value.encode("utf-8"))

        def msg(obj, selector, *args):
            return objc.objc_msgSend(ctypes.c_void_p(obj), ctypes.c_void_p(selector), *args)

        ns_string = cls("NSString")
        ns_process_info = cls("NSProcessInfo")
        name_obj = msg(ns_string, sel("stringWithUTF8String:"), ctypes.c_char_p(name.encode("utf-8")))
        process = msg(ns_process_info, sel("processInfo"))
        msg(process, sel("setProcessName:"), ctypes.c_void_p(name_obj))
    except Exception as exc:
        if os.getenv("NEWELLE_DEBUG_IMPORTS") == "1":
            print(f"Failed to set macOS process name: {exc}")


def configure_macos_locale() -> None:
    if sys.platform != "darwin":
        return
    if any(os.getenv(name) for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")):
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        return

    try:
        languages = subprocess.check_output(
            ["defaults", "read", "-g", "AppleLanguages"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        match = re.search(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?', languages)
        if match:
            language = match.group(1).replace("-", "_")
            os.environ.setdefault("LANG", f"{language}.UTF-8")
            os.environ.setdefault("LANGUAGE", language)
    except Exception:
        pass

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass


def detect_version() -> str:
    if os.getenv("NEWELLE_VERSION"):
        return os.getenv("NEWELLE_VERSION", "dev")
    meson_file = os.path.join(ROOT, "meson.build")
    try:
        with open(meson_file, "r", encoding="utf-8") as handle:
            content = handle.read()
        match = re.search(r"version:\s*'([^']+)'", content)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "dev"


def load_package():
    spec = importlib.util.spec_from_file_location(
        "newelle",
        os.path.join(SOURCE_DIR, "__init__.py"),
        submodule_search_locations=[SOURCE_DIR],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to create import spec for Newelle")
    module = importlib.util.module_from_spec(spec)
    sys.modules["newelle"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    version = detect_version()
    app_name = os.getenv("NEWELLE_APP_NAME", "Newelle")
    locale_dir = LOCALE_DIR
    if locale_dir.endswith(os.path.sep + "po") and not os.path.isdir(os.path.join(locale_dir, "en", "LC_MESSAGES")):
        bundled_locale_dir = os.path.join(os.path.dirname(PKG_DATA_DIR), "locale")
        if os.path.isdir(bundled_locale_dir):
            locale_dir = bundled_locale_dir

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    configure_macos_locale()
    gettext.bindtextdomain("newelle", locale_dir)
    gettext.textdomain("newelle")
    gettext.install("newelle", locale_dir)

    os.environ.setdefault("NEWELLE_ICON_DIR", os.path.join(os.path.dirname(PKG_DATA_DIR), "icons"))
    bin_dir = os.getenv("NEWELLE_BIN_DIR")
    if bin_dir and os.path.isdir(bin_dir):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    configure_macos_process_identity(app_name)
    preload_macos_gtk_libraries()

    import gi
    from gi.repository import Gio, GLib

    GLib.set_prgname(app_name)
    GLib.set_application_name(app_name)
    sys.argv[0] = app_name

    resource_path = os.path.join(PKG_DATA_DIR, "newelle.gresource")
    if os.path.exists(resource_path):
        resource = Gio.Resource.load(resource_path)
        resource._register()

    load_package()
    from newelle import main as newelle_main

    return newelle_main.main(version)


if __name__ == "__main__":
    raise SystemExit(main())
