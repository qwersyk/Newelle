from ..handler import Handler
from ...tools import Tool, ToolResult
from ...ui.widgets.image_generator import ImageGeneratorWidget
from gi.repository import GLib
from threading import Thread
import os
import requests
from PIL import Image
from io import BytesIO

# Lazy import to avoid circular dependency at module level
_mini_app_class = None

def _get_mini_app_class():
    global _mini_app_class
    if _mini_app_class is None:
        from ...ui.widgets.image_generator_mini_app import ImageGeneratorMiniApp
        _mini_app_class = ImageGeneratorMiniApp
    return _mini_app_class


class ImageGeneratorHandler(Handler):
    """Base handler for image generation services.

    Subclasses should override generate_image() to return a URL or file path
    to the generated image. No GTK knowledge is required in subclasses -
    the base class handles all widget interaction.

    The default get_tools() implementation returns a tool that accepts a prompt
    and generates an image.
    """
    key = ""
    schema_key = "image-generator-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.cache_dir = os.path.join(path, "generated_images")
        os.makedirs(self.cache_dir, exist_ok=True)

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        """Generate an image from a prompt.

        Subclasses must override this to implement their specific API call.
        Called from a background thread, so it should NOT interact with GTK.

        Args:
            prompt: The text prompt for image generation
            msg_uuid: Unique message identifier for caching
            output_file: Optional local file path to save the image to.
                         If provided and the result is a URL, the base class
                         downloads it to this path and returns the path instead.
                         Subclasses may also handle this directly.

        Returns:
            str: A URL (http/https) or local file path to the generated image
        """
        raise NotImplementedError("Subclasses must implement generate_image()")

    def _set_image_on_widget(self, widget: ImageGeneratorWidget, image_source: str, msg_uuid: str):
        """Set the image on the widget from a URL or file path.

        Called on the GTK main thread via GLib.idle_add.

        Args:
            widget: The ImageGeneratorWidget to update
            image_source: URL or local file path to the image, or None on error
            msg_uuid: Unique message identifier for caching
        """
        if not image_source:
            widget.show_error("Image generation failed.")
            return

        def on_loaded(success):
            if success:
                widget.save_image(self.cache_path_for(msg_uuid))

        if image_source.startswith(("http://", "https://")):
            widget.set_image_from_url(image_source, on_loaded)
        else:
            widget.set_image_from_path(image_source, on_loaded)

    def generate_and_display(
        self,
        prompt: str,
        widget: ImageGeneratorWidget,
        msg_uuid: str,
        on_done_callback=None,
        on_error_callback=None,
    ):
        """Generate an image and display it on the given widget.

        Runs in a background thread - safe to call from the GTK main thread.
        This is the main public entry point for programmatic image generation.

        Args:
            prompt: The text prompt for image generation
            widget: The ImageGeneratorWidget to display the result on
            msg_uuid: Unique message identifier for caching
            on_done_callback: Optional callback, called on GTK main thread when done
            on_error_callback: Optional callback receiving a user-facing error
                               message on the GTK main thread
        """
        output_path = self.cache_path_for(msg_uuid)

        def generate():
            error_message = None
            try:
                image_source = self.generate_image(prompt, msg_uuid, output_file=output_path)
                if not isinstance(image_source, str) or not image_source.strip():
                    raise RuntimeError("The image provider returned no image.")
                if image_source.startswith(("http://", "https://")):
                    image_source = self._download_image(image_source, output_path)
                elif not os.path.isfile(image_source):
                    raise FileNotFoundError("The generated image file was not created.")
            except Exception as e:
                print(f"Image generation failed: {e}")
                image_source = None
                error_message = str(e).strip() or "Image generation failed."
            GLib.idle_add(
                self._finish_generation,
                widget,
                image_source,
                msg_uuid,
                error_message,
                on_done_callback,
                on_error_callback,
            )

        Thread(target=generate, daemon=True).start()

    def _finish_generation(
        self,
        widget,
        image_source,
        msg_uuid,
        error_message,
        on_done_callback,
        on_error_callback,
    ):
        """Apply a worker result and invoke callbacks on the GTK main thread."""
        try:
            if error_message:
                widget.show_error(error_message)
                if on_error_callback:
                    on_error_callback(error_message)
            else:
                self._set_image_on_widget(widget, image_source, msg_uuid)
        finally:
            if on_done_callback:
                on_done_callback()
        return False

    def _generate_image_tool(self, prompt: str, msg_uuid = None):
        """Default tool function for image generation."""
        widget = ImageGeneratorWidget(width=400, height=400)
        widget.set_prompt(prompt)
        result = ToolResult()
        result.set_widget(widget)
        failed = {"value": False}

        def on_error(message):
            failed["value"] = True
            result.set_output(f"Image generation failed: {message}")

        def on_done():
            if not failed["value"]:
                result.set_output(None)

        self.generate_and_display(
            prompt,
            widget,
            msg_uuid,
            on_done_callback=on_done,
            on_error_callback=on_error,
        )
        return result

    def _download_image(self, url: str, path: str, headers: dict = None, timeout: int = 120, verify: bool = True, proxies: dict = None, auth = None, allow_redirects: bool = True) -> str:
        """Download an image from a URL to a local file path.

        Thread-safe - called from background threads.
        Always converts and saves as PNG to match the expected file extension.

        Args:
            url: The image URL to download
            path: The local file path to save to
            headers: Optional HTTP headers to include in the request
            timeout: Request timeout in seconds (default: 30)
            verify: Whether to verify SSL certificates (default: True)
            proxies: Optional proxy configuration dict
            auth: Optional authentication (tuple or requests.auth object)
            allow_redirects: Whether to follow redirects (default: True)

        Returns:
            str: The local file path on success

        Raises:
            RuntimeError: If the request fails or the response is not an image
        """
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                verify=verify,
                proxies=proxies,
                auth=auth,
                allow_redirects=allow_redirects,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "svg" in content_type:
                with open(path, 'wb') as f:
                    f.write(response.content)
            else:
                img = Image.open(BytesIO(response.content))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                img.save(path, "PNG")
            return path
        except requests.Timeout as e:
            raise RuntimeError("Timed out while downloading the generated image.") from e
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            detail = f" (HTTP {status})" if status else ""
            raise RuntimeError(
                f"Failed to download the generated image{detail}."
            ) from e
        except (OSError, ValueError) as e:
            raise RuntimeError(
                "The image provider returned invalid image data."
            ) from e

    def get_mini_app(self, constants: dict, **kwargs) -> 'Gtk.Box':
        """Create and return a mini app window for image generation.

        The mini app provides a self-contained interface with:
        - A prompt entry field
        - A generate button
        - Image display with save capability
        - Inline editing of the handler's extra settings

        Args:
            constants: The handler constants dict (e.g., AVAILABLE_IMAGE_GENERATORS)
            **kwargs: Additional keyword arguments passed to the window constructor

        Returns:
            Adw.Window: The mini app window (not yet shown)
        """
        MiniAppClass = _get_mini_app_class()
        return MiniAppClass(self, constants, **kwargs)

    def cache_path_for(self, msg_uuid: str) -> str:
        """Return the cache file path for a given message UUID."""
        return os.path.join(self.cache_dir, f"{msg_uuid}.png")

    def _restore_image_tool(self, msg_uuid, prompt: str) -> ToolResult:
        """Default restore function for image generation tool."""
        widget = ImageGeneratorWidget(width=400, height=400)
        widget.set_prompt(prompt)
        cached_path = self.cache_path_for(msg_uuid)
        if os.path.exists(cached_path):
            widget.set_image_from_path(cached_path)
        return ToolResult(widget=widget)

    def get_tools(self) -> list:
        """Return the list of tools exposed by this handler.
        
        Default implementation provides a 'generate_image' tool that 
        accepts a prompt parameter.
        """
        return [Tool(
            "generate_image",
            "Generate an image from a text prompt. Use detailed, descriptive prompts with English words separated by commas.",
            self._generate_image_tool,
            title="Generate Image",
            restore_func=self._restore_image_tool,
            icon_name="insert-image-symbolic",
            default_on=False
        )]
