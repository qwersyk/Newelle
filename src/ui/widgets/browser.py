import gi
import urllib.parse
import threading
import json
import os
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib, GObject, Gio, Adw, GdkPixbuf
from ...ui import load_image_with_callback
from ...utility.website_scraper import WebsiteScraper

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
    
    def __init__(self, starting_url="https://www.google.com", search_string="https://www.google.com/search?q=%s", session_file=None, **kwargs):
        """
        Initialize the browser widget.
        
        Args:
            starting_url (str): The initial URL to load
            search_string (str): The search engine query string with %s placeholder for the search term
            **kwargs: Additional keyword arguments passed to Gtk.Box
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.session_file = session_file
        self.starting_url = starting_url
        self.search_string = search_string
        self.current_url = ""
        self.current_title = ""
        self.current_favicon = None
        self.loading = threading.Semaphore(1)

        self.favicon_pixbuf : GdkPixbuf.Pixbuf | None = None

        # Setup Webview with Persistent Session
        self._setup_webview()
        self._build_ui()
        
        # Load the starting URL
        if self.session_file:
            self.load_session(self.session_file, lambda: self.webview.load_uri(self.starting_url))
        else:
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
        # Add webview to scrolled window
        self.scrolled_window.set_child(self.webview)
    
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
        self.settings.set_user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Create network session
        if self.session_file:
            data_dir = self.session_file + "_data"
            cache_dir = self.session_file + "_cache"
            self.network_session = WebKit.NetworkSession.new(data_dir, cache_dir)
            
            # Explicitly configure cookie persistence
            cookie_manager = self.network_session.get_cookie_manager()
            cookie_file = os.path.join(data_dir, "cookies.sqlite")
            cookie_manager.set_persistent_storage(cookie_file, WebKit.CookiePersistentStorage.SQLITE)
        else:
             self.network_session = WebKit.NetworkSession.new(None, None)

        # Create user content manager for potential script injection
        self.user_content_manager = WebKit.UserContentManager()
        
        # Create the webview
        self.webview = WebKit.WebView(
            network_session=self.network_session,
            user_content_manager=self.user_content_manager
        )
        self.webview.set_settings(self.settings)
        
        # Connect signals
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("notify::uri", self._on_uri_changed)
        self.webview.connect("notify::title", self._on_title_changed)
        
    
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
            self.loading.release()
            self.loading = threading.Semaphore()
            self.loading.acquire()
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
            self.article = WebsiteScraper(uri)
            threading.Thread(target=self.download_favicon).start()
            self.loading.release() 
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
        self.article.parse_article()
        favicon = self.article.get_favicon()
        if self.last_favicon != favicon:
            load_image_with_callback(favicon, lambda pixbuf_loader : self.on_favicon_loaded(pixbuf_loader))
            self.last_favicon = favicon

    def on_favicon_loaded(self, loader: GdkPixbuf.PixbufLoader):
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
        if self.session_file:
            self.save_session(self.session_file)

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
        
        sem = threading.Semaphore()
        def callback(html_content, error):
            result['html'] = html_content
            result['error'] = error
            result['done'] = True
            sem.release()
        sem.acquire() 
        self.get_page_html(callback)
        # Wait for the result (with timeout)
        sem.acquire()
        sem.release()  
        if result['error']:
            print(f"Error getting HTML: {result['error']}")
            return None
        
        return result['html']

    def _on_attach_clicked(self, button):
        """Handle attach button click."""
        self.emit('attach-clicked')

    def save_session(self, file_path):
        """
        Save current session information to a file.
        
        Args:
            file_path (str): Path where to save the session data
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Create session data dictionary
            session_data = {
                'current_url': self.current_url,
                'current_title': self.current_title,
                'starting_url': self.starting_url,
                'search_string': self.search_string
            }
            
            # Save session data to JSON file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            
            # Manually save all cookies (including session cookies) to a JSON file
            # This is necessary because set_persistent_storage might drop session cookies
            cookie_manager = self.network_session.get_cookie_manager()
            
            def on_cookies_retrieved(manager, result):
                try:
                    cookies = manager.get_all_cookies_finish(result)
                    cookie_list = []
                    for cookie in cookies:
                        cookie_dict = {
                            "name": cookie.get_name(),
                            "value": cookie.get_value(),
                            "domain": cookie.get_domain(),
                            "path": cookie.get_path(),
                            "secure": cookie.get_secure(),
                            "http_only": cookie.get_http_only(),
                            "expires": cookie.get_expires().format_iso8601() if cookie.get_expires() else None
                        }
                        cookie_list.append(cookie_dict)
                    
                    cookies_json_path = file_path + ".cookies.json"
                    with open(cookies_json_path, 'w', encoding='utf-8') as f:
                        json.dump(cookie_list, f, indent=2)
                        
                except Exception as e:
                    print(f"Error saving cookies to JSON: {e}")

            cookie_manager.get_all_cookies(None, on_cookies_retrieved)
            
        except Exception as e:
            print(f"Error saving session: {e}")

    def load_session(self, file_path, on_loaded_callback=None):
        """
        Load session information from a file.
        
        Args:
            file_path (str): Path to the session data file
            on_loaded_callback (callable): Function to call when loading (including cookies) is complete
        """
        try:
            # Check if session file exists
            if not os.path.exists(file_path):
                print(f"Session file not found: {file_path}")
                if on_loaded_callback:
                    on_loaded_callback()
                return
            
            # Load session data from JSON file
            with open(file_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            
            # Restore session settings
            if 'search_string' in session_data:
                self.search_string = session_data['search_string']
            
            # Manually load cookies from JSON file
            cookies_json_path = file_path + ".cookies.json"
            if os.path.exists(cookies_json_path):
                with open(cookies_json_path, 'r', encoding='utf-8') as f:
                    cookie_list = json.load(f)
                    
                cookie_manager = self.network_session.get_cookie_manager()
                from gi.repository import Soup
                
                # Counter for cookies processed
                cookies_to_process = 0
                processed_cookies = 0
                
                valid_cookies = []
                
                for c_data in cookie_list:
                    try:
                        name = c_data.get("name")
                        value = c_data.get("value")
                        domain = c_data.get("domain")
                        path = c_data.get("path")
                        
                        if name and value and domain and path:
                            cookie = Soup.Cookie.new(name, value, domain, path, -1)
                            if c_data.get("secure"):
                                cookie.set_secure(True)
                            if c_data.get("http_only"):
                                cookie.set_http_only(True)
                            if c_data.get("expires"):
                                try:
                                    date = GLib.DateTime.new_from_iso8601(c_data["expires"], None)
                                    cookie.set_expires(date)
                                except:
                                    pass # Ignore expiry parsing errors, treat as session cookie
                            valid_cookies.append(cookie)        
                    except Exception as e:
                        print(f"Error restoring cookie: {e}")

                cookies_to_process = len(valid_cookies)
                
                if cookies_to_process == 0:
                    if on_loaded_callback:
                        on_loaded_callback()
                    return

                def on_cookie_added(manager, output):
                   nonlocal processed_cookies
                   processed_cookies += 1
                   if processed_cookies >= cookies_to_process:
                       if on_loaded_callback:
                           on_loaded_callback()
                           
                for cookie in valid_cookies:
                    cookie_manager.add_cookie(cookie, None, on_cookie_added)
                    
            else:
                 if on_loaded_callback:
                    on_loaded_callback()

        except Exception as e:
            print(f"Error loading session: {e}")
            if on_loaded_callback:
                on_loaded_callback()
