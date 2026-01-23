import threading
from ..utility.message_chunk import get_message_chunks
from ..extensions import NewelleExtension
from ..ui.widgets import WebSearchWidget
from ..tools import ToolResult, Tool
from gi.repository import Gtk, GLib
import os
import json 


class WebsearchIntegration(NewelleExtension):
    id = "websearch"
    name = "Websearch"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.widgets = {}
        self.load_widet_cache()
        self.msgid = 0

    def search(self, query: str, only_links: bool = False, max_results: int = 5):
        msgid = self.ui_controller.get_current_message_id()
        widget = self.get_gtk_widget(query, "", msgid)
        result = ToolResult()
        result.set_widget(widget)
        def get_answer():
            out = self.get_answer(query, "", only_links=only_links, max_results=max_results)
            result.set_output(out)
        th = threading.Thread(target=get_answer)
        th.start()
        return result
    
    def restore_search(self, msg_id, query:str, only_links: bool = False, max_results: int = 5):
        widget = self.restore_gtk_widget(query, "", msg_id)
        return ToolResult(widget=widget)

    def get_tools(self) -> list:
        return [Tool(
            "search", "Perform a search query on the internet, you can specify the number of results to return and if you want to only return the links and titles.", self.search,title="Search", restore_func=self.restore_search, icon_name="system-search-symbolic"
            )]

    def get_replace_codeblocks_langs(self) -> list:
        return ["search"]

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        return True 

    def get_answer(self, codeblock: str, lang: str, only_links: bool = False, max_results: int = None) -> str | None:
        msgid = self.msgid
        if self.websearch.supports_streaming_query():
            text, sources = self.websearch.query_streaming(codeblock, lambda title, link, favicon, codeblock=codeblock, msgid=msgid: self.add_website(codeblock, title, link, favicon, msgid), max_results=max_results)  
        else:
            text, sources = self.websearch.query(codeblock, max_results=max_results)
            for source in sources:
                self.add_website(codeblock, source, source, "", msgid)
        self.finish(codeblock, text, sources, msgid)
        if only_links:
            return "Here are the links for the web search result for query '" + codeblock + "':\n" + "\n".join(sources)
        return "Here is the web search result for query '"+ codeblock + "':\n" + text

    def finish(self, codeblock: str, result: str, sources, msgid):
        self.widget_cache[msgid]["result"] = result
        self.save_widget_cache()
        search_widget = self.widgets.get(codeblock, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.finish, result)

    def add_website(self, term, title, link, favicon, msgid):
        search_widget = self.widgets.get(term, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.add_website, title, link, favicon)
        self.widget_cache[msgid]["websites"].append((title, link, favicon))

    def load_search_widget(self, query, sources, result):
        widget = WebSearchWidget(query)
        for title, link, favicon in tuple(sources):
            widget.add_website(title, link, favicon)
        widget.finish(result)
        widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
        return widget 

    def restore_gtk_widget(self, codeblock: str, lang: str, msgid) -> Gtk.Widget | None:
        cache = self.widget_cache.get(msgid, None)
        if cache is not None:
            return self.load_search_widget(codeblock, cache["websites"], cache["result"])
        else:
            search_widget = WebSearchWidget(codeblock)
            search_widget.finish("No result found")
        return search_widget

    def get_gtk_widget(self, codeblock: str, lang: str, msgid) -> Gtk.Widget | None:
        self.msgid = msgid
        search_widget = WebSearchWidget(search_term=codeblock)
        search_widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
        self.widgets[codeblock] = search_widget 
        self.widget_cache[msgid] = {}
        self.widget_cache[msgid]["websites"] = []
        self.widget_cache[msgid]["result"] = "No result found"
        return search_widget

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        chunks = get_message_chunks(bot_response)
        for chunk in chunks:
            if chunk.type == "codeblock" and chunk.lang == "search":
                bot_response = "```search\n" + chunk.text + "\n```"
                break
        return history, bot_response

    def save_widget_cache(self):
        with open(os.path.join(self.extension_path, "websearch_cache.json"), "w+") as f:
            json.dump(self.widget_cache, f)

    def load_widet_cache(self):
        if os.path.exists(os.path.join(self.extension_path, "websearch_cache.json")):
            with open(os.path.join(self.extension_path, "websearch_cache.json")) as f:
                self.widget_cache = json.load(f)
            for key in self.widget_cache.copy():
                self.widget_cache[int(key)] = self.widget_cache[key]
                del self.widget_cache[key]
        else:
            self.widget_cache = {}

