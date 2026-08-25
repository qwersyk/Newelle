from gi.repository import GdkPixbuf, Gtk, Gdk, GLib
from ...ui import load_image_with_callback
import os
from threading import Thread


class ImageGeneratorWidget(Gtk.Box):
    """
    A sophisticated image widget with loading animation and save capabilities
    """

    def __init__(self, width=400, height=400):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.width = width
        self.height = height
        self.current_pixbuf = None
        self.current_url = None
        self.prompt = None  # Store the original prompt

        self.set_margin_start(5)
        
        # Set up CSS for loading animation
        self.setup_css()

        # Create the main container
        self.image_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.image_container.set_size_request(self.width, self.height)
        self.image_container.set_halign(Gtk.Align.START)
        self.image_container.set_valign(Gtk.Align.CENTER)


        # Create loading overlay
        self.loading_overlay = Gtk.Overlay()

        # Create placeholder for loading state
        self.loading_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.loading_container.set_halign(Gtk.Align.CENTER)
        self.loading_container.set_valign(Gtk.Align.CENTER)
        self.loading_container.add_css_class("loading-container")

        # Loading animation elements
        self.loading_pulse = Gtk.Box()
        self.loading_pulse.set_size_request(60, 60)
        self.loading_pulse.add_css_class("loading-pulse")

        self.loading_text = Gtk.Label(label="Loading image...")
        self.loading_text.add_css_class("loading-text")

        self.loading_container.append(self.loading_pulse)
        self.loading_container.append(self.loading_text)

        # Keep failures visible in-place instead of replacing the generated
        # image with an unexplained missing-image icon.
        self.error_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=24,
            margin_end=24,
        )
        self.error_container.set_halign(Gtk.Align.CENTER)
        self.error_container.set_valign(Gtk.Align.CENTER)
        self.error_container.add_css_class("error-container")

        self.error_icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        self.error_icon.set_pixel_size(48)
        self.error_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.error_label.set_max_width_chars(48)
        self.error_container.append(self.error_icon)
        self.error_container.append(self.error_label)
        self.error_container.set_visible(False)

        # Create the actual image widget
        self.image = Gtk.Image()
        self.image.set_size_request(self.width, self.height)
        self.image.set_pixel_size(max(self.width, self.height))

        # Create the image overlay
        self.overlay_buttons = Gtk.Box(valign=Gtk.Align.START, halign=Gtk.Align.END)
        self.copy_button = Gtk.Button(icon_name="edit-copy-symbolic", css_classes=["success", "flat"])
        self.copy_button.set_tooltip_text("Copy prompt to clipboard")
        self.copy_button.connect("clicked", self.on_copy_clicked)
        self.overlay_buttons.append(self.copy_button)

        self.save_button = Gtk.Button(icon_name="document-save-symbolic", css_classes=["accent", "flat"])
        self.save_button.set_tooltip_text("Save image to file")
        self.save_button.connect("clicked", self.on_save_clicked)
        self.overlay_buttons.append(self.save_button)

        self.image_overlay = Gtk.Overlay()
        self.image_overlay.set_child(self.image)
        self.image_overlay.add_overlay(self.overlay_buttons)

        # On hover show buttons
        ev = Gtk.EventControllerMotion.new()
        ev.connect(
            "enter",
            lambda x, y, data: self.overlay_buttons.set_visible(
                self.current_pixbuf is not None
            ),
        )
        ev.connect("leave", lambda data: self.overlay_buttons.set_visible(False))
        self.image_overlay.add_controller(ev)

        # Setup overlay
        self.loading_overlay.set_child(self.image_overlay)
        self.loading_overlay.add_overlay(self.loading_container)
        self.loading_overlay.add_overlay(self.error_container)

        self.image_container.append(self.loading_overlay)
        self.append(self.image_container)

        # Initially show loading state
        self.show_loading(True)

    def setup_css(self):
        """Setup CSS for loading animations"""
        css_provider = Gtk.CssProvider()
        css = """
        .loading-container {
            border-radius: 12px;
            padding: 24px;
        }

        .loading-pulse {
            background: linear-gradient(45deg, #6366f1, #8b5cf6, #06b6d4, #10b981);
            background-size: 300% 300%;
            border-radius: 12px;
            width: 60px;
            height: 60px;
            min-width: 60px;
            min-height: 60px;
            max-width: 60px;
            max-height: 60px;
            animation: loading-pulse 2s ease-in-out infinite, loading-gradient 3s ease-in-out infinite;
        }

        .loading-text {
            margin-top: 16px;
            font-weight: 500;
            opacity: 0.8;
            animation: loading-fade 1.5s ease-in-out infinite alternate;
        }

        .error-container {
            border-radius: 12px;
            padding: 24px;
        }

        .error-container label {
            color: @error_color;
        }

        @keyframes loading-pulse {
            0%, 100% {
                transform: scale(1);
                box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.4);
            }
            50% {
                transform: scale(1.1);
                box-shadow: 0 0 0 10px rgba(99, 102, 241, 0);
            }
        }

        @keyframes loading-gradient {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        @keyframes loading-fade {
            0% { opacity: 0.5; }
            100% { opacity: 1; }
        }

        .image-loaded {
            transition: all 0.7s ease-in-out;
            opacity: 1;
        }

        .image-loading {
            filter: blur(2px);
            opacity: 0.7;
            transition: all 0.3s ease-in-out;
        }
        """

        css_provider.load_from_data(css.encode())

        # Apply CSS to default display
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display,
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def set_size(self, width, height):
        """Set the size of the image widget"""
        self.width = width
        self.height = height
        self.image_container.set_size_request(width, height)
        self.image.set_size_request(width, height)

    def show_loading(self, show=True):
        """Show or hide the loading animation"""
        self.loading_container.set_visible(show)
        if show:
            self.error_container.set_visible(False)
            self.overlay_buttons.set_visible(False)
            self.image.add_css_class("image-loading")
        else:
            self.image.remove_css_class("image-loading")
            self.image.add_css_class("image-loaded")

    def show_error(self, message):
        """Stop loading and display a generation or image-loading error."""
        self.show_loading(False)
        self.current_pixbuf = None
        self.current_url = None
        self.image.clear()
        self.overlay_buttons.set_visible(False)
        self.error_label.set_label(message or "Image generation failed.")
        self.error_container.set_visible(True)

    def set_image_from_url(self, url, callback=None):
        """Load image from URL with loading animation"""
        self.current_url = url
        self.show_loading(True)
        def load_complete_callback(pixbuf_loader):
            self.current_pixbuf = pixbuf_loader.get_pixbuf()
            # Scale pixbuf to fit widget size while maintaining aspect ratio
            scaled_pixbuf = self.scale_pixbuf_to_fit(self.current_pixbuf)
            self.image.set_from_pixbuf(scaled_pixbuf)
            self.show_loading(False)
            self.error_container.set_visible(False)
            if callback:
                callback(True)

        def load_error_callback():
            self.show_error("The generated image could not be loaded.")
            if callback:
                callback(False)

        try:
            load_image_with_callback(url, load_complete_callback)
        except Exception as e:
            print(f"Error loading image from URL: {e}")
            load_error_callback()

    def set_image_from_path(self, path, callback=None):
        """Load image from local file path using Pillow to avoid Flatpak glycin loader issues."""
        self.show_loading(True)

        def load_in_thread():
            try:
                from PIL import Image as PILImage
                from gi.repository import GLib

                img = PILImage.open(path)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")

                width, height = img.size
                raw_data = img.tobytes()
                rowstride = width * 4

                gbytes = GLib.Bytes.new(raw_data)
                pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                    gbytes,
                    GdkPixbuf.Colorspace.RGB,
                    True,
                    8,
                    width,
                    height,
                    rowstride,
                )
                self.current_pixbuf = pixbuf

                def update_ui():
                    scaled_pixbuf = self.scale_pixbuf_to_fit(pixbuf)
                    self.image.set_from_pixbuf(scaled_pixbuf)
                    self.show_loading(False)
                    self.error_container.set_visible(False)
                    if callback:
                        callback(True)
                    return False

                GLib.idle_add(update_ui)

            except Exception as e:
                print(f"Error loading image from path: {e}")

                def show_error():
                    self.show_error("The generated image could not be loaded.")
                    if callback:
                        callback(False)
                    return False

                from gi.repository import GLib
                GLib.idle_add(show_error)

        thread = Thread(target=load_in_thread)
        thread.daemon = True
        thread.start()

    def scale_pixbuf_to_fit(self, pixbuf):
        """Scale pixbuf to fit widget size while maintaining aspect ratio"""
        if not pixbuf:
            return pixbuf

        orig_width = pixbuf.get_width()
        orig_height = pixbuf.get_height()

        # Calculate scaling factor to fit within widget bounds
        scale_x = self.width / orig_width
        scale_y = self.height / orig_height
        scale = min(scale_x, scale_y)

        new_width = int(orig_width * scale)
        new_height = int(orig_height * scale)

        return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)

    def save_image(self, file_path, format="png"):
        """Save the current image to a file"""
        if not self.current_pixbuf:
            raise ValueError("No image loaded to save")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        try:
            from PIL import Image as PILImage
            width = self.current_pixbuf.get_width()
            height = self.current_pixbuf.get_height()
            rowstride = self.current_pixbuf.get_rowstride()
            has_alpha = self.current_pixbuf.get_has_alpha()
            raw = self.current_pixbuf.read_pixel_bytes().get_data()
            mode = "RGBA" if has_alpha else "RGB"
            img = PILImage.frombytes(mode, (width, height), raw, "raw", mode, rowstride)
            pil_format = "PNG" if format == "png" else "JPEG" if format == "jpeg" else format.upper()
            img.save(file_path, pil_format)
        except ImportError:
            self.current_pixbuf.savev(file_path, format, [], [])
        return True

    def download_and_save(self, save_path, format="png", callback=None):
        """Download current URL and save to path"""
        if not self.current_url:
            raise ValueError("No URL set to download")

        def download_complete(success):
            if success:
                try:
                    self.save_image(save_path, format)
                    if callback:
                        callback(True, save_path)
                except Exception as e:
                    print(f"Error saving image: {e}")
                    if callback:
                        callback(False, str(e))
            else:
                if callback:
                    callback(False, "Failed to load image")

        # If image is already loaded, save it directly
        if self.current_pixbuf:
            try:
                self.save_image(save_path, format)
                if callback:
                    callback(True, save_path)
            except Exception as e:
                if callback:
                    callback(False, str(e))
        else:
            # Load image first, then save
            self.set_image_from_url(self.current_url, download_complete)

    def on_save_clicked(self, button):
        """Handle save button click - open file chooser and save image as PNG"""
        if not self.current_pixbuf:
            # Show error dialog if no image is loaded
            dialog = Gtk.AlertDialog()
            dialog.set_message("No image to save")
            dialog.set_detail("Please wait for the image to load before saving.")
            dialog.show(self.get_root())
            return

        # Create file chooser dialog
        dialog = Gtk.FileDialog()
        dialog.set_title("Save Image as PNG")
        dialog.set_accept_label("Save")

        # Create PNG filter
        png_filter = Gtk.FileFilter()
        png_filter.set_name("PNG Images (*.png)")
        png_filter.add_pattern("*.png")
        dialog.set_default_filter(png_filter)

        # Set default filename
        dialog.set_initial_name("generated_image.png")

        # Show dialog and handle response
        def on_save_response(dialog, result):
            try:
                file = dialog.save_finish(result)
                if file:
                    file_path = file.get_path()

                    # Ensure .png extension
                    if not file_path.lower().endswith('.png'):
                        file_path += '.png'

                    # Save the image as PNG
                    try:
                        self.save_image(file_path, "png")
                        print(f"Image successfully saved to: {file_path}")

                    except Exception as e:
                        # Show error dialog
                        error_dialog = Gtk.AlertDialog()
                        error_dialog.set_message("Failed to save image")
                        error_dialog.set_detail(f"Error: {str(e)}")
                        error_dialog.show(self.get_root())

            except Exception as e:
                # Handle dialog cancellation silently
                if "dismissed" not in str(e).lower():
                    print(f"Save dialog error: {e}")

        dialog.save(self.get_root(), None, on_save_response)

    def set_prompt(self, prompt):
        """Set the original prompt for this image"""
        self.prompt = prompt

    def on_copy_clicked(self, button):
        """Handle copy button click - copy prompt to clipboard"""
        if not self.prompt:
            # Show info dialog if no prompt available
            dialog = Gtk.AlertDialog()
            dialog.set_message("No prompt to copy")
            dialog.set_detail("The original prompt is not available.")
            dialog.show(self.get_root())
            return

        # Get the clipboard
        clipboard = Gdk.Display.get_default().get_clipboard()

        # Copy the prompt to clipboard
        clipboard.set(self.prompt)

        # Show success feedback
        print(f"Copied to clipboard: {self.prompt}")

        # Optional: Brief visual feedback by changing button state
        original_classes = self.copy_button.get_css_classes()
        self.copy_button.set_css_classes(["success", "flat", "suggested-action"])

        # Reset button appearance after a short delay
        def reset_button():
            self.copy_button.set_css_classes(original_classes)
            return False  # Don't repeat

        from gi.repository import GLib
        GLib.timeout_add(1000, reset_button)  # Reset after 1 second
