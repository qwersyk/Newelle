from gi.repository import Gtk, GdkPixbuf, GLib
import requests

def apply_css_to_widget(widget, css_string):
    provider = Gtk.CssProvider()
    context = widget.get_style_context()

    # Load the CSS from the string
    provider.load_from_data(css_string.encode())

    # Add the provider to the widget's style context
    context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

def load_image_with_callback(url, callback, error_callback=None):
    """
    Load an image from URL and call the callback with the pixbuf loader when complete.
    
    Args:
        url (str): The URL of the image to load
        callback (callable): Function to call when image is loaded successfully.
                           Should accept (pixbuf_loader) as argument
        error_callback (callable, optional): Function to call on error.
                                           Should accept (exception) as argument
    """ 
    def _load_image():
        pixbuf_loader = GdkPixbuf.PixbufLoader()
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            for chunk in response.iter_content(chunk_size=1024):
                pixbuf_loader.write(chunk)
            
            pixbuf_loader.close()
            
            # Schedule callback on main thread
            GLib.idle_add(callback, pixbuf_loader)
            
        except Exception as e:
            print(f"Exception loading image: {e}")
            if error_callback:
                GLib.idle_add(error_callback, e)
    
    # Run the loading in a separate thread to avoid blocking the UI
    import threading
    thread = threading.Thread(target=_load_image)
    thread.daemon = True
    thread.start()
