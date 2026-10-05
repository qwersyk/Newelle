import base64
from io import BytesIO

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
import requests


_IMAGE_REQUEST_HEADERS = {
    "Accept": "image/avif,image/webp,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _prepare_image_data(data, content_type=""):
    """Validate downloaded image bytes before decoding them."""
    stripped_data = data.lstrip()
    normalized_content_type = content_type.partition(";")[0].strip().lower()
    if (
        normalized_content_type in ("text/html", "application/xhtml+xml")
        or stripped_data.startswith((b"<!DOCTYPE html", b"<!doctype html", b"<html"))
    ):
        raise ValueError("The image URL returned an HTML document")

    if not data:
        raise ValueError("The image URL returned an empty response")

    return data


class _LoadedImage:
    """Preserve the callback interface formerly provided by PixbufLoader."""

    def __init__(self, pixbuf):
        self._pixbuf = pixbuf

    def get_pixbuf(self):
        return self._pixbuf


def _pixbuf_from_image_data(data, content_type=""):
    """Decode image bytes without starting the external glycin loader."""
    data = _prepare_image_data(data, content_type)
    normalized_content_type = content_type.partition(";")[0].strip().lower()
    stripped_data = data.lstrip()

    if (
        normalized_content_type == "image/svg+xml"
        or b"<svg" in stripped_data[:1024].lower()
    ):
        import gi

        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg

        pixbuf = Rsvg.Handle.new_from_data(data).get_pixbuf()
        if pixbuf is None:
            raise ValueError("Could not render the SVG image")
        return pixbuf

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image.seek(0)
        image.load()
        image = image.convert("RGBA")
        width, height = image.size
        pixel_bytes = GLib.Bytes.new(image.tobytes())

    return GdkPixbuf.Pixbuf.new_from_bytes(
        pixel_bytes,
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        width,
        height,
        width * 4,
    )


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
                           Its argument exposes get_pixbuf().
        error_callback (callable, optional): Function to call on error.
                                           Should accept (exception) as argument
    """ 
    def _load_image():
        try:
            with requests.get(
                url,
                headers=_IMAGE_REQUEST_HEADERS,
                timeout=(5, 20),
            ) as response:
                response.raise_for_status()
                image_data = response.content
                content_type = response.headers.get("Content-Type", "")

            loaded_image = _LoadedImage(
                _pixbuf_from_image_data(image_data, content_type)
            )

            # Schedule callback on main thread
            GLib.idle_add(callback, loaded_image)

        except Exception as e:
            print(f"Exception loading image from {url}: {e}")
            if error_callback:
                GLib.idle_add(error_callback, e)
    
    # Run the loading in a separate thread to avoid blocking the UI
    import threading
    thread = threading.Thread(target=_load_image)
    thread.daemon = True
    thread.start()


def append_image_codeblock(text, box):
    """Append image-block contents to a GTK box on the main thread."""
    for line in text.split("\n"):
        if not line.strip(): continue
        image = Gtk.Image(css_classes=["image"])
        if line.startswith("data:image/"):
            try:
                header_end = line.index(",")
                data = line[header_end + 1:]
                raw_data = base64.b64decode(data)
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(raw_data))
                image.set_from_paintable(texture)
                box.append(image)
            except Exception:
                try:
                    header_end = line.index(",")
                    data = line[header_end + 1:]
                    raw_data = base64.b64decode(data)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(raw_data)
                    loader.close()
                    image.set_from_pixbuf(loader.get_pixbuf())
                    box.append(image)
                except Exception:
                    pass
        elif line.startswith(("https://", "http://")):
            img = image
            load_image_with_callback(line, lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf()))
            box.append(image)
        else:
            image.set_from_file(line)
            box.append(image)
