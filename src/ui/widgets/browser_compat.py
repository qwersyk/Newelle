import json
import os
import threading
import urllib.parse

from gi.repository import Adw, GObject, Gtk, GdkPixbuf, GLib

from ...ui import load_image_with_callback
from ...utility.system import open_website
from ...utility.website_scraper import WebsiteScraper
from .runtime import MACOS_NATIVE_BROWSER_AVAILABLE

if MACOS_NATIVE_BROWSER_AVAILABLE:
    from .browser_native_macos import NativeMacOSBrowserSession


class BrowserWidget(Gtk.Box):
    __gsignals__ = {
        "page-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
        "attach-clicked": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "favicon-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, starting_url="https://www.google.com", search_string="https://www.google.com/search?q=%s", session_file=None, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.webview = self
        self.session_file = session_file
        self.starting_url = starting_url
        self.search_string = search_string
        self.current_url = ""
        self.current_title = ""
        self.current_favicon = None
        self.favicon_pixbuf: GdkPixbuf.Pixbuf | None = None
        self.page_html = None
        self.page_text = ""
        self.page_description = ""
        self.loading_generation = 0
        self.page_cache: dict[str, dict[str, str | None]] = {}
        self.history: list[str] = []
        self.history_index = -1
        self.native_browser = None

        self._build_ui()
        if MACOS_NATIVE_BROWSER_AVAILABLE:
            self.native_browser = NativeMacOSBrowserSession(self, self.native_host, starting_url)
        self.connect("notify::root", self._on_root_changed)

        if self.session_file:
            self.load_session(self.session_file, lambda: self.navigate_to(self.starting_url))
        else:
            self.navigate_to(self.starting_url)

    def _build_ui(self):
        self.toolbar = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False, show_end_title_buttons=False)

        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.set_tooltip_text("Go Back")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.toolbar.pack_start(self.back_button)

        self.forward_button = Gtk.Button(icon_name="go-next-symbolic")
        self.forward_button.set_tooltip_text("Go Forward")
        self.forward_button.connect("clicked", self._on_forward_clicked)
        self.toolbar.pack_start(self.forward_button)

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Refresh")
        self.refresh_button.connect("clicked", self._on_refresh_clicked)
        self.toolbar.pack_start(self.refresh_button)

        self.url_entry = Gtk.Entry(hexpand=True, placeholder_text="Enter URL or search term...")
        self.url_entry.connect("activate", self._on_url_activate)
        self.toolbar.set_title_widget(self.url_entry)

        self.home_button = Gtk.Button(icon_name="go-home-symbolic")
        self.home_button.set_tooltip_text("Home")
        self.home_button.connect("clicked", self._on_home_clicked)
        self.toolbar.pack_end(self.home_button)

        self.open_button = Gtk.Button(icon_name="window-new-symbolic")
        self.open_button.set_tooltip_text("Open in Default Browser")
        self.open_button.connect("clicked", lambda *_: open_website(self.current_url or self.starting_url))
        self.toolbar.pack_end(self.open_button)

        self.attach_button = Gtk.Button(icon_name="attach-symbolic")
        self.attach_button.set_tooltip_text("Attach")
        self.attach_button.connect("clicked", lambda *_: self.emit("attach-clicked"))
        self.toolbar.pack_end(self.attach_button)

        self.append(self.toolbar)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=12, margin_bottom=8, margin_start=16, margin_end=16)
        self.title_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.title_label.add_css_class("title-3")
        meta_box.append(self.title_label)

        self.description_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.description_label.add_css_class("dim-label")
        meta_box.append(self.description_label)
        self.append(meta_box)

        self.status_page = Adw.StatusPage(
            icon_name="internet-symbolic",
            title="Loading page",
            description="Fetching content for the current page.",
        )

        self.body_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.body_view.set_monospace(False)
        self.body_view.set_top_margin(12)
        self.body_view.set_bottom_margin(12)
        self.body_view.set_left_margin(16)
        self.body_view.set_right_margin(16)

        self.body_stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.body_stack.add_named(self.status_page, "status")

        if MACOS_NATIVE_BROWSER_AVAILABLE:
            self.native_host = Gtk.DrawingArea(hexpand=True, vexpand=True)
            self.native_host.set_content_width(1)
            self.native_host.set_content_height(1)
            self.body_stack.add_named(self.native_host, "native")

        self.scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.scrolled_window.set_child(self.body_view)
        self.body_stack.add_named(self.scrolled_window, "content")
        self.append(self.body_stack)

        self._update_navigation_buttons()

    def _on_root_changed(self, widget, _param):
        if widget.get_root() is None and self.native_browser is not None:
            self.native_browser.close()

    def _normalize_target(self, text: str) -> str:
        text = text.strip()
        if not text:
            return self.current_url or self.starting_url
        if self._is_url(text):
            if not text.startswith(("http://", "https://", "file://", "ftp://")):
                return "https://" + text
            return text
        return self.search_string % urllib.parse.quote_plus(text)

    def _is_url(self, text: str) -> bool:
        return ("." in text and " " not in text) or text.startswith(("http://", "https://", "file://", "ftp://"))

    def _on_url_activate(self, entry):
        self.navigate_to(self._normalize_target(entry.get_text()))

    def _on_home_clicked(self, button):
        self.navigate_to(self.starting_url)

    def _on_refresh_clicked(self, button):
        if self.current_url:
            if self.native_browser is not None:
                self.native_browser.reload()
            self._load_page(self.current_url, refresh=True, push_history=False)

    def _on_back_clicked(self, button):
        if self.native_browser is not None and self.native_browser.can_go_back():
            self.native_browser.go_back()
            return
        if self.history_index <= 0:
            return
        self.history_index -= 1
        self._load_page(self.history[self.history_index], refresh=False, push_history=False)

    def _on_forward_clicked(self, button):
        if self.native_browser is not None and self.native_browser.can_go_forward():
            self.native_browser.go_forward()
            return
        if self.history_index + 1 >= len(self.history):
            return
        self.history_index += 1
        self._load_page(self.history[self.history_index], refresh=False, push_history=False)

    def _update_navigation_buttons(self):
        native_back = self.native_browser is not None and self.native_browser.can_go_back()
        native_forward = self.native_browser is not None and self.native_browser.can_go_forward()
        self.back_button.set_sensitive(self.history_index > 0 or native_back)
        self.forward_button.set_sensitive(self.history_index + 1 < len(self.history) or native_forward)

    def _set_loading_state(self, url: str):
        self.current_url = url
        self.url_entry.set_text(url)
        self.status_page.set_title("Loading page")
        self.status_page.set_description(url)
        self.body_stack.set_visible_child_name("status")

    def _download_favicon(self, favicon_url: str | None):
        if not favicon_url:
            self.favicon_pixbuf = None
            self.emit("favicon-changed", None)
            return

        def on_loaded(loader):
            self.favicon_pixbuf = loader.get_pixbuf()
            self.emit("favicon-changed", self.favicon_pixbuf)

        load_image_with_callback(favicon_url, on_loaded)

    def _apply_page(self, generation: int, page: dict, push_history: bool):
        if generation != self.loading_generation:
            return GLib.SOURCE_REMOVE

        self.current_url = page["url"]
        self.current_title = page["title"] or page["url"]
        self.current_favicon = page["favicon"]
        self.page_html = page["html"]
        self.page_text = page["text"] or ""
        self.page_description = page["description"] or ""
        self.url_entry.set_text(self.current_url)
        self.title_label.set_text(self.current_title)
        self.description_label.set_text(self.page_description)
        self.body_view.get_buffer().set_text(self.page_text or "No readable content was extracted from this page.")

        if self.native_browser is not None:
            self.body_stack.set_visible_child_name("native")
        else:
            self.body_stack.set_visible_child_name("content")

        if push_history:
            if self.history_index + 1 < len(self.history):
                self.history = self.history[: self.history_index + 1]
            self.history.append(self.current_url)
            self.history_index = len(self.history) - 1

        self._update_navigation_buttons()
        self._download_favicon(self.current_favicon)
        self._emit_page_changed()
        return GLib.SOURCE_REMOVE

    def _apply_error(self, generation: int, url: str, message: str):
        if generation != self.loading_generation:
            return GLib.SOURCE_REMOVE

        message = str(message).replace("<", "‹").replace(">", "›")
        self.current_url = url
        self.current_title = url
        self.current_favicon = None
        self.page_html = None
        self.page_text = ""
        self.page_description = ""
        self.url_entry.set_text(url)
        self.title_label.set_text(url)
        self.description_label.set_text("")
        self.status_page.set_title("Failed to load page")
        self.status_page.set_description(message)
        if self.native_browser is not None:
            self.body_stack.set_visible_child_name("native")
        else:
            self.body_stack.set_visible_child_name("status")
        self._download_favicon(None)
        self._emit_page_changed()
        return GLib.SOURCE_REMOVE

    def _load_page(self, url: str, refresh: bool = False, push_history: bool = True, sync_native: bool = True):
        self.loading_generation += 1
        generation = self.loading_generation
        self._set_loading_state(url)

        if sync_native and self.native_browser is not None:
            self.native_browser.load(url, present=False)

        if not refresh and url in self.page_cache:
            GLib.idle_add(self._apply_page, generation, self.page_cache[url], push_history)
            return

        def worker():
            try:
                scraper = WebsiteScraper(url)
                html = scraper.get_page_source()
                page = {
                    "url": url,
                    "html": html,
                    "title": scraper.get_title() or url,
                    "description": scraper.get_description() or "",
                    "text": scraper.get_text() or "",
                    "favicon": scraper.get_favicon() or None,
                }
                self.page_cache[url] = page
                GLib.idle_add(self._apply_page, generation, page, push_history)
            except Exception as exc:
                GLib.idle_add(self._apply_error, generation, url, str(exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _emit_page_changed(self):
        self.emit("page-changed", self.current_url, self.current_title, None)
        if self.session_file:
            self.save_session(self.session_file)

    def _on_native_load_started(self):
        self.status_page.set_title("Loading interactive page")
        self.status_page.set_description(self.current_url or self.starting_url)
        self.body_stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _on_native_page_changed(self, url: str, title: str):
        previous_url = self.current_url
        if url:
            self.current_url = url
            self.url_entry.set_text(url)
        if title:
            self.current_title = title
            self.title_label.set_text(title)
        self._update_navigation_buttons()
        self.body_stack.set_visible_child_name("native")
        if url and url != previous_url:
            self._load_page(url, refresh=True, push_history=True, sync_native=False)
            return GLib.SOURCE_REMOVE
        self._emit_page_changed()
        return GLib.SOURCE_REMOVE

    def _on_native_html_ready(self, html: str):
        self.page_html = html
        return GLib.SOURCE_REMOVE

    def _on_native_load_failed(self, message: str):
        message = str(message).replace("<", "‹").replace(">", "›")
        self.status_page.set_title("Interactive browser error")
        self.status_page.set_description(message)
        if self.native_browser is not None:
            self.body_stack.set_visible_child_name("native")
        elif self.page_text:
            self.body_stack.set_visible_child_name("content")
        else:
            self.body_stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _on_native_window_closed(self):
        return GLib.SOURCE_REMOVE

    def navigate_to(self, url):
        self._load_page(url)

    def search(self, query):
        self.navigate_to(self.search_string % urllib.parse.quote_plus(query))

    def get_current_url(self):
        if self.native_browser is not None:
            native_url = self.native_browser.get_current_url()
            if native_url:
                return native_url
        return self.current_url

    def get_current_title(self):
        if self.native_browser is not None:
            native_title = self.native_browser.get_current_title()
            if native_title:
                return native_title
        return self.current_title

    def get_current_favicon(self):
        return self.current_favicon

    def set_search_string(self, search_string):
        self.search_string = search_string

    def get_page_html(self, callback):
        callback(self.page_html, None if self.page_html is not None else "No page HTML available")

    def get_page_html_sync(self):
        return self.page_html

    def save_session(self, file_path):
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "current_url": self.get_current_url(),
                        "current_title": self.get_current_title(),
                        "starting_url": self.starting_url,
                        "search_string": self.search_string,
                        "history": self.history,
                        "history_index": self.history_index,
                    },
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as exc:
            print(f"Error saving browser session: {exc}")

    def load_session(self, file_path, on_loaded_callback=None):
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as handle:
                    session_data = json.load(handle)
                self.starting_url = session_data.get("current_url") or session_data.get("starting_url") or self.starting_url
                self.search_string = session_data.get("search_string", self.search_string)
                self.history = session_data.get("history", [])
                self.history_index = session_data.get("history_index", len(self.history) - 1 if self.history else -1)
        except Exception as exc:
            print(f"Error loading browser session: {exc}")
        if on_loaded_callback is not None:
            on_loaded_callback()
