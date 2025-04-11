from gi.repository import Gtk, Pango, Gdk
import xml.etree.ElementTree as ET
from .. import apply_css_to_widget

class MarkupTextView(Gtk.TextView):
    def __init__(self, parent):
        super().__init__()
        self.set_wrap_mode(Gtk.WrapMode.WORD)
        self.set_editable(False)
        self.set_cursor_visible(False)

        self.buffer = self.get_buffer()
        self.create_tags()
        #self.buffer.connect("changed", lambda b: self.update_textview_size(parent))
        self.add_css_class("scroll")
        apply_css_to_widget(
            self, ".scroll { background-color: rgba(0,0,0,0);}"
        )

    def update_textview_size(self, parent=None):
        if parent is not None:
            s = parent.get_size(Gtk.Orientation.HORIZONTAL)
        else:
            s = 300
        buffer = self.get_buffer()
        layout = self.create_pango_layout(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True))
        layout.set_width(s * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD)
        width, height = layout.get_pixel_size()
        self.set_size_request(width, height)
    
    def create_tags(self):
        self.tags = {
            'b': self.buffer.create_tag("bold", weight=Pango.Weight.BOLD),
            'i': self.buffer.create_tag("italic", style=Pango.Style.ITALIC),
            'tt': self.buffer.create_tag("tt", family="monospace"),
            'sub': self.buffer.create_tag("sub", rise=-5000, size_points=8),
            'sup': self.buffer.create_tag("sup", rise=5000, size_points=8),
            'a': self.buffer.create_tag("link", foreground="blue", underline=Pango.Underline.SINGLE)
        }

    def add_markup_text(self, iter, text: str):
        wrapped_markup = f"<root>{text}</root>"
        try:
            root = ET.fromstring(wrapped_markup)
        except ET.ParseError as e:
            print("Parse error:", e)
            self.buffer.insert(iter, text)
            return
        self._insert_markup_recursive(root, iter, [])

    def set_markup(self, markup: str):
        # Wrap in a root tag for parsing
        wrapped_markup = f"<root>{markup}</root>"
        try:
            root = ET.fromstring(wrapped_markup)
        except ET.ParseError as e:
            print("Parse error:", e)
            return

        self.buffer.set_text("")
        self._insert_markup_recursive(root, self.buffer.get_start_iter(), [])

    def _insert_markup_recursive(self, elem, iter, active_tags):
        # Add text before children
        if elem.text:
            self.buffer.insert_with_tags(iter, elem.text, *active_tags)

        # Process children recursively
        for child in elem:
            tag_name = child.tag.lower()
            tags_to_apply = list(active_tags)

            if tag_name == "a":
                tags_to_apply.append(self.tags["a"])
            elif tag_name in self.tags:
                tags_to_apply.append(self.tags[tag_name])

            self._insert_markup_recursive(child, iter, tags_to_apply)

            # Tail text after this child
            if child.tail:
                self.buffer.insert_with_tags(iter, child.tail, *active_tags)
