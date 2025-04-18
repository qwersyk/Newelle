from gi.repository import Gtk, GLib, GdkPixbuf
from ..extensions import NewelleExtension
from ..ui.widgets import WebsiteButton
import threading
from ..utility.message_chunk import get_message_chunks
from newspaper import Article

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
                lines += ["```website", "" + line.lstrip("#"), "```"]
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
            docs.append("-----\nSource: " + website + "\n" + article.text)
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
        threading.Thread(target=self.get_article, args=(button,)).start()
        return button

    def restore_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        print("restore")
        return super().restore_gtk_widget(codeblock, lang)

    def get_article_content(self, url: str):
        if url in self.caches:
            return self.caches[url]
        else:
            article = Article(url)
            article.download()
            article.parse()
            self.caches[url] = article
            return article

    def get_article(self, button: WebsiteButton):
        article = self.get_article_content(button.url)
        title = article.title
        favicon = article.meta_favicon
        if not favicon.startswith("http") and not favicon.startswith("https"):
            from urllib.parse import urlparse, urljoin
            base_url = urlparse(button.url).scheme + "://" + urlparse(button.url).netloc
            favicon = urljoin(base_url, favicon)
        description = article.meta_description[:100]
        def update_button():
            button.title.set_text(title)
            button.description.set_text(description) 
        GLib.idle_add(update_button)
        threading.Thread(target=self.load_image, args=(favicon, button.icon)).start()


    def load_image(self, url, image: Gtk.Image):
        import requests
        # Create a pixbuf loader that will load the image
        pixbuf_loader = GdkPixbuf.PixbufLoader()
        pixbuf_loader.connect("area-prepared", self.on_area_prepared, image)
        try:
            response = requests.get(url, stream=True) #stream = True prevent download the whole file into RAM
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024): #Load in chunks to avoid consuming too much memory for large files
                pixbuf_loader.write(chunk)
        except Exception as e:
            print("Exception generating the image: " + str(e))

    def on_area_prepared(self, loader: GdkPixbuf.PixbufLoader, image: Gtk.Image):
        # Function runs when the image loaded. Remove the spinner and open the image
        image.set_from_pixbuf(loader.get_pixbuf())
