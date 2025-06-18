from gi.repository import Gtk, GLib, GdkPixbuf
from ..extensions import NewelleExtension
from ..ui.widgets import WebsiteButton
import threading
from ..utility.message_chunk import get_message_chunks
from ..ui import load_image_with_callback
from ..utility.website_scraper import WebsiteScraper

CHUNK_SIZE = 512 
MAX_CONTEXT = 5000

class WebsiteReader(NewelleExtension):
    id = "website-reader"
    name = "Website Reader"

    def __init__(self, pip_path: str, extension_path: str, settings):
        super().__init__(pip_path, extension_path, settings)
        self.caches = {}
    def get_replace_codeblocks_langs(self) -> list:
        return ["website"]
   
    def preprocess_history(self, history: list, prompts: list) -> tuple[list, list]:
        user_message = history[-1]["Message"]
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
        history[-1]["Message"] = "\n".join(lines)

        # Find articles
        websites = []
        for message in history:
            for chunk in get_message_chunks(message["Message"]):
                if chunk.type == "codeblock" and chunk.lang == "website":
                    websites.append(chunk.text)
        docs = []
        for website in websites:
            article = self.get_article_content(website)
            docs.append("-----\nSource: " + website + "\n" + article.get_text())
        if sum(len(doc) for doc in docs) < MAX_CONTEXT:
            prompts += docs
        elif self.rag is not None:
            print("Using RAG")
            rag_docs = ["text:" + doc for doc in docs]
            index = self.rag.build_index(rag_docs, CHUNK_SIZE)
            results = index.query(user_message)
            prompts += ["Content from previous websites:\n" + "\n".join(results)]
        else:
            prompts.append("Content from previous websites:\n" + "\n".join(docs)[:MAX_CONTEXT])
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


