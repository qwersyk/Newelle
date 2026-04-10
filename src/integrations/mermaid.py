from gi.repository import Gtk
from ..extensions import NewelleExtension
from ..ui.widgets import MermaidWidget


class MermaidIntegration(NewelleExtension):
    id = "mermaid"
    name = "Mermaid Diagrams"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def get_replace_codeblocks_langs(self) -> list:
        return ["mmd", "mermaid"]

    def get_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None:
        return MermaidWidget(codeblock)

    def restore_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None:
        return MermaidWidget(codeblock)
