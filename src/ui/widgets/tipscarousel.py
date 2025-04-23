from gi.repository import Gtk, Adw, Pango
import random 

class TipsCarousel(Gtk.Box):
    """
    A standalone widget displaying random tips in an Adw.Carousel.
    """
    def __init__(self, tips_data: list, tip_limit: int = 0, **kwargs):
        """
        Initializes the TipsCarousel widget.

        Args:
            tips_data: A list of dictionaries, where each dict has 'title' and 'subtitle'.
            tip_limit: The maximum number of tips to display. If None, displays all tips.
            **kwargs: Additional arguments for Gtk.Box.
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, **kwargs)

        if not isinstance(tips_data, list):
             raise TypeError("tips_data must be a list")
        if tip_limit is not None and not isinstance(tip_limit, int):
             raise TypeError("tip_limit must be an integer or None")
        if tip_limit is not None and tip_limit < 0:
             tip_limit = 0
        

        self._tips_data = tips_data # Store the full list of tips
        self._tip_limit = tip_limit # Store the display limit

        # Build the internal widgets
        self._build_ui()

        # Populate the carousel initially with shuffled tips
        self.shuffle_tips()

    def _build_ui(self):
        """
        Builds the layout of the widget (label, carousel, indicators).
        """
        # Title for the tips section
        tips_label = Gtk.Label(label=_("Newelle Tips"))
        tips_label.add_css_class("heading") # Use a suitable style class for a section title
        tips_label.set_halign(Gtk.Align.START) # Center the title
        tips_label.set_margin_bottom(5) # Reduce margin here, box spacing handles the rest

        self.append(tips_label)

        # Create the Adw.Carousel
        self._carousel = Adw.Carousel()
        self._carousel.set_hexpand(True) # Allow carousel to expand horizontally
        self._carousel.set_vexpand(False) # Prevent carousel from taking excessive vertical space
        self._carousel.set_halign(Gtk.Align.FILL) # Fill available horizontal space allocated to it
        self._carousel.set_spacing(12) # Add spacing between carousel pages (cards)

        self.append(self._carousel)

        # Add indicator dots for the carousel
        self._indicator_dots = Adw.CarouselIndicatorDots()
        self._indicator_dots.set_carousel(self._carousel) # Link indicators to the carousel
        self._indicator_dots.set_halign(Gtk.Align.CENTER) # Center the dots horizontally
        self._indicator_dots.set_margin_top(5) # Space between carousel and dots

        self.append(self._indicator_dots)

    def _create_tip_card(self, tip_data: dict) -> Gtk.Widget:
        """
        Creates a single clickable tip card widget.

        Args:
            tip_data: Dictionary containing 'title' and 'subtitle'.

        Returns:
            A Gtk.Widget (a Button) representing the tip card.
        """
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Title Label (bold)
        title_label = Gtk.Label(wrap=True, halign=Gtk.Align.START, css_classes=["heading"])
        title_label.set_markup(tip_data['title'])
        content_box.append(title_label)

        # Subtitle Label
        subtitle_label = Gtk.Label(wrap=True, halign=Gtk.Align.START, css_classes=["body"], overflow=Gtk.Overflow.VISIBLE, wrap_mode=Pango.WrapMode.WORD, vexpand=True, width_request=250)
        subtitle_label.set_text(tip_data['subtitle'])
        content_box.append(subtitle_label)

        tip_button = Gtk.Button(vexpand=True, hexpand=False)
        tip_button.set_child(content_box)
        tip_button.add_css_class("richtext") 

        tip_button.set_vexpand(False) 
        tip_button.set_valign(Gtk.Align.FILL) # Align cards to the top within the carousel

        on_click = tip_data.get("on_click", None)
        if on_click is not None:
            tip_button.connect("clicked", lambda x : on_click(), tip_data)
        else:
            tip_button.set_sensitive(False)
        return tip_button

    def shuffle_tips(self):
        """
        Shuffles the internal list of tips and repopulates the carousel
        with a random selection up to the specified limit.
        """
        num_available_tips = len(self._tips_data)

        if num_available_tips == 0:
            tips_to_display = []
        elif self._tip_limit is None or self._tip_limit >= num_available_tips:
            # Display all tips if limit is None or greater than available
            tips_to_display = random.sample(self._tips_data, num_available_tips)
        else:
            # Display a random sample up to the limit
            tips_to_display = random.sample(self._tips_data, self._tip_limit)

        # Clear existing children from the carousel
        while self._carousel.get_first_child():
            self._carousel.remove(self._carousel.get_first_child())

        # Add the new selection of tips to the carousel
        if tips_to_display:
            for tip in tips_to_display:
                card = self._create_tip_card(tip)
                self._carousel.append(card)

