from ..utility.message_chunk import get_message_chunks
from ..extensions import NewelleExtension
from ..ui.widgets import WebSearchWidget
from gi.repository import Gtk, GLib


class WebsearchIntegration(NewelleExtension):
    id = "websearch"
    name = "Websearch"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.widgets = {}
        self.load_widet_cache()

    def get_replace_codeblocks_langs(self) -> list:
        return ["search"]

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        return True 

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        if self.websearch.supports_streaming_query():
            text, sources = self.websearch.query_streaming(codeblock, lambda title, link, favicon, codeblock=codeblock: self.add_website(codeblock, title, link, favicon))   
            text = text[:5000]
        else:
            text, sources = self.websearch.query(codeblock)
            text = text[:5000]
            for source in sources:
                self.add_website(codeblock, source, source, "")
        self.finish(codeblock, text)
        return "Here is the web search result for query '"+ codeblock + "':\n" + text

    def finish(self, codeblock: str, result: str):
        self.widget_cache[codeblock]["result"] = result
        self.save_widget_cache()
        search_widget = self.widgets.get(codeblock, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.finish, result)

    def add_website(self, term, title, link, favicon):
        search_widget = self.widgets.get(term, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.add_website, title, link, favicon)
        self.widget_cache[term]["websites"].append((title, link, favicon))

    def restore_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        search_widget = self.widgets.get(codeblock, None)
        if search_widget is not None and False:
            return search_widget
        else:
            cache = self.widget_cache.get(codeblock, None)
            if cache is not None:
                search_widget = WebSearchWidget(codeblock)
                search_widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
                if "websites" in cache:
                    for title, link, favicon in cache["websites"]:
                        search_widget.add_website(title, link, favicon)
                search_widget.finish(cache["result"])
            else:
                search_widget = WebSearchWidget(codeblock)
                search_widget.finish("No result found")
        return search_widget

    def get_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        search_widget = WebSearchWidget(search_term=codeblock)
        search_widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
        self.widgets[codeblock] = search_widget 
        self.widget_cache[codeblock] = {}
        self.widget_cache[codeblock]["websites"] = []
        self.widget_cache[codeblock]["result"] = "No result found"
        return search_widget

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        chunks = get_message_chunks(bot_response)
        for chunk in chunks:
            if chunk.type == "codeblock" and chunk.lang == "search":
                bot_response = "```search\n" + chunk.text + "\n```"
                break
        return history, bot_response

    def save_widget_cache(self):
        self.set_setting("widget_cache", self.widget_cache)

    def load_widet_cache(self):
        self.widget_cache = self.get_setting("widget_cache", False, {})

