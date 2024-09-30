import gi, os, subprocess

from gi.repository import Gtk, Pango, Gio, Gdk, GtkSource, GObject, Adw, GLib
import threading

def apply_css_to_widget(widget, css_string):
    provider = Gtk.CssProvider()
    context = widget.get_style_context()

    # Load the CSS from the string
    provider.load_from_data(css_string.encode())

    # Add the provider to the widget's style context
    context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)


class File(Gtk.Image):
    def __init__(self, path, file_name):
        if os.path.isdir(os.path.join(os.path.expanduser(path), file_name)):
            if file_name == "Desktop":
                name = "user-desktop"
            elif file_name == "Documents":
                name = "folder-documents"
            elif file_name == "Downloads":
                name = "folder-download"
            elif file_name == "Music":
                name = "folder-music"
            elif file_name == "Pictures":
                name = "folder-pictures"
            elif file_name == "Public":
                name = "folder-publicshare"
            elif file_name == "Templates":
                name = "folder-templates"
            elif file_name == "Videos":
                name = "folder-videos"
            elif file_name == ".var/app/io.github.qwersyk.Newelle/Newelle":
                name = "user-bookmarks"
            else:
                name = "folder"
        else:
            if file_name[len(file_name) - 4:len(file_name)] in [".png", ".jpg"]:
                name = "image-x-generic"
            else:
                name = "text-x-generic"
        super().__init__(icon_name=name)

        self.path = path
        self.file_name = file_name
        self.drag_source = Gtk.DragSource.new()
        self.drag_source.set_actions(Gdk.DragAction.COPY)
        self.drag_source.connect("prepare", self.move)
        self.add_controller(self.drag_source)

    def move(self, drag_source, x, y):
        snapshot = Gtk.Snapshot.new()
        self.do_snapshot(self, snapshot)
        paintable = snapshot.to_paintable()
        drag_source.set_icon(paintable, int(x), int(y))

        data = os.path.normpath(os.path.expanduser(f"{self.path}/{self.file_name}"))
        return Gdk.ContentProvider.new_for_value(data)


class MultilineEntry(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.placeholding = True
        self.placeholder = ""
        self.enter_func = None
        self.on_change_func = None
        # Handle enter key
        # Call handle_enter_key only when shift is not pressed
        # shift + enter = new line
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", lambda controller, keyval, keycode, state:
            self.handle_enter_key() if keyval == Gdk.KEY_Return and not (state & Gdk.ModifierType.SHIFT_MASK) else None
        )

        # Scroll
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_max_content_height(150)
        scroll.set_propagate_natural_height(True)
        scroll.set_margin_start(10)
        scroll.set_margin_end(10)
        self.append(scroll)

        # TextView
        self.input_panel = Gtk.TextView()
        self.input_panel.set_wrap_mode(Gtk.WrapMode.WORD)
        self.input_panel.set_hexpand(True)
        self.input_panel.set_vexpand(False)
        self.input_panel.set_top_margin(5)
        self.input_panel.add_controller(key_controller)
        # Event management
        focus_controller = Gtk.EventControllerFocus.new()
        self.input_panel.add_controller(focus_controller)

        # Connect the enter and leave signals
        focus_controller.connect("enter", self.on_focus_in, None)
        focus_controller.connect("leave", self.on_focus_out, None)

        # Add style to look like a GTK Entry
        self.add_css_class("card")
        self.add_css_class("frame")
        self.input_panel.add_css_class("multilineentry")
        apply_css_to_widget(self.input_panel, ".multilineentry { background-color: rgba(0,0,0,0); font-size: 15px;}")

        # Add TextView to the ScrolledWindow
        scroll.set_child(self.input_panel)

    def set_placeholder(self, text):
        self.placeholder = text
        if self.placeholding:
            self.set_text(self.placeholder, False)

    def set_on_enter(self, function):
        """Add a function that is called when ENTER (without SHIFT) is pressed"""
        self.enter_func = function

    def handle_enter_key(self):
        if self.enter_func is not None:
            GLib.idle_add(self.set_text, self.get_text().rstrip("\n"))
            GLib.idle_add(self.enter_func, self)

    def get_input_panel(self):
        return self.input_panel

    def set_text(self, text, remove_placeholder=True):
        if remove_placeholder:
            self.placeholding = False
        self.input_panel.get_buffer().set_text(text)

    def get_text(self):
        return self.input_panel.get_buffer().get_text(self.input_panel.get_buffer().get_start_iter(), self.input_panel.get_buffer().get_end_iter(), False)

    def on_focus_in(self, widget, data):
        if self.placeholding:
            self.set_text("", False)
            self.placeholding = False

    def on_focus_out(self, widget, data):
        if self.get_text() == "":
            self.placeholding = True
            self.set_text(self.placeholder, False)

    def set_on_change(self, function):
        self.on_change_func = function
        self.input_panel.get_buffer().connect("changed", self.on_change)

    def on_change(self, buffer):
        if self.on_change_func is not None:
            self.on_change_func(self)

class CopyBox(Gtk.Box):
    def __init__(self, txt, lang, parent = None,id_message=-1):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10, css_classes=["osd", "toolbar", "code"])
        self.txt = txt
        self.id_message = id_message
        box = Gtk.Box(halign=Gtk.Align.END)

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.copy_button.set_child(icon)
        self.copy_button.connect("clicked", self.copy_button_clicked)

        self.sourceview = GtkSource.View()

        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(txt, -1)

        manager = GtkSource.LanguageManager.new()
        language = manager.get_language(lang)
        self.buffer.set_language(language)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme('classic')
        self.buffer.set_style_scheme(style_scheme)

        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_vexpand(True)
        self.sourceview.set_show_line_numbers(True)
        self.sourceview.set_background_pattern(GtkSource.BackgroundPatternType.GRID)
        self.sourceview.set_editable(False)
        style = "success"
        if lang in ["python", "cpp", "php", "objc", "go", "typescript", "lua", "perl", "r", "dart", "sql"]:
            style = "accent"
        if lang in ["java", "javascript", "kotlin", "rust"]:
            style = "warning"
        if lang in ["ruby", "swift", "scala"]:
            style = "error"
        if lang in ["console"]:
            style = ""
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        main.set_homogeneous(True)
        label = Gtk.Label(label=lang, halign=Gtk.Align.START, margin_start=10, css_classes=[style, "heading"],wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        main.append(label)
        self.append(main)
        self.append(self.sourceview)
        main.append(box)
        if lang == "python":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_python)
            self.parent = parent

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            self.text_expander.set_expanded(False)
            self.text_expander.set_visible(False)
            box.append(self.run_button)
            self.append(self.text_expander)

        elif lang == "console":
            # Run button
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_console)
            # Run in external terminal button 
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="gnome-terminal-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.terminal_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.terminal_button.set_child(icon)
            self.terminal_button.connect("clicked", self.run_console_terminal)
            
            self.parent = parent

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            console = "None"
            if id_message<len(self.parent.chat) and self.parent.chat[id_message]["User"]=="Console":
                console = self.parent.chat[id_message]["Message"]
            self.text_expander.set_child(
                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=console, selectable=True))
            self.text_expander.set_expanded(False)
            box.append(self.run_button)
            box.append(self.terminal_button)
            self.append(self.text_expander)

        box.append(self.copy_button)

    def copy_button_clicked(self, widget):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.txt))

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button.set_child(icon)

    def run_console(self, widget,multithreading=False):
        if multithreading:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="emblem-ok-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(False)
            code = self.parent.execute_terminal_command(self.txt.split("\n"))
            if self.id_message<len(self.parent.chat) and self.parent.chat[self.id_message]["User"]=="Console":
                self.parent.chat[self.id_message]["Message"] = code[1]
            else:
                self.parent.chat.append({"User": "Console", "Message": " " + code[1]})
            self.text_expander.set_child(
                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=code[1], selectable=True))
            if self.parent.status and len(self.parent.chat)-1==self.id_message and self.id_message<len(self.parent.chat) and self.parent.chat[self.id_message]["User"]=="Console":
                self.parent.status = False
                self.parent.update_button_text()
                self.parent.scrolled_chat()
                self.parent.send_message()
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(True)
        else:
            threading.Thread(target=self.run_console, args=[widget, True]).start()
    
    def run_console_terminal(self, widget,multithreading=False):
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="emblem-ok-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        command = self.txt + "; exec bash"
        cmd = self.parent.external_terminal.split()
        arguments = [s.replace("{0}", command) for s in cmd]
        subprocess.Popen(["flatpak-spawn", "--host"] + arguments)


    def run_python(self, widget):
        self.text_expander.set_visible(True)
        t = self.txt.replace("'", '"""')
        console_permissions = ""
        if not self.parent.virtualization:
            console_permissions = "flatpak-spawn --host "
        process = subprocess.Popen(f"""{console_permissions}python3 -c '{t}'""", stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=True)
        stdout, stderr = process.communicate()
        text = "Done"
        if process.returncode != 0:
            text = stderr.decode()

        else:
            if stdout.decode() != "":
                text = stdout.decode()
        self.text_expander.set_child(
            Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text, selectable=True))


class BarChartBox(Gtk.Box):
    def __init__(self, data_dict,percentages):
        Gtk.Box.__init__(self,orientation=Gtk.Orientation.VERTICAL, margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10, css_classes=["card","chart"])

        self.data_dict = data_dict
        max_value = max(self.data_dict.values())
        if percentages and max_value<=100:
            max_value = 100
        for label, value in self.data_dict.items():
            bar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10)

            bar = Gtk.ProgressBar()
            bar.set_fraction(value / max_value)

            label = Gtk.Label(label=label,wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.set_halign(Gtk.Align.CENTER)
            bar_box.append(label)
            bar_box.append(bar)
            self.append(bar_box)

class ComboRowHelper(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        combo: Adw.ComboRow,
        options: tuple[tuple[str, str]],
        selected_value: str,
    ):
        super().__init__()
        self.combo = combo
        self.__combo = combo
        self.__factory = Gtk.SignalListItemFactory()
        self.__factory.connect("setup", self.__on_setup_listitem)
        self.__factory.connect("bind", self.__on_bind_listitem)
        combo.set_factory(self.__factory)

        self.__store = Gio.ListStore(item_type=self.ItemWrapper)
        i = 0
        selected_index = 0
        for option in options:
            if option[1] == selected_value:
                selected_index = i
            i += 1
            self.__store.append(self.ItemWrapper(option[0], option[1]))
        combo.set_model(self.__store)

        combo.set_selected(selected_index)
        combo.connect("notify::selected-item", self.__on_selected)
    class ItemWrapper(GObject.Object):
        def __init__(self, name: str, value: str):
            super().__init__()
            self.name = name
            self.value = value

    def __on_selected(self, combo: Adw.ComboRow, selected_item: GObject.ParamSpec) -> None:
        value = self.__combo.get_selected_item().value
        self.emit("changed", value)

    def __on_setup_listitem(self, factory: Gtk.ListItemFactory, list_item: Gtk.ListItem) -> None:
        label = Gtk.Label()
        list_item.set_child(label)
        list_item.row_w = label

    def __on_bind_listitem(self, factory: Gtk.ListItemFactory, list_item: Gtk.ListItem) -> None:
        label = list_item.get_child()
        label.set_text(list_item.get_item().name)

