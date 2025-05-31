from gi.repository import Gtk, Adw, GLib, Gio, Pango 
from .widgets import File
import os 
import posixpath
import subprocess


class ExplorerPanel(Gtk.Box):
    def __init__(self, controller, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.controller = controller
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class("view")
        self.add_css_class("background")
        self.set_size_request(420, -1)

        # Extra vars 
        self.check_streams = {"folder": False, "chat": False}
        self.main_path = "~" 
        # Headerbar
        self.explorer_panel_header = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False, show_end_title_buttons=False) 
        self.append(self.explorer_panel_header)

        
        # Folders
        self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Notification block 
        self.notification_block = Adw.ToastOverlay()
        self.append(self.notification_block)
        self.notification_block.set_child(self.folder_blocks_panel)
        self.build_explorer_panel_buttons()

    def build_explorer_panel_buttons(self):
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        # Back explorer panel button
        button_folder_back = Gtk.Button(css_classes=["flat"], icon_name="go-previous-symbolic")
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-previous-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box.append(icon)
        button_folder_back.set_child(box)
        button_folder_back.connect("clicked", self.go_back_in_explorer_panel)

        # Forward explorer panel button
        button_folder_forward = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-next-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_folder_forward.set_child(box)
        button_folder_forward.connect("clicked", self.go_forward_in_explorer_panel)

        # Home explorer panel button
        button_home = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-home-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_home.set_child(box)
        button_home.connect("clicked", self.go_home_in_explorer_panel)

        # Reload explorer panel button
        button_reload = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_reload.set_child(box)
        button_reload.connect("clicked", self.update_folder)

        box = Gtk.Box(spacing=6)
        box.append(button_folder_back)
        box.append(button_folder_forward)
        box.append(button_home)
        self.explorer_panel_header.pack_start(box)
        box = Gtk.Box(spacing=6)
        box.append(button_reload)

        # Box containing explorer panel specific buttons
        self.explorer_panel_headerbox = box
        self.explorer_panel_header.pack_end(box)


    def go_back_in_explorer_panel(self, *a):
        self.main_path += "/.."
        GLib.idle_add(self.update_folder)

    def go_home_in_explorer_panel(self, *a):
        self.main_path = "~"
        GLib.idle_add(self.update_folder)

    def go_forward_in_explorer_panel(self, *a):
        if self.main_path[len(self.main_path) - 3 : len(self.main_path)] == "/..":
            self.main_path = self.main_path[0 : len(self.main_path) - 3]
            GLib.idle_add(self.update_folder)

    def update_folder(self, *a):
        if not self.check_streams["folder"]:
            self.check_streams["folder"] = True
            if os.path.exists(os.path.expanduser(self.main_path)):
                self.explorer_panel_header.set_title_widget(
                    Gtk.Label(
                        label=os.path.normpath(self.main_path)
                        + (3 - len(os.path.normpath(self.main_path))) * " ",
                        css_classes=["title"],
                        ellipsize=Pango.EllipsizeMode.MIDDLE,
                        max_width_chars=15,
                        halign=Gtk.Align.CENTER,
                        hexpand=True,
                    )
                )
                if (
                    len(os.listdir(os.path.expanduser(self.main_path))) == 0
                    or (
                        sum(
                            1
                            for filename in os.listdir(
                                os.path.expanduser(self.main_path)
                            )
                            if not filename.startswith(".")
                        )
                        == 0
                        and not self.controller.newelle_settings.hidden_files
                    )
                    and os.path.normpath(self.main_path) != "~"
                ):
                    self.remove(self.folder_blocks_panel)
                    self.folder_blocks_panel = Gtk.Box(
                        orientation=Gtk.Orientation.VERTICAL, spacing=20, opacity=0.25
                    )
                    self.append(self.folder_blocks_panel)
                    icon = Gtk.Image.new_from_gicon(
                        Gio.ThemedIcon(name="folder-symbolic")
                    )
                    icon.set_css_classes(["empty-folder"])
                    icon.set_valign(Gtk.Align.END)
                    icon.set_vexpand(True)
                    self.folder_blocks_panel.append(icon)
                    self.folder_blocks_panel.append(
                        Gtk.Label(
                            label=_("Folder is Empty"),
                            wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                            vexpand=True,
                            valign=Gtk.Align.START,
                            css_classes=["empty-folder", "heading"],
                        )
                    )
                else:
                    self.remove(self.folder_blocks_panel)
                    self.folder_blocks_panel = Gtk.Box(
                        orientation=Gtk.Orientation.VERTICAL
                    )
                    self.append(self.folder_blocks_panel)

                    flow_box = Gtk.FlowBox(vexpand=True)
                    flow_box.set_valign(Gtk.Align.START)
                    if os.path.normpath(self.main_path) == "~" or os.path.normpath(self.main_path) == os.path.expanduser("~"):
                        os.chdir(os.path.expanduser("~"))
                        fname = "/".join(self.controller.newelle_dir.split("/")[3:])
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(fname)
                        button.connect("clicked", self.open_folder)
                        icon = File(
                            self.main_path, fname 
                        )
                        icon.set_css_classes(["large"])
                        icon.set_valign(Gtk.Align.END)
                        icon.set_vexpand(True)
                        file_label = Gtk.Label(
                            label="Newelle",
                            wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                            vexpand=True,
                            max_width_chars=11,
                            valign=Gtk.Align.START,
                            ellipsize=Pango.EllipsizeMode.MIDDLE,
                        )
                        file_box = Gtk.Box(
                            orientation=Gtk.Orientation.VERTICAL, spacing=6
                        )
                        file_box.append(icon)
                        file_box.set_size_request(110, 110)
                        file_box.append(file_label)
                        button.set_child(file_box)

                        flow_box.append(button)
                    for file_info in os.listdir(os.path.expanduser(self.main_path)):
                        if (
                            file_info[0] == "."
                            and not self.controller.newelle_settings.hidden_files
                        ):
                            continue
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(file_info)
                        button.connect("clicked", self.open_folder)

                        icon = File(self.main_path, file_info)
                        icon.set_css_classes(["large"])
                        icon.set_valign(Gtk.Align.END)
                        icon.set_vexpand(True)
                        file_label = Gtk.Label(
                            label=file_info + " " * (5 - len(file_info)),
                            wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                            vexpand=True,
                            max_width_chars=11,
                            valign=Gtk.Align.START,
                            ellipsize=Pango.EllipsizeMode.MIDDLE,
                        )
                        file_box = Gtk.Box(
                            orientation=Gtk.Orientation.VERTICAL, spacing=6
                        )
                        file_box.append(icon)
                        file_box.set_size_request(110, 110)
                        file_box.append(file_label)
                        button.set_child(file_box)

                        flow_box.append(button)
                    scrolled_window = Gtk.ScrolledWindow()
                    scrolled_window.set_policy(
                        Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
                    )
                    scrolled_window.set_child(flow_box)
                    self.folder_blocks_panel.append(scrolled_window)
            else:
                self.main_path = "~"
                self.update_folder()
            self.check_streams["folder"] = False

    def get_target_directory(self, working_directory, directory):
        try:
            directory = directory.strip()
            if directory.startswith("'") and directory.endswith("'"):
                directory = directory[1:-1]
            elif directory.startswith('"') and directory.endswith('"'):
                directory = directory[1:-1]

            if directory.startswith("~"):
                directory = os.path.expanduser("~") + directory[1:]

            target_directory = posixpath.join(working_directory, directory)
            return (True, os.path.normpath(target_directory))
        except (IndexError, OSError) as e:
            return (False, working_directory)

    def open_folder(self, button, *a):
        if os.path.exists(
            os.path.join(os.path.expanduser(self.main_path), button.get_name())
        ):
            if os.path.isdir(
                os.path.join(os.path.expanduser(self.main_path), button.get_name())
            ):
                self.main_path += "/" + button.get_name()
                os.chdir(os.path.expanduser(self.main_path))
                GLib.idle_add(self.update_folder)
            else:
                subprocess.run(
                    [
                        "xdg-open",
                        os.path.expanduser(self.main_path + "/" + button.get_name()),
                    ]
                )
        else:
            self.notification_block.add_toast(
                Adw.Toast(title=_("File not found"), timeout=2)
            )

