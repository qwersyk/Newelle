import threading
from ..utility.message_chunk import get_message_chunks
from ..extensions import NewelleExtension
from ..ui.widgets import WebSearchWidget
from ..tools import ToolResult, Tool, Command
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
        self.tool_uuid = ""

    def search(self, query: str, tool_uuid: str = None, only_links: bool = False, max_results: int = 5):
        tool_uuid = tool_uuid if tool_uuid is not None else self.ui_controller.get_current_tool_call_id()
        widget = self.get_gtk_widget(query, "", tool_uuid)
        result = ToolResult()
        result.set_widget(widget)
        def get_answer():
            out = self.get_answer(query, "", only_links=only_links, max_results=max_results)
            result.set_output(out)
        th = threading.Thread(target=get_answer)
        th.start()
        return result
    
    def restore_search(self, tool_uuid: str, query:str, only_links: bool = False, max_results: int = 5):
        widget = self.restore_gtk_widget(query, "", tool_uuid)
        return ToolResult(widget=widget)

    def get_tools(self) -> list:
        return [Tool(
            "search", "Perform a search query on the internet, you can specify the number of results to return and if you want to only return the links and titles.", self.search,title="Search", restore_func=self.restore_search, icon_name="system-search-symbolic"
            )]

    def get_commands(self) -> list:
        return [Command(
            "websearch", "Perform a search query on the internet.", self.search, restore_func=self.restore_search, icon_name="system-search-symbolic"
        )]

    def get_replace_codeblocks_langs(self) -> list:
        return ["search"]

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        return True 

    def get_answer(self, codeblock: str, lang: str, only_links: bool = False, max_results: int = None) -> str | None:
        tool_uuid = self.tool_uuid
        if self.websearch.supports_streaming_query():
            text, sources = self.websearch.query_streaming(codeblock, lambda title, link, favicon, codeblock=codeblock, tool_uuid=tool_uuid: self.add_website(codeblock, title, link, favicon, tool_uuid), max_results=max_results)  
        else:
            text, sources = self.websearch.query(codeblock, max_results=max_results)
            for source in sources:
                self.add_website(codeblock, source, source, "", tool_uuid)
        self.finish(codeblock, text, sources, tool_uuid)
        if only_links:
            return "Here are the links for the web search result for query '" + codeblock + "':\n" + "\n".join(sources)
        return "Here is the web search result for query '"+ codeblock + "':\n" + text

    def finish(self, codeblock: str, result: str, sources, tool_uuid):
        if tool_uuid:
            tool_uuid = str(tool_uuid)
            self.widget_cache[tool_uuid]["result"] = result
            self.save_widget_cache()
        search_widget = self.widgets.get(codeblock, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.finish, result)

    def add_website(self, term, title, link, favicon, tool_uuid):
        search_widget = self.widgets.get(term, None)
        if search_widget is not None:
            GLib.idle_add(search_widget.add_website, title, link, favicon)
        if tool_uuid:
            tool_uuid = str(tool_uuid)
            self.widget_cache[tool_uuid]["websites"].append((title, link, favicon))

    def load_search_widget(self, query, sources, result):
        widget = WebSearchWidget(query)
        for title, link, favicon in tuple(sources):
            widget.add_website(title, link, favicon)
        widget.finish(result)
        widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
        return widget 

    def restore_gtk_widget(self, codeblock: str, lang: str, tool_uuid) -> Gtk.Widget | None:
        if tool_uuid:
            tool_uuid = str(tool_uuid)
        cache = self.widget_cache.get(tool_uuid, None)
        if cache is not None:
            return self.load_search_widget(codeblock, cache["websites"], cache["result"])
        else:
            search_widget = WebSearchWidget(codeblock)
            search_widget.finish("No result found")
        return search_widget

    def get_gtk_widget(self, codeblock: str, lang: str, tool_uuid) -> Gtk.Widget | None:
        self.tool_uuid = tool_uuid
        search_widget = WebSearchWidget(search_term=codeblock)
        search_widget.connect("website-clicked", lambda widget,link : self.ui_controller.open_link(link, False, not self.settings.get_boolean("external-browser")))
        self.widgets[codeblock] = search_widget 
        if tool_uuid:
            tool_uuid = str(tool_uuid)
            self.widget_cache[tool_uuid] = {}
            self.widget_cache[tool_uuid]["websites"] = []
            self.widget_cache[tool_uuid]["result"] = "No result found"
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
        else:
            self.widget_cache = {}

