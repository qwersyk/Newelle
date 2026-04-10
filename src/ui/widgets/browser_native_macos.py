import ctypes
import ctypes.util

import objc

from gi.repository import GLib

from AppKit import NSMakeRect, NSView, NSViewHeightSizable, NSViewWidthSizable
from Foundation import NSObject, NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

_capsule_to_pointer = ctypes.pythonapi.PyCapsule_GetPointer
_capsule_to_pointer.restype = ctypes.c_void_p
_capsule_to_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]

_gtk_library = ctypes.CDLL(ctypes.util.find_library("gtk-4.1") or "libgtk-4.1.dylib")
_get_native_window = _gtk_library.gdk_macos_surface_get_native_window
_get_native_window.argtypes = [ctypes.c_void_p]
_get_native_window.restype = ctypes.c_void_p


class _NativeBrowserDelegate(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_NativeBrowserDelegate, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def webView_didStartProvisionalNavigation_(self, webview, navigation):
        self.owner._did_start_loading()

    def webView_didFinishNavigation_(self, webview, navigation):
        self.owner._did_finish_loading()

    def webView_didFailProvisionalNavigation_withError_(self, webview, navigation, error):
        self.owner._did_fail_loading(str(error))

    def webViewWebContentProcessDidTerminate_(self, webview):
        self.owner._did_fail_loading("Web content process terminated")


class NativeMacOSBrowserSession:
    def __init__(self, widget, host_widget, starting_url: str):
        self.widget = widget
        self.host_widget = host_widget
        self.starting_url = starting_url
        self.delegate = _NativeBrowserDelegate.alloc().initWithOwner_(self)
        self.container_view = None
        self.webview = None
        self.window_pointer = None
        self.pending_url = starting_url
        self.pending_html = None
        self.last_frame = None
        self.tick_id = self.host_widget.add_tick_callback(self._on_tick)

    def _on_tick(self, host_widget, frame_clock):
        self._sync_embedded_view()
        return True

    def _get_surface_pointer(self):
        root = self.host_widget.get_root()
        if root is None:
            return None
        surface = root.get_surface()
        if surface is None:
            return None
        return _capsule_to_pointer(surface.__gpointer__, None)

    def _get_window(self):
        surface_pointer = self._get_surface_pointer()
        if surface_pointer is None:
            return None
        native_window = _get_native_window(surface_pointer)
        if not native_window:
            return None
        return native_window, objc.objc_object(c_void_p=native_window)

    def _create_webview(self):
        configuration = WKWebViewConfiguration.alloc().init()
        self.webview = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0.0, 0.0, 100.0, 100.0), configuration)
        self.webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.webview.setNavigationDelegate_(self.delegate)

    def _attach_to_window(self):
        window_info = self._get_window()
        if window_info is None:
            return False

        native_pointer, native_window = window_info
        if self.window_pointer != native_pointer:
            self._detach()
            self.window_pointer = native_pointer

        if self.container_view is None:
            if self.webview is None:
                self._create_webview()
            self.container_view = NSView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 100.0, 100.0))
            self.container_view.addSubview_(self.webview)
            native_window.contentView().addSubview_(self.container_view)

        self._update_frame(native_window)
        if self.pending_html is not None:
            self.webview.loadHTMLString_baseURL_(self.pending_html, None)
            self.pending_html = None
        elif self.pending_url:
            request = self._to_request(self.pending_url)
            if request is not None:
                self.webview.loadRequest_(request)
            self.pending_url = None
        return True

    def _update_frame(self, native_window):
        if self.container_view is None:
            return
        root = self.host_widget.get_root()
        if root is None:
            return
        success, bounds = self.host_widget.compute_bounds(root)
        if not success:
            return

        content_view = native_window.contentView()
        width = max(1.0, bounds.get_width())
        height = max(1.0, bounds.get_height())
        x = bounds.get_x()
        if content_view.isFlipped():
            y = bounds.get_y()
        else:
            y = content_view.frame().size.height - bounds.get_y() - height
        frame = NSMakeRect(x, y, width, height)
        self.container_view.setHidden_(False)
        frame_key = (int(x), int(y), int(width), int(height))
        if self.last_frame != frame_key:
            self.container_view.setFrame_(frame)
            self.webview.setFrame_(NSMakeRect(0.0, 0.0, width, height))
            self.last_frame = frame_key

    def _sync_embedded_view(self):
        if self.host_widget.get_root() is None:
            self._detach()
            return
        if not self.host_widget.get_mapped():
            if self.container_view is not None:
                self.container_view.setHidden_(True)
            return
        self._attach_to_window()

    def _to_request(self, url: str):
        nsurl = NSURL.URLWithString_(url)
        if nsurl is None:
            return None
        return NSURLRequest.requestWithURL_(nsurl)

    def load(self, url: str, present: bool = False):
        self.pending_html = None
        self.pending_url = url
        if self._attach_to_window():
            self.pending_url = None

    def load_html(self, html: str):
        self.pending_url = None
        self.pending_html = html
        if self._attach_to_window():
            self.pending_html = None

    def show(self):
        self._sync_embedded_view()

    def close(self):
        if self.tick_id is not None:
            self.host_widget.remove_tick_callback(self.tick_id)
            self.tick_id = None
        self._detach()

    def _detach(self):
        if self.container_view is not None:
            self.container_view.removeFromSuperview()
            self.container_view = None
        self.window_pointer = None
        self.last_frame = None

    def can_go_back(self) -> bool:
        return bool(self.webview is not None and self.webview.canGoBack())

    def can_go_forward(self) -> bool:
        return bool(self.webview is not None and self.webview.canGoForward())

    def go_back(self):
        if self.can_go_back():
            self.webview.goBack()

    def go_forward(self):
        if self.can_go_forward():
            self.webview.goForward()

    def reload(self):
        if self.webview is not None:
            self.webview.reload()

    def stop(self):
        if self.webview is not None:
            self.webview.stopLoading()

    def get_current_url(self) -> str:
        if self.webview is None or self.webview.URL() is None:
            return getattr(self.widget, "current_url", "")
        return str(self.webview.URL().absoluteString())

    def get_current_title(self) -> str:
        if self.webview is None:
            return getattr(self.widget, "current_title", "")
        title = self.webview.title()
        return str(title) if title else getattr(self.widget, "current_title", "")

    def _dispatch_to_widget(self, method_name: str, *args):
        callback = getattr(self.widget, method_name, None)
        if callback is not None:
            GLib.idle_add(callback, *args)

    def _did_start_loading(self):
        self._dispatch_to_widget("_on_native_load_started")

    def _did_finish_loading(self):
        self._dispatch_to_widget("_on_native_page_changed", self.get_current_url(), self.get_current_title())
        self._request_html()

    def _did_fail_loading(self, message: str):
        self._dispatch_to_widget("_on_native_load_failed", message)

    def _request_html(self):
        if self.webview is None:
            return

        def completion(result, error):
            if error is not None or result is None:
                return
            self._dispatch_to_widget("_on_native_html_ready", str(result))

        self.webview.evaluateJavaScript_completionHandler_("document.documentElement.outerHTML", completion)
