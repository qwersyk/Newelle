import threading
from dataclasses import dataclass
from typing import List
from gi.repository import Gtk, Adw, GLib, Gdk, Pango

@dataclass
class LibraryModel:
    id: str
    name: str
    description: str
    tags: List[str]
    is_installed: bool = False
    is_pinned: bool = False

class ModelLibraryWindow(Adw.Window):
    def __init__(self, handler, parent_window=None, **kwargs):
        super().__init__(**kwargs)
        self.handler = handler
        self.set_modal(True)
        if parent_window:
            self.set_transient_for(parent_window)
        
        # ...
        self.set_title("Ollama Model Library")
        
        self.load_css()
        
        # Main layout
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)
        
        # Header Bar
        header = Adw.HeaderBar()
        content.append(header)
        
        # Search Bar
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search models...")
        self.search_entry.connect("search-changed", self.on_search_changed)
        header.set_title_widget(self.search_entry)

        # Refresh Button
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.connect("clicked", self.refresh_library)
        refresh_btn.set_tooltip_text("Refresh Library")
        header.pack_start(refresh_btn)

        # Custom Model Button
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.connect("clicked", self.show_add_custom_model_dialog)
        add_btn.set_tooltip_text("Add Custom Model")
        header.pack_end(add_btn)
        
        # Scrolled Window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)
        content.append(self.scrolled)
        
        # FlowBox for cards
        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(3)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_row_spacing(20)
        self.flowbox.set_column_spacing(20)
        self.flowbox.set_margin_top(20)
        self.flowbox.set_margin_bottom(20)
        self.flowbox.set_margin_start(20)
        self.flowbox.set_margin_end(20)
        
        # Center the flowbox content
        viewport = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        viewport.set_halign(Gtk.Align.CENTER)
        viewport.append(self.flowbox)
        
        self.scrolled.set_child(viewport)
        
        # Lazy loading setup
        self.scrolled.get_vadjustment().connect("value-changed", self.on_scroll)
        self.loaded_count = 0
        self.batch_size = 50
        self.all_model_keys = [] # List of model keys (str)
        self.filtered_keys = [] # List of filtered keys
        
        self.cards = {} # key -> widget
        self.load_models()
        
        # Periodically check download status
        GLib.timeout_add(500, self.update_downloads)
        self.set_size_request(600, 400)

    def load_css(self):
        provider = Gtk.CssProvider()
        css = """
        .ollama-tag {
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            color: white;
            font-weight: bold;
            margin: 2px;
            transition: opacity 200ms;
        }
        .ollama-tag:hover {
            opacity: 0.8;
            cursor: pointer;
        }
        .tag-blue { background-color: #3584e4; color: white; }
        .tag-green { background-color: #2ec27e; color: white; }
        .tag-orange { background-color: #ff7800; color: white; }
        .tag-purple { background-color: #9141ac; color: white; }
        .tag-red { background-color: #e01b24; color: white; }
        .tag-yellow { background-color: #f6d32d; color: black; }
        .tag-gray { background-color: alpha(currentColor, 0.1); color: currentColor; }
        """
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def get_tag_class(self, tag):
        tag = str(tag).lower()
        if tag.endswith("b") or tag.endswith("gb") or tag.endswith("mb"):
            return "tag-blue"
        if len(tag) == 2: # Languages
            return "tag-green"
        if tag in ["code", "math", "coding"]:
            return "tag-purple"
        if tag in ["huge", "large", "small", "tiny"]:
            return "tag-orange"
        
        # Hash based for others
        colors = ["tag-blue", "tag-green", "tag-orange", "tag-purple", "tag-red", "tag-yellow"]
        return colors[hash(tag) % len(colors)]

    def on_scroll(self, adj):
        if adj.get_value() + adj.get_page_size() >= adj.get_upper() - 100:
            self.load_more_models()

    def load_models(self):
        # Clear existing
        child = self.flowbox.get_first_child()
        while child:
            self.flowbox.remove(child)
            child = self.flowbox.get_first_child()
        self.cards = {}
        
        # Re-fetch everything
        self.all_models = self.handler.fetch_models()
        self.loaded_count = 0
        self.apply_filter()

    def apply_filter(self):
        # Filter self.all_models based on search text
        text = self.search_entry.get_text().lower()
        if not text:
            self.filtered_models = list(self.all_models)
        else:
            self.filtered_models = []
            for model in self.all_models:
                match = False
                # Search in id, name, description
                if text in model.id.lower() or text in model.name.lower() or text in model.description.lower():
                    match = True
                
                if not match:
                    for t in model.tags:
                        if text in str(t).lower():
                            match = True
                            break

                if match:
                    self.filtered_models.append(model)
                    
        # Reset view
        child = self.flowbox.get_first_child()
        while child:
            self.flowbox.remove(child)
            child = self.flowbox.get_first_child()
        self.cards = {}
        self.loaded_count = 0
        
        self.load_more_models()

    def load_more_models(self):
        total = len(self.filtered_models)
        if self.loaded_count >= total:
            return
            
        end = min(self.loaded_count + self.batch_size, total)
        batch = self.filtered_models[self.loaded_count:end]
        
        for model in batch:
            card = self.create_card(model)
            self.flowbox.append(card)
            self.cards[model.id] = card
            
        self.loaded_count = end

    def create_card(self, model: LibraryModel):
        key = model.id
        title = model.name
        desc = model.description
        
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.model = model
        card.add_css_class("card")
        card.set_size_request(250, -1) # Fixed width
        
        # Inner content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_spacing(10)
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)
        card.append(content)
        
        # Header with Title and Pin icon
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_spacing(10)
        
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.add_css_class("heading")
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_hexpand(True)
        header_box.append(title_label)
        
        if model.is_pinned:
            pin_icon = Gtk.Image.new_from_icon_name("user-bookmarks-symbolic")
            pin_icon.add_css_class("dim-label")
            header_box.append(pin_icon)
            
        content.append(header_box)
        
        # Description
        if desc:
            desc_label = Gtk.Label(label=desc)
            desc_label.set_halign(Gtk.Align.START)
            desc_label.set_wrap(True)
            desc_label.set_lines(3)
            desc_label.set_ellipsize(Pango.EllipsizeMode.END)
            desc_label.add_css_class("body")
            desc_label.add_css_class("dim-label")
            content.append(desc_label)
            
        # Stats / Tags
        if model.tags:
            tags_box = Gtk.FlowBox()
            tags_box.set_selection_mode(Gtk.SelectionMode.NONE)
            tags_box.set_max_children_per_line(10) # Auto wrap
            tags_box.set_min_children_per_line(1)
            tags_box.set_row_spacing(5)
            tags_box.set_column_spacing(5)
            
            for tag in model.tags:
                lbl = Gtk.Label(label=str(tag))
                lbl.add_css_class("ollama-tag")
                lbl.add_css_class(self.get_tag_class(tag))
                
                # Make clickable
                click_gesture = Gtk.GestureClick()
                click_gesture.connect("pressed", self.on_tag_clicked, tag)
                lbl.add_controller(click_gesture)
                
                # Set pointer cursor
                cursor = Gdk.Cursor.new_from_name("pointer", None)
                lbl.set_cursor(cursor)
                
                tags_box.append(lbl)
                
            content.append(tags_box)

        # Bottom Action Area
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        action_box.set_spacing(10)
        action_box.set_margin_top(10)
        
        # Status / Progress
        status_stack = Gtk.Stack()
        
        # Download Button
        download_btn = Gtk.Button(icon_name="folder-download-symbolic", css_classes=["flat", "suggested-action"])
        download_btn.set_tooltip_text("Download Model")
        download_btn.connect("clicked", lambda b: self.install_model(key))
        
        # Delete Button
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.add_css_class("destructive-action")
        delete_btn.set_tooltip_text("Delete Model")
        delete_btn.connect("clicked", lambda b: self.install_model(key))
        
        # Progress Bar
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_valign(Gtk.Align.CENTER)
        progress_bar.set_hexpand(True)
        
        status_stack.add_named(download_btn, "download")
        status_stack.add_named(delete_btn, "delete")
        status_stack.add_named(progress_bar, "progress")
        
        action_box.append(status_stack)
        
        # Store references to update state
        card.status_stack = status_stack
        card.progress_bar = progress_bar
        card.model_key = key
        
        self.update_card_state(card)
        
        content.append(action_box)
        
        return card

    def on_tag_clicked(self, gesture, n_press, x, y, tag):
        self.search_entry.set_text(str(tag))

    def update_card_state(self, card):
        key = card.model_key
        is_installed = self.handler.model_installed(key)
        downloading = self.handler.get_percentage(key)
        
        if downloading > 0 and downloading < 1:
            card.status_stack.set_visible_child_name("progress")
            card.progress_bar.set_fraction(downloading)
        elif is_installed:
            card.status_stack.set_visible_child_name("delete")
        else:
            card.status_stack.set_visible_child_name("download")

    def install_model(self, key):
        threading.Thread(target=self.handler.install_model, args=(key,)).start()

    def update_downloads(self):
        for key, card in self.cards.items():
            self.update_card_state(card)
        return True # Keep running

    def on_search_changed(self, entry):
        self.apply_filter()

    def refresh_library(self, button):
        def _refresh():
             self.handler.get_models(manual=True)
             GLib.idle_add(self.load_models)

        threading.Thread(target=_refresh).start()

    def show_add_custom_model_dialog(self, button):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Add Custom Model",
            body="Enter the model name (e.g. llama3:8b) or HF path"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        
        entry = Gtk.Entry()
        entry.set_placeholder_text("Model Name")
        entry.set_margin_start(20)
        entry.set_margin_end(20)
        
        # Adw.MessageDialog custom child
        dialog.set_extra_child(entry)
        
        def on_response(d, response):
            if response == "add":
                text = entry.get_text()
                if text:
                    self.handler.set_setting("extra_model_name", text)
                    threading.Thread(target=self.handler.pull_model, args=(text,)).start()
            d.close()
            
        dialog.connect("response", on_response)
        dialog.present()
