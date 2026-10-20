from gi.repository import Gtk, GLib, GdkPixbuf
from ..extensions import NewelleExtension
from ..ui.widgets import WebsiteButton
import threading
from ..utility.message_chunk import get_message_chunks
from ..ui import load_image_with_callback
from ..utility.website_scraper import WebsiteScraper
from ..utility.source_attribution import format_source_context
from ..tools import Tool, ToolResult

CHUNK_SIZE = 512 
MAX_CONTEXT = 5000

class WebsiteReader(NewelleExtension):
    id = "website-reader"
    name = "Website Reader"

    def __init__(self, pip_path: str, extension_path: str, settings):
        super().__init__(pip_path, extension_path, settings)
        self.caches = {}
    
    def read_website(self, url, advanced=False):
        widget = self.get_gtk_widget(url, "website")
        result = ToolResult() 
        def get_answer():
            try:
                out = self.get_article_content(url)
            except Exception as e:
                print(f"Error occurred while fetching article from {url}: {e}")
                out = f"Error fetching the website content. {e}"
                result.set_output(out)
                return
            if not advanced:
                out = out.get_text()
            else:
                out = out.clean_html_to_markdown(out.html, True)
            result.set_output(out)
        result.set_widget(widget)
        t = threading.Thread(target=get_answer)
        t.start()
        return result 

    def restore_read_website(self, msg_id, url):
        widget = self.get_gtk_widget(url, "website")
        result = ToolResult()
        result.set_widget(widget)
        return result 

    def get_tools(self) -> list:
        return [Tool("website", "Read a website content. The advanced mode will return extra information about the page, including links. Only use it if normal mode has not produced good results.", self.read_website, title="Read Websites", restore_func=self.restore_read_website, icon_name="internet-symbolic")]           
    def get_replace_codeblocks_langs(self) -> list:
        return ["website"]
   
    def preprocess_history(self, history: list, prompts: list) -> tuple[list, list]:
        current_user = next((message for message in reversed(history)
                             if message.get("User") == "User" and not message.get("ToolContext")), None)
        if current_user is None or ("WebsiteContext" in current_user
                                    and current_user.get("WebsiteContextMessage") == current_user["Message"]):
            return history, prompts
        user_message = current_user["Message"]
        lines = []
        for line in user_message.split("\n"):
            if line.startswith("#https://") or line.startswith("#http://"):
                # Extract just the URL without the hashtag
                urlline = line.split("#")[1].split()
                url = urlline[0]
                lines += ["```website", url, "```"]
                lines += [" ".join(urlline[1:])]
            else:
                lines += [line]
        current_user["Message"] = "\n".join(lines)

        # Find articles
        websites = []
        for message in history:
            for chunk in get_message_chunks(message["Message"]):
                if chunk.type == "codeblock" and chunk.lang == "website":
                    websites.append(chunk.text)
        docs = []
        for website in websites:
            article = self.get_article_content(website)
            docs.append(format_source_context(
                article.get_text(),
                website,
                title=article.get_title() or None,
                source_type="Web",
            ))
        if sum(len(doc) for doc in docs) < MAX_CONTEXT:
            current_user["WebsiteContext"] = docs
        elif self.rag is not None:
            print("Using RAG")
            rag_docs = ["text:" + doc for doc in docs]
            index = self.rag.build_index(rag_docs, CHUNK_SIZE)
            results = index.query(user_message)
            current_user["WebsiteContext"] = results
        else:
            current_user["WebsiteContext"] = ["\n".join(docs)[:MAX_CONTEXT]]
        current_user["WebsiteContextMessage"] = current_user["Message"]
        return history, prompts

    def get_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        website_url = codeblock
         
        button = WebsiteButton(website_url)
        button.connect("clicked", self.open_website)
        threading.Thread(target=self.get_article, args=(button,)).start()
        return button

    def open_website(self, button: WebsiteButton):
        self.ui_controller.open_link(button.url, False, not self.settings.get_boolean("external-browser"))

    def restore_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        return super().restore_gtk_widget(codeblock, lang)

    def get_article_content(self, url: str):
        if url in self.caches:
            return self.caches[url]
        else:
            scraper = WebsiteScraper(url)
            scraper.parse_article()
            self.caches[url] = scraper
            return scraper

    def get_article(self, button: WebsiteButton):
        article = self.get_article_content(button.url)
        title = article.get_title()
        favicon = article.get_favicon()
        description = article.get_description()[:100]
        def update_button():
            button.title.set_text(title)
            button.description.set_text(description) 
        GLib.idle_add(update_button)
        load_image_with_callback(favicon, lambda pixbuf_loader : button.icon.set_from_pixbuf(pixbuf_loader.get_pixbuf()))
