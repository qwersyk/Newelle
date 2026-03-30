import gi
gi.require_version('WebKit', '6.0')
from gi.repository import Adw, Gdk, GLib, Gtk, WebKit

MERMAID_HTML_TEMPLATE = """
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      margin: 0;
      padding: 12px;
      background-color: {bg_color};
    }}
  </style>
  <script type="module">
    import mermaid from "https://cdn.skypack.dev/mermaid@8.14.0";

    mermaid.initialize({{
      startOnLoad: true, // Ensures it looks for .mermaid divs automatically
      logLevel: "error",
      securityLevel: "loose",
      theme: (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ?
        "dark" :
        "default"
    }});

    function sendHeight() {{
      const root = document.querySelector(".mermaid");
      const bodyStyle = window.getComputedStyle(document.body);
      const paddingTop = parseFloat(bodyStyle.paddingTop) || 0;
      const paddingBottom = parseFloat(bodyStyle.paddingBottom) || 0;

      let contentHeight = 0;
      if (root) {{
        contentHeight = Math.ceil(root.getBoundingClientRect().height);
      }}

      const fallbackHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
      const height = Math.max(contentHeight + paddingTop + paddingBottom, fallbackHeight);

      try {{
        window.webkit.messageHandlers.resizer.postMessage(height);
      }} catch (e) {{}}
    }}

    function setupAutoResize() {{
      const root = document.querySelector(".mermaid");
      if (!root) {{
        return;
      }}

      const observer = new MutationObserver(function() {{
        setTimeout(sendHeight, 100);
      }});
      observer.observe(root, {{ childList: true, subtree: true }});

      requestAnimationFrame(sendHeight);
      setTimeout(sendHeight, 300);
      setTimeout(sendHeight, 1000);
      setTimeout(sendHeight, 2000);
    }}

    if (document.readyState === "loading") {{
      document.addEventListener("DOMContentLoaded", setupAutoResize, {{ once: true }});
    }} else {{
      setupAutoResize();
    }}
  </script>
</head>
<body>
  <div class="mermaid">
    {diagram}
  </div>
</body>
</html>
"""


def rgb_to_hex(red: float, green: float, blue: float) -> str:
    r = max(0, min(255, int(round(red * 255))))
    g = max(0, min(255, int(round(green * 255))))
    b = max(0, min(255, int(round(blue * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


class MermaidWidget(Gtk.Box):
    def __init__(self, diagram_code, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.add_css_class("card")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(4)
        self.set_margin_end(4)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=8,
            margin_bottom=4,
            margin_start=12,
            margin_end=12,
        )

        icon = Gtk.Image(
            icon_name="view-grid-symbolic",
            pixel_size=20,
            valign=Gtk.Align.CENTER,
        )
        icon.add_css_class("accent")
        header.append(icon)

        title = Gtk.Label(
            label="Mermaid Diagram",
            halign=Gtk.Align.START,
            hexpand=True,
        )
        title.add_css_class("heading")
        title.add_css_class("caption")
        header.append(title)

        self.copy_button = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.copy_button.set_icon_name("edit-copy-symbolic")
        self.copy_button.set_tooltip_text("Copy diagram code")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        header.append(self.copy_button)

        self.append(header)

        self._diagram_code = diagram_code
        self._max_height = 600
        self._min_height = 80
        self._last_height = 0

        content_manager = WebKit.UserContentManager()
        content_manager.register_script_message_handler("resizer", None)
        content_manager.connect("script-message-received::resizer", self._on_height_message)

        self.webview = WebKit.WebView(user_content_manager=content_manager)
        self.webview.set_vexpand(False)
        self.webview.set_hexpand(True)
        self.webview.set_halign(Gtk.Align.FILL)

        settings = WebKit.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(False)
        self.webview.set_settings(settings)
        self.webview.connect("load-changed", self._on_load_changed)

        self.webview.set_size_request(-1, 200)

        self.append(self.webview)
        self._load_diagram()

    def _load_diagram(self):
        color = self.get_style_context().lookup_color('window_bg_color')[1]
        default = rgb_to_hex(color.red, color.green, color.blue) if color else "#ffffff"

        escaped = self._diagram_code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = MERMAID_HTML_TEMPLATE.format(
            diagram=escaped,
            bg_color=default,
        )
        self.webview.load_html(html, None)

    def _on_height_message(self, manager, message):
        height = None
        try:
            # WebKit script-message payload is exposed as a JS value in recent APIs.
            js_value = message.get_js_value() if hasattr(message, "get_js_value") else None
            if js_value is not None:
                try:
                    height = js_value.to_int32()
                except Exception:
                    try:
                        height = int(js_value.to_double())
                    except Exception:
                        height = None
            elif hasattr(message, "to_int32"):
                height = message.to_int32()
        except Exception:
            height = None

        if isinstance(height, int) and height > 0:
          clamped = min(max(height, self._min_height), self._max_height)
          if abs(clamped - self._last_height) >= 2:
            self._last_height = clamped
            GLib.idle_add(self._set_webview_height, clamped)

    def _set_webview_height(self, height: int):
        self.webview.set_size_request(-1, height)
        return GLib.SOURCE_REMOVE

    def _on_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            GLib.timeout_add(350, self._request_height)
            GLib.timeout_add(1200, self._request_height)

    def _request_height(self):
        script = """
        (function() {
        const root = document.querySelector('.mermaid');
        const bodyStyle = window.getComputedStyle(document.body);
        const paddingTop = parseFloat(bodyStyle.paddingTop) || 0;
        const paddingBottom = parseFloat(bodyStyle.paddingBottom) || 0;

        let contentHeight = 0;
        if (root) {
          contentHeight = Math.ceil(root.getBoundingClientRect().height);
        }

        const fallbackHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
        const height = Math.max(contentHeight + paddingTop + paddingBottom, fallbackHeight);
            try {
                window.webkit.messageHandlers.resizer.postMessage(height);
            } catch (e) {}
        })();
        """
        self.webview.evaluate_javascript(script, -1, None, None, None)
        return GLib.SOURCE_REMOVE

    def _on_copy_clicked(self, button):
        display = Gdk.Display.get_default()
        if display is None:
            return

        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self._diagram_code))

        button.set_icon_name("object-select-symbolic")
        def reset_icon():
            button.set_icon_name("edit-copy-symbolic")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(2000, reset_icon)


