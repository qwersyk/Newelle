from gi.repository import Gtk, Adw

from .settings import Settings
from .widgets import CopyBox
from ..utility.system import can_escape_sandbox, is_flatpak
import subprocess


class PresentationWindow(Adw.Window):
    def __init__(self, title, settings, parent):
        super().__init__(title=title, deletable=True, modal=True)
        self.app = parent.get_application()
        self.controller = parent.controller
        self.settings = settings

        self.set_default_size(640, 700)
        self.set_transient_for(parent)
        self.set_modal(True)

        mainbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        headerbar = Gtk.HeaderBar(css_classes=["flat"])
        indicator = Adw.CarouselIndicatorDots()
        headerbar.set_title_widget(indicator)
        mainbox.append(headerbar)
        
        # Navigation buttons
        self.previous = Gtk.Button(opacity=0, icon_name="left-large-symbolic", valign=Gtk.Align.CENTER, margin_start=12, margin_end=12, css_classes=["circular"])
        self.next = Gtk.Button(opacity=1, icon_name="right-large-symbolic", valign=Gtk.Align.CENTER,margin_start=12, margin_end=12, css_classes=["circular", "suggested-action"])
        # Carousel
        contentbox = Gtk.Box()
        carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_long_swipes=True, allow_scroll_wheel=True, interactive=True, allow_mouse_drag=False)
        indicator.set_carousel(carousel)
        # Content
        contentbox.append(self.previous)
        contentbox.append(carousel)
        contentbox.append(self.next)
        mainbox.append(contentbox)

        self.carousel = carousel
        # Signals
        self.carousel.connect("page-changed", self.page_changes)
        self.previous.connect("clicked", self.previous_page)
        self.next.connect("clicked", self.next_page)

        self.build_pages()
        self.set_size_request(640, 700)
        self.set_content(mainbox)
        self.connect("close-request", self.close_window)

    def close_window(self,_=None):
        self.settings.set_boolean("welcome-screen-shown", True)
        self.app.win.update_settings()
        self.destroy()
    def page_changes(self, carousel, page):
        """Called when a page of carousel is changed. Changes the opacity of the next and previous buttons"""
        if page > 0:
            self.previous.set_opacity(1)
        else:
            self.previous.set_opacity(0)
        if page >= self.carousel.get_n_pages()-1:
            self.next.set_opacity(0)
        else:
           self.next.set_opacity(1)
    
    def next_page(self, button):
        self.carousel.get_position()
        if self.carousel.get_position() < self.carousel.get_n_pages()-1:
            self.carousel.scroll_to(self.carousel.get_nth_page(int(self.carousel.get_position()+1)), True)

    def previous_page(self, button):
        self.carousel.get_position()
        if self.carousel.get_position() > 0:
            self.carousel.scroll_to(self.carousel.get_nth_page(int(self.carousel.get_position()-1)), True)
    
    def build_pages(self):
        """Builds the pages of the presentation
        Every page must be in this format:
        - title: Title of the page
        - description: Description of the page
        - widget: Widget to be displayed in the page - Not working if you use picture
        - picture: Path of the picture to be displayed in the page - Not working if you use widget
        - actions: List of buttons to be displayed in the page

        The actions list must be in this format:
        - label: Label of the button
        - callback: Callback to be called when the button is pressed
        - classes: List of classes to be applied to the button
        """
        settings = Settings(self.app, self.controller, headless=True)
        pages = [
            {
                "title": _("Welcome to Newelle"),
                "description": _("Your ultimate virtual assistant."),
                "picture": "/io/github/qwersyk/Newelle/images/illustration.svg",
                "actions": [
                    {
                        "label": _("Github Page"),
                        "classes": [],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/qwersyk/Newelle"]), 
                    }
                ]
            },
            {
                "title": _("Choose your favourite AI Language Model"),
                "description": _("Newelle can be used with mutiple models and providers!\\n<b>Note: It is strongly suggested to read the Guide to LLM page</b>"),
                "widget": self.__steal_from_settings(settings.LLM),
                "actions": [
                    {
                        "label": _("Guide to LLM"),
                        "classes": ["suggested-action"],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/qwersyk/Newelle/wiki/User-guide-to-the-available-LLMs"]),
                    }
                ] 
            },
            {
                "title": _("Chat with your documents"),
                "description": _("Newelle can retrieve relevant information from documents you send in the chat or from your own files! Information relevant to your query will be sent to the LLM."),
                "widget": self.__steal_from_settings(settings.RAG),
                "actions": [
                ] 
            },
        ]
        
        # Only show the virtualization page if running in Flatpak
        if is_flatpak():
            pages.append({
                "title": _("Command virtualization"),
                "description": _("Newelle can be used to run commands on your system, but pay attention at what you run! <b>The LLM is not under our control, so it might generate malicious code!</b>\\nBy default, your commands will be <b>virtualized in Flatpak environment</b>, but pay attention!"),
                "widget": self.__steal_from_settings(settings.neural_network),
                "actions": [
                ] 
            })
        
        pages.append({
                "title": _("Extensions"),
                "description": _("You can extend Newelle's functionalities using extensions!"),
                "picture": "/io/github/qwersyk/Newelle/images/extension.svg",
                "actions": [
                    {
                        "label": _("Download extensions"),
                        "classes": ["suggested-action"],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/topics/newelle-extension"]),
                    }
                ]
            })
        # Show the warning only if there are not enough permissions
        if not can_escape_sandbox():
            pages.append({
                "title": _("Permission Error"),
                "description": _("Newelle does not have enough permissions to run commands on your system."),
                "picture": "/io/github/qwersyk/Newelle/images/error.svg",
                "actions": [
                    {
                        "label": "Learn more",
                        "classes": ["suggested-action"],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/qwersyk/Newelle?tab=readme-ov-file#permission"]),
                    }
                ]
            })
        pages.append({
                "title": _("Begin using the app"),
                "description": None,
                "widget":self.__create_icon("emblem-default-symbolic"),
                "actions": [
                    {
                        "label": _("Start chatting"),
                        "classes": ["suggested-action"],
                        "callback": self.close_window,
                    }
                ]
            })
        # Build the pages
        for page in pages:
            if "picture" in page:
                p = self.create_image_page(page["title"], page["description"], page["picture"], page["actions"])
            elif "widget" in page:
                p = self.create_page(page["title"], page["description"], page["widget"], page["actions"])
            else:
                continue
            self.carousel.append(p)

    def __steal_from_settings(self, widget: Gtk.Widget):
        """Steals a widget from the settings page. It unparsents the given widget and wraps it in a scroll Window

        Args:
            widget: widget stolen from settings 

        Returns: scrollwindow            
        """
        scroll = Gtk.ScrolledWindow(propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        widget.unparent()
        widget.set_margin_bottom(3)
        widget.set_margin_end(3)
        widget.set_margin_start(3)
        widget.set_margin_top(3)
        scroll.set_child(widget)
        return scroll

    def __create_icon(self, icon_name):
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(200)
        return img

    def __create_copybox(self): # I feel like it's a little out of place from the look, but maybe I'm wrong.
        """Create a copybox with necessary properties

        Returns: copybox 
            
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, hexpand=False)
        copy = CopyBox("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle", "bash")
        copy.set_hexpand(False)
        copy.set_vexpand(True)
        img = Gtk.Image.new_from_icon_name("warning-outline-symbolic")
        img.add_css_class("error")
        img.set_vexpand(True)
        img.set_pixel_size(200)
        box.append(img)
        box.append(copy)
        return box
    
    def create_page(self, title: str, description: str, widget: Gtk.Widget, actions: list):
        """Create a page from the given properties

        Args:
            title: title of the page 
            description: description of the page
            widget: widget to be displayed in the page
            actions: List of buttons to be displayed in the page

        Returns: the page
        """
        page = Gtk.Box(hexpand=True, vexpand=True, valign=Gtk.Align.CENTER, orientation=Gtk.Orientation.VERTICAL, spacing=20)

        page.append(widget)

        # Title
        title_label = Gtk.Label(css_classes=["title-1"])
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_text(title)
        page.append(title_label)

        # Description
        if description:
            description_label = Gtk.Label(single_line_mode=False,max_width_chars=50,wrap=True, css_classes=["body-1"])
            description_label.set_halign(Gtk.Align.CENTER)
            description_label.set_text(description)
            description_label.set_use_markup(True)
            page.append(description_label) # Actions 
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.CENTER, hexpand=False, baseline_position=Gtk.BaselinePosition.CENTER, margin_bottom=20)
        for action in actions:
            button = Gtk.Button(css_classes=action["classes"])
            button.set_label(action["label"])
            button.connect("clicked", action["callback"])
            buttons.append(button)
        page.append(buttons)

        return page

    def create_image_page(self, title:str, description: str, picture:str, actions: list):
        # Picture
        pic = Gtk.Picture()
        pic.set_resource(picture)
        return self.create_page(title, description, pic, actions)

