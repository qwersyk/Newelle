import gi
import urllib.parse
from newspaper import Article
import threading
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib, GObject, Gio, Adw, GdkPixbuf

class BrowserWidget(Gtk.Box):
    """
    A simple web browser widget using WebKit 6.0
    """
    last_favicon = ""
    # Define signals
    __gsignals__ = {
        'page-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
        'attach-clicked': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'favicon-changed': (GObject.SignalFlags.RUN_FIRST, None, (object,))
    }
    
    def __init__(self, starting_url="https://www.google.com", search_string="https://www.google.com/search?q=%s", **kwargs):
        """
        Initialize the browser widget.
        
        Args:
            starting_url (str): The initial URL to load
            search_string (str): The search engine query string with %s placeholder for the search term
            **kwargs: Additional keyword arguments passed to Gtk.Box
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        
        self.starting_url = starting_url
        self.search_string = search_string
        self.current_url = ""
        self.current_title = ""
        self.current_favicon = None
        
        self.favicon_pixbuf : GdkPixbuf | None = None
        self._build_ui()
        self._setup_webview()
        
        # Load the starting URL
        self.webview.load_uri(self.starting_url)
    
    def _build_ui(self):
        """Build the user interface."""
        # Create toolbar
        self.toolbar = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False, show_end_title_buttons=False)
        
        # Navigation buttons
        self.back_button = Gtk.Button()
        self.back_button.set_icon_name("go-previous-symbolic")
        self.back_button.set_tooltip_text("Go Back")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.toolbar.pack_start(self.back_button)
        
        self.forward_button = Gtk.Button()
        self.forward_button.set_icon_name("go-next-symbolic")
        self.forward_button.set_tooltip_text("Go Forward")
        self.forward_button.connect("clicked", self._on_forward_clicked)
        self.toolbar.pack_start(self.forward_button)
        
        self.refresh_button = Gtk.Button()
        self.refresh_button.set_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Refresh")
        self.refresh_button.connect("clicked", self._on_refresh_clicked)
        self.toolbar.pack_start(self.refresh_button)
        
        # Create spinner for loading state
        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_size_request(16, 16)  # Icon size
        
        # URL/Search entry
        self.url_entry = Gtk.Entry()
        self.url_entry.set_hexpand(True)
        self.url_entry.set_placeholder_text("Enter URL or search term...")
        self.url_entry.connect("activate", self._on_url_activate)
        self.toolbar.set_title_widget(self.url_entry)
        
        # Home button
        self.home_button = Gtk.Button()
        self.home_button.set_icon_name("go-home-symbolic")
        self.home_button.set_tooltip_text("Home")
        self.home_button.connect("clicked", self._on_home_clicked)
        self.toolbar.pack_end(self.home_button)
        
        # Attach button
        self.attach_button = Gtk.Button()
        self.attach_button.set_icon_name("attach-symbolic")
        self.attach_button.set_tooltip_text("Attach")
        self.attach_button.connect("clicked", self._on_attach_clicked)
        self.toolbar.pack_end(self.attach_button)
        
        self.append(self.toolbar)
        
        # Create scrolled window for webview
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        self.append(self.scrolled_window)
    
    def _setup_webview(self):
        """Set up the WebKit webview."""
        # Create WebKit web context and settings
        self.web_context = WebKit.WebContext.get_default()
        self.settings = WebKit.Settings()
        
        # Enable some useful features
        self.settings.set_enable_javascript(True)
        self.settings.set_enable_html5_database(True)
        self.settings.set_enable_html5_local_storage(True)
        self.settings.set_enable_developer_extras(False)
        self.settings.set_enable_page_cache(True)
        
        # Create user content manager for potential script injection
        self.user_content_manager = WebKit.UserContentManager()
        
        # Create the webview
        self.webview = WebKit.WebView(
            web_context=self.web_context, user_content_manager=self.user_content_manager
        )
        self.webview.set_settings(self.settings)
        
        # Connect signals
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("notify::uri", self._on_uri_changed)
        self.webview.connect("notify::title", self._on_title_changed)
        
        # Add webview to scrolled window
        self.scrolled_window.set_child(self.webview)
    
    def _on_back_clicked(self, button):
        """Handle back button click."""
        if self.webview.can_go_back():
            self.webview.go_back()
    
    def _on_forward_clicked(self, button):
        """Handle forward button click."""
        if self.webview.can_go_forward():
            self.webview.go_forward()
    
    def _on_refresh_clicked(self, button):
        """Handle refresh button click."""
        # Check if currently loading by seeing if spinner is the button's child
        current_child = self.refresh_button.get_child()
        if isinstance(current_child, Gtk.Spinner):
            # If currently loading, stop the loading
            self.webview.stop_loading()
        else:
            # If not loading, refresh the page
            self.webview.reload()
    
    def _on_home_clicked(self, button):
        """Handle home button click."""
        self.webview.load_uri(self.starting_url)
    
    def _on_url_activate(self, entry):
        """Handle URL entry activation."""
        text = entry.get_text().strip()
        if not text:
            return
        
        # Check if it's a URL or search term
        if self._is_url(text):
            # Ensure it has a protocol
            if not text.startswith(('http://', 'https://', 'file://', 'ftp://')):
                text = 'https://' + text
            self.webview.load_uri(text)
        else:
            # It's a search term, use the search string
            search_url = self.search_string % urllib.parse.quote_plus(text)
            self.webview.load_uri(search_url)
    
    def _is_url(self, text):
        """Check if text looks like a URL."""
        # Simple heuristic: contains a dot and no spaces, or starts with protocol
        return ('.' in text and ' ' not in text) or text.startswith(('http://', 'https://', 'file://', 'ftp://'))
    
    def _on_load_changed(self, webview, load_event):
        """Handle load changed event."""
        if load_event == WebKit.LoadEvent.STARTED:
            # Replace button content with spinner
            self.refresh_button.set_child(self.loading_spinner)
            self.loading_spinner.start()
            self.refresh_button.set_tooltip_text("Stop Loading")
        elif load_event == WebKit.LoadEvent.FINISHED:
            # Replace spinner with refresh icon
            self.loading_spinner.stop()
            self.refresh_button.set_child(None)
            self.refresh_button.set_icon_name("view-refresh-symbolic")
            self.refresh_button.set_tooltip_text("Refresh")
            uri = self.webview.get_uri()
            self.article = Article(uri)
            threading.Thread(target=self.download_favicon).start()
            
        # Update navigation buttons
        self.back_button.set_sensitive(webview.can_go_back())
        self.forward_button.set_sensitive(webview.can_go_forward())
    
    def _on_uri_changed(self, webview, param):
        """Handle URI change."""
        uri = webview.get_uri()
        if uri:
            self.current_url = uri
            self.url_entry.set_text(uri)
            self._emit_page_changed()
    
    def download_favicon(self):
        """Download the page's favicon."""
        html = self.get_page_html_sync()
        self.article.set_html(html)
        self.article.parse()
        favicon = self.article.meta_favicon
        if not favicon.startswith("http") and not favicon.startswith("https"):
            from urllib.parse import urlparse, urljoin
            base_url = urlparse(self.article.url).scheme + "://" + urlparse(self.article.url).netloc
            favicon = urljoin(base_url, favicon)
        if self.last_favicon != favicon:
            self.load_image(favicon)
            self.last_favicon = favicon

    def load_image(self, url):
        print(url)
        import requests
        # Create a pixbuf loader that will load the image
        pixbuf_loader = GdkPixbuf.PixbufLoader()
        pixbuf_loader.connect("area-prepared", self.on_area_prepared)
        try:
            response = requests.get(url, stream=True) #stream = True prevent download the whole file into RAM
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024): #Load in chunks to avoid consuming too much memory for large files
                pixbuf_loader.write(chunk)
            pixbuf_loader.close()
        except Exception as e:
            print("Exception generating the image: " + str(e))

    def on_area_prepared(self, loader: GdkPixbuf.PixbufLoader):
        # Function runs when the image loaded. Remove the spinner and open the image
        self.favicon_pixbuf = loader.get_pixbuf()
        self.emit('favicon-changed', self.favicon_pixbuf)
 
    def _on_title_changed(self, webview, param):
        """Handle title change."""
        title = webview.get_title()
        if title:
            self.current_title = title
            self._emit_page_changed()
     
    def _emit_page_changed(self):
        """Emit the page-changed signal with current page information."""
        self.emit('page-changed', self.current_url, self.current_title, self.current_favicon)
    
    def navigate_to(self, url):
        """Navigate to a specific URL."""
        self.webview.load_uri(url)
    
    def search(self, query):
        """Perform a search using the configured search string."""
        search_url = self.search_string % urllib.parse.quote_plus(query)
        self.webview.load_uri(search_url)
    
    def get_current_url(self):
        """Get the current URL."""
        return self.current_url
    
    def get_current_title(self):
        """Get the current page title."""
        return self.current_title
    
    def get_current_favicon(self):
        """Get the current page favicon."""
        return self.current_favicon
    
    def set_search_string(self, search_string):
        """Set a new search string."""
        self.search_string = search_string

    def get_page_html(self, callback):
        """
        Get the HTML content of the current page.
        
        Args:
            callback (callable): Function to call with the HTML content as a string.
                                The callback should accept two parameters: (html_content, error)
                                where html_content is the HTML string and error is None on success,
                                or html_content is None and error contains the error message.
        """
        def on_javascript_finished(webview, result, user_data):
            try:
                # Get the JavaScript result
                js_result = webview.evaluate_javascript_finish(result)
                if js_result:
                    html_content = js_result.to_string()
                    callback(html_content, None)
                else:
                    callback(None, "Failed to get HTML content")
            except Exception as e:
                callback(None, str(e))
        
        # Execute JavaScript to get the page HTML
        javascript_code = "document.documentElement.outerHTML;"
        self.webview.evaluate_javascript(
            javascript_code,
            -1,  # length
            None,  # world_name
            None,  # source_uri
            None,  # cancellable
            on_javascript_finished,
            None   # user_data
        )

    def get_page_html_sync(self):
        """
        Get the HTML content of the current page synchronously.
        
        Returns:
            str: The HTML content of the page, or None if failed.
            
        Note: This is a blocking operation and should be used carefully.
        """
        import time
        
        result = {'html': None, 'error': None, 'done': False}
        
        def callback(html_content, error):
            result['html'] = html_content
            result['error'] = error
            result['done'] = True
        
        self.get_page_html(callback)
        
        # Wait for the result (with timeout)
        timeout = 5  # 5 seconds timeout
        start_time = time.time()
        while not result['done'] and (time.time() - start_time) < timeout:
            # Process pending GTK events
            time.sleep(0.01)
        
        if result['error']:
            print(f"Error getting HTML: {result['error']}")
            return None
        
        return result['html']

    def _on_attach_clicked(self, button):
        """Handle attach button click."""
        self.emit('attach-clicked')
