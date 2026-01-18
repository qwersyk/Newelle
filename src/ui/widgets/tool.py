from gi.repository import Gtk, Adw, Pango

class ToolWidget(Gtk.ListBox):
    def __init__(self, tool_name, chunk_text=""):
        super().__init__()
        self.add_css_class("boxed-list")
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)
        self.expander_row = Adw.ExpanderRow(
            title=tool_name,
            subtitle="Running...",
            icon_name="tools-symbolic",
        )
        self.append(self.expander_row)
        self.chunk_text = chunk_text

    def set_result(self, success, result_text):
        self.expander_row.set_subtitle("Completed" if success else "Error")
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        full_text = self.chunk_text + "\n" + str(result_text)
        display_text = full_text[:8000] if len(full_text) > 8000 else full_text
        label = Gtk.Label(
            label=display_text,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            xalign=0,
        )
        content_box.append(label)
        self.expander_row.add_row(content_box)
