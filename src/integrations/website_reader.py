from gi.repository import Gtk, GLib, GdkPixbuf
from ..extensions import NewelleExtension
from ..ui.widgets import WebsiteButton
import threading 
from newspaper import Article

class WebsiteReader(NewelleExtension):
    id = "website-reader"
    name = "Website Reader"

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

        return history, prompts

    def get_gtk_widget(self, codeblock: str, lang: str) -> Gtk.Widget | None:
        website_url = codeblock
         
        button = WebsiteButton(website_url)
        threading.Thread(target=self.get_article, args=(button,)).start()
        return button

    def get_article(self, button: WebsiteButton):
        article = Article(button.url)
        article.download()
        article.parse()
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
            for chunk in response.iter_content(chunk_size=8192): #Load in chunks to avoid consuming too much memory for large files
                pixbuf_loader.write(chunk)
        except Exception as e:
            print("Exception generating the image: " + str(e))

    def on_area_prepared(self, loader: GdkPixbuf.PixbufLoader, image: Gtk.Image):
        # Function runs when the image loaded. Remove the spinner and open the image
        image.set_from_pixbuf(loader.get_pixbuf())
