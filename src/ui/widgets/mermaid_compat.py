from html import escape

from gi.repository import Gdk, Gtk

from .runtime import MACOS_NATIVE_BROWSER_AVAILABLE

if MACOS_NATIVE_BROWSER_AVAILABLE:
    from .browser_native_macos import NativeMacOSBrowserSession


MERMAID_HTML_TEMPLATE = """
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: {bg_color};
      overflow: auto;
    }}
    body {{
      padding: 16px;
      box-sizing: border-box;
    }}
  </style>
  <script type="module">
    import mermaid from "https://cdn.skypack.dev/mermaid@8.14.0";
    mermaid.initialize({{
      startOnLoad: true,
      logLevel: "error",
      securityLevel: "loose",
      theme: (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "default"
    }});
  </script>
</head>
<body>
  <div class="mermaid">{diagram}</div>
</body>
</html>
"""


class MermaidWidget(Gtk.Box):
    def __init__(self, diagram_code, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, **kwargs)
        self._diagram_code = diagram_code
        self.native_browser = None
        self.add_css_class("card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(8)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_bottom(4)

        title = Gtk.Label(label="Mermaid Diagram", xalign=0, hexpand=True)
        title.add_css_class("heading")
        header.append(title)

        copy_button = Gtk.Button(icon_name="edit-copy-symbolic", css_classes=["flat"])
        copy_button.connect("clicked", self._on_copy_clicked)
        header.append(copy_button)
        self.append(header)

        self.stack = Gtk.Stack(hexpand=True)
        self.append(self.stack)

        if MACOS_NATIVE_BROWSER_AVAILABLE:
            self.native_host = Gtk.DrawingArea(hexpand=True, vexpand=False)
            self.native_host.set_content_width(1)
            self.native_host.set_content_height(320)
            native_scroller = Gtk.ScrolledWindow(min_content_height=220)
            native_scroller.set_child(self.native_host)
            self.stack.add_named(native_scroller, "preview")
            self.native_browser = NativeMacOSBrowserSession(self, self.native_host, "")
            self.native_browser.load_html(self._build_html())

        fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        fallback_box.set_margin_start(12)
        fallback_box.set_margin_end(12)
        fallback_box.set_margin_bottom(12)

        body = Gtk.Label(
            label="Mermaid source",
            wrap=True,
            xalign=0,
        )
        fallback_box.append(body)

        code = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        code.get_buffer().set_text(diagram_code)
        scroller = Gtk.ScrolledWindow(min_content_height=120)
        scroller.set_child(code)
        fallback_box.append(scroller)
        self.stack.add_named(fallback_box, "source")
        self.stack.set_visible_child_name("preview" if self.native_browser is not None else "source")
        self.connect("notify::root", self._on_root_changed)

    def _build_html(self) -> str:
        return MERMAID_HTML_TEMPLATE.format(
            diagram=escape(self._diagram_code),
            bg_color="#ffffff",
        )

    def _on_root_changed(self, widget, _param):
        if widget.get_root() is None and self.native_browser is not None:
            self.native_browser.close()

    def _on_native_load_started(self):
        return False

    def _on_native_page_changed(self, url: str, title: str):
        self.stack.set_visible_child_name("preview")
        return False

    def _on_native_html_ready(self, html: str):
        return False

    def _on_native_load_failed(self, message: str):
        self.stack.set_visible_child_name("source")
        return False

    def _on_copy_clicked(self, button):
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(self._diagram_code))
        button.set_icon_name("object-select-symbolic")
