import gi, os, subprocess

gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Pango, Gio, Gdk, GtkSource


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


class CopyBox(Gtk.Box):
    def __init__(self, txt, lang, parent = None):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10, css_classes=["osd", "toolbar", "code"])
        self.txt = txt
        box = Gtk.Box(halign=Gtk.Align.END)

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button = Gtk.Button(halign=Gtk.Align.END, css_classes=["flat"], margin_end=10)
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
        self.sourceview.set_size_request(340, -1)
        style = "success"
        if lang in ["python", "cpp", "php", "objc", "go", "typescript", "lua", "perl", "r", "dart", "sql"]:
            style = "accent"
        if lang in ["java", "javascript", "kotlin", "rust"]:
            style = "warning"
        if lang in ["ruby", "swift", "scala"]:
            style = "error"

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["card"])
        main.set_homogeneous(True)
        label = Gtk.Label(label=lang, halign=Gtk.Align.START, margin_start=10, css_classes=[style, "heading"],wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.set_size_request(200, -1)
        main.append(label)
        self.append(main)
        self.append(self.sourceview)
        main.append(box)
        if lang == "python":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, css_classes=["flat"], margin_end=10)
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_python)

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            self.text_expander.set_expanded(False)
            self.text_expander.set_visible(False)
            box.append(self.run_button)
            self.append(self.text_expander)

        elif lang == "console":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, css_classes=["flat"], margin_end=10)
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_console)
            self.parent = parent

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            self.text_expander.set_expanded(False)
            self.text_expander.set_visible(False)
            box.append(self.run_button)
            self.append(self.text_expander)

        box.append(self.copy_button)

    def copy_button_clicked(self, widget):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.txt))

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button.set_child(icon)
    def run_console(self, widget):
        self.text_expander.set_visible(True)
        code = self.parent.execute_terminal_command(self.txt.split("\n"))
        self.text_expander.set_child(
            Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=code[1], selectable=True))
        self.parent.chat.append({"User": "Console", "Message": " " + code[1] + "\end"})
    def run_python(self, widget):
        self.text_expander.set_visible(True)
        t = self.txt.replace("'", '"""')
        process = subprocess.Popen(f"""flatpak-spawn --host python3 -c '{t}'""", stdout=subprocess.PIPE,
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
