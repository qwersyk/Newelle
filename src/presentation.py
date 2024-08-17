import gi
from gi.repository import Gtk, Adw, GdkPixbuf
from gi.repository.Gio import Subprocess

from .settings import Settings
from .gtkobj import CopyBox
from .extra import can_escape_sandbox
import subprocess
class PresentationWindow(Adw.Window):
    def __init__(self, title, settings, path, parent : Adw.ApplicationWindow):
        super().__init__(title=title, resizable=False, deletable=True, modal=True)
        self.app = parent.get_application()
        self.settings = settings
        self.path = path

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
        carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_long_swipes=True, allow_scroll_wheel=True, interactive=True)
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
        self.set_size_request(800, 600)
        self.set_content(mainbox)
        self.connect("close-request", self.close_window)

    def close_window(self,_):
        self.settings.set_boolean("welcome-screen-shown", True)
        self.destroy()
    def page_changes(self, carousel, page):
        """Called when a page of the carousel is changed. Changes the opacity of the next and previous buttons"""
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
        settings = Settings(self.app, headless=True)
        pages = [
            {
                "title": "Welcome to Newelle, Your ultimate virtual assistant",
                "description": "Newelle is an advanced chat bot that aims to revolutionize your virtual assistant experience.",
                "picture": "/io/github/qwersyk/Newelle/images/newelle.svg",
                "actions": [
                    {
                        "label": "Github Page",
                        "classes": [],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/qwersyk/Newelle"]), 
                    }
                ]
            },
            {
                "title": "Choose your favourite AI Language Model",
                "description": "Newelle can be used with mutiple models and providers! Choose your favourite in the settings.",
                "widget": self.__steal_from_settings(settings.LLM),
                "actions": [] 
            },
            {
                "title": "Extensions",
                "description": "You can extend Newelle's functionalities using extensions!",
                "picture": "/io/github/qwersyk/Newelle/images/extensions.png",
                "actions": [
                    {
                        "label": "Download extensions",
                        "classes": ["suggested-action"],
                        "callback": lambda x: subprocess.Popen(["xdg-open", "https://github.com/topics/newelle-extension"]), 
                    }
                ]
            }
        ]
        # Show the warning only if there are not enough permissions
        if not can_escape_sandbox():
            pages.append({
                "title": "Permission Error",
                "description": "Newelle does not have enough permissions to run commands on your system, please run the command above. This action is only necessary if you want Newelle to run commands in a non virtualized envirnoment",
                "widget": self.__create_copybox(),
                "actions": [
                    {
                        "label": "Understood",
                        "classes": ["primary"],
                        "callback": lambda x: self.destroy()
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
        """Steals a widget from the settings page. It unparents the given widget and wraps it in a scroll Window

        Args:
            widget: the widget stolen from the settings 

        Returns: the scrollwindow            
        """
        scroll = Gtk.ScrolledWindow(vexpand=True) 
        widget.unparent()
        scroll.set_child(widget)
        return scroll


    def __create_copybox(self):
        """Create a copybox with the necessary properties

        Returns: the copybox 
            
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, hexpand=False)
        copy = CopyBox("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle", "bash", parent=box)
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
            widget: the widget to be displayed in the page
            actions: List of buttons to be displayed in the page

        Returns: the page
        """
        page = Gtk.Box(hexpand=True, vexpand=True, orientation=Gtk.Orientation.VERTICAL, spacing=20)

        page.append(widget)

        # Title
        title_label = Gtk.Label(css_classes=["title-1"])
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_text(title)
        page.append(title_label)

        # Description
        description_label = Gtk.Label(single_line_mode=False,max_width_chars=50,wrap=True, css_classes=["body-1"])
        description_label.set_halign(Gtk.Align.CENTER)
        description_label.set_text(description)
        page.append(description_label)
        # Actions
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
        pic = Gtk.Image()
        pic.set_from_resource(picture)
        pic.set_vexpand(True)
        pic.set_hexpand(True)
        return self.create_page(title, description, pic, actions)

