import gi
import sys


def _has_gi_namespace(namespace: str, version: str) -> bool:
    try:
        gi.require_version(namespace, version)
        __import__(f"gi.repository.{namespace}")
        return True
    except (ImportError, ValueError):
        return False


WEBKIT_AVAILABLE = _has_gi_namespace("WebKit", "6.0")
VTE_AVAILABLE = _has_gi_namespace("Vte", "3.91")

try:
    if sys.platform == "darwin":
        import objc  # noqa: F401
        from AppKit import NSWindow  # noqa: F401
        from WebKit import WKWebView  # noqa: F401
        MACOS_NATIVE_BROWSER_AVAILABLE = True
    else:
        MACOS_NATIVE_BROWSER_AVAILABLE = False
except Exception:
    MACOS_NATIVE_BROWSER_AVAILABLE = False
