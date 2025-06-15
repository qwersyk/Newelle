from gi.repository import Gtk, Pango

class WebsiteButton(Gtk.Button):
    """
    A custom GTK button widget that displays file information (icon, name, path).
    """

    def __init__(self, url, **kwargs):
        """
        Initializes the FileButton.

        Args:
            path (str): The path to the file/directory.
            main_path (str, optional): The main path to resolve relative paths. Defaults to ".".
            **kwargs:  Keyword arguments passed to the Gtk.Button constructor.
        """
        super().__init__(**kwargs)
        self.url = url
        self.set_css_classes(["flat"])
        self.set_margin_top(5)
        self.set_margin_start(5)
        self.set_margin_bottom(5)
        self.set_margin_end(5)

        # Normalize and expand the path
        self.set_name(self.url)  # Set the path as the button's name
        self._build_ui()

    def _build_ui(self):
        """
        Constructs the user interface elements within the button.
        """
        box = Gtk.Box()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        icon_name = "internet-symbolic"

        icon = Gtk.Image(icon_name=icon_name)
        icon.set_css_classes(["large"])
        self.icon = icon 

        box.append(icon)
        box.append(vbox)
        vbox.set_size_request(250, -1)
        vbox.set_margin_start(5)
        self.title =Gtk.Label(
                label=self.url,
                css_classes=["title-3"],
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
        vbox.append(
            self.title
        )
        self.description = Gtk.Label( 
                label="",
                css_classes=["body"],
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
        )

        vbox.append(
            self.description
        )

        self.url_text = Gtk.Label(
            label=f"<a href='{self.url}'>{self.url}</a>",
            css_classes=["body"],
            halign=Gtk.Align.START,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            use_markup=True
        )
        vbox.append(self.url_text)
        self.set_child(box)
