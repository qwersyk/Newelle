from gi.repository import Gtk, Adw, GLib, Gio, Pango, Gdk, GObject
from .widgets import File
import os
import posixpath
import subprocess
import gettext

_ = gettext.gettext

class ExplorerPanel(Gtk.Box):
    __gsignals__ = {
        'new-tab-requested': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'path-changed': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'open-terminal-requested': (GObject.SignalFlags.RUN_FIRST, None, (str,))
    }

    def __init__(self, controller, starting_path="~", *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.tab = None
        self.controller = controller
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class("view")
        self.add_css_class("background")
        self.set_size_request(420, -1)

        # Extra vars
        self.check_streams = {"folder": False, "chat": False}
        self.main_path = starting_path
        self.flow_box = None
        self.get_current_path()
        self.context_menu_target = None  # Store the target file/folder for context menu

        # Create main content container
        self.main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Headerbar
        self.explorer_panel_header = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False, show_end_title_buttons=False)
        self.main_content.append(self.explorer_panel_header)

        # Search bar
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry()
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_entry.connect('search-changed', self.on_search_changed)
        self.main_content.append(self.search_bar)

        # Folders
        self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_content.append(self.folder_blocks_panel)

        # Notification block with main content as child
        self.notification_block = Adw.ToastOverlay()
        self.notification_block.set_child(self.main_content)
        self.append(self.notification_block)

        self.build_explorer_panel_buttons()
        self.update_folder()

    def go_to_path(self, path): 
        self.set_main_path(path)
        os.chdir(os.path.expanduser(self.main_path))
        self.get_current_path()
        GLib.idle_add(self.update_folder)

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

        # Search explorer panel button
        button_search = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="system-search-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_search.set_child(box)
        button_search.connect("clicked", self.on_search_button_clicked)

        box = Gtk.Box(spacing=6)
        box.append(button_folder_back)
        box.append(button_folder_forward)
        box.append(button_home)
        self.explorer_panel_header.pack_start(box)
        box = Gtk.Box(spacing=6)
        box.append(button_search)
        box.append(button_reload)

        # Box containing explorer panel specific buttons
        self.explorer_panel_headerbox = box
        self.explorer_panel_header.pack_end(box)

    def on_search_button_clicked(self, button):
        if self.search_bar.get_search_mode():
            self.search_bar.set_search_mode(False)
        else:
            self.search_bar.set_search_mode(True)
            self.search_entry.grab_focus()

    def on_search_changed(self, entry):
        if self.flow_box:
            self.flow_box.invalidate_filter()

    def filter_files(self, child):
        query = self.search_entry.get_text().lower()
        if not query:
            return True
        button = child.get_child()
        if not button:
            return False
        filename = button.get_name()
        return query in filename.lower()

    def _normalize_path(self, path):
        """Normalize path and replace home directory with ~."""
        abs_path = os.path.normpath(os.path.expanduser(path))
        home_dir = os.path.expanduser("~")
        if abs_path == home_dir:
            return "~"
        elif abs_path.startswith(home_dir + os.sep):
            return "~" + abs_path[len(home_dir):]
        return abs_path

    def get_current_path(self):
        self.main_path = self._normalize_path(self.main_path)
        return self.main_path

    def set_main_path(self, new_path):
        normalized_path = self._normalize_path(new_path)
        if self.main_path != normalized_path:
            self.main_path = normalized_path
            self.search_bar.set_search_mode(False)
            self.search_entry.set_text("")
            self.emit('path-changed', self.main_path)

    def go_back_in_explorer_panel(self, *a):
        path = os.path.expanduser(self.main_path)
        if os.path.exists(path) and os.path.isdir(path):
            new_path = os.path.dirname(path)
            self.set_main_path(new_path)
        if self.main_path == "/".join(self.controller.newelle_dir.split("/")[3:]):
            self.set_main_path("~")
        GLib.idle_add(self.update_folder)

    def go_home_in_explorer_panel(self, *a):
        self.set_main_path("~")
        GLib.idle_add(self.update_folder)

    def go_forward_in_explorer_panel(self, *a):
        if self.main_path[len(self.main_path) - 3 : len(self.main_path)] == "/..":
            self.set_main_path(self.main_path[0 : len(self.main_path) - 3])
            GLib.idle_add(self.update_folder)

    def update_folder(self, *a):
        if not self.check_streams["folder"]:
            self.check_streams["folder"] = True
            self.update_tab()
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
                    self.main_content.remove(self.folder_blocks_panel)
                    self.folder_blocks_panel = Gtk.Box(
                        orientation=Gtk.Orientation.VERTICAL, spacing=20, opacity=0.25
                    )
                    self.main_content.append(self.folder_blocks_panel)
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
                    self.main_content.remove(self.folder_blocks_panel)
                    self.folder_blocks_panel = Gtk.Box(
                        orientation=Gtk.Orientation.VERTICAL
                    )
                    self.main_content.append(self.folder_blocks_panel)

                    self.flow_box = Gtk.FlowBox(vexpand=True)
                    self.flow_box.set_valign(Gtk.Align.START)
                    self.flow_box.set_filter_func(self.filter_files)
                    
                    # Add right-click gesture for empty space
                    empty_space_right_click = Gtk.GestureClick()
                    empty_space_right_click.set_button(3)  # Right mouse button
                    empty_space_right_click.connect("pressed", self.on_empty_space_right_click)
                    
                    if os.path.normpath(self.main_path) == "~" or os.path.normpath(self.main_path) == os.path.expanduser("~"):
                        os.chdir(os.path.expanduser("~"))
                        fname = "/".join(self.controller.newelle_dir.split("/")[3:])
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(fname)
                        button.connect("clicked", self.open_folder)

                        # Add right-click gesture for Newelle folder
                        right_click = Gtk.GestureClick()
                        right_click.set_button(3)  # Right mouse button
                        right_click.connect("pressed", self.on_right_click, button, fname)
                        button.add_controller(right_click)

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

                        self.flow_box.append(button)
                    for file_info in os.listdir(os.path.expanduser(self.main_path)):
                        if (
                            file_info[0] == "."
                            and not self.controller.newelle_settings.hidden_files
                        ):
                            continue
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(file_info)
                        button.connect("clicked", self.open_folder)

                        # Add right-click gesture
                        right_click = Gtk.GestureClick()
                        right_click.set_button(3)  # Right mouse button
                        right_click.connect("pressed", self.on_right_click, button, file_info)
                        button.add_controller(right_click)

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

                        self.flow_box.append(button)
                    scrolled_window = Gtk.ScrolledWindow()
                    scrolled_window.set_policy(
                        Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
                    )
                    scrolled_window.set_child(self.flow_box)
                    
                    # Add the right-click gesture to the scrolled window for empty space clicks
                    scrolled_window.add_controller(empty_space_right_click)
                    
                    self.folder_blocks_panel.append(scrolled_window)
            else:
                self.set_main_path("~")
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
        name = button.get_name()
        full_path = os.path.join(os.path.expanduser(self.main_path), name)
        if os.path.exists(full_path):
            if os.path.isdir(full_path):
                self.set_main_path(full_path)
                os.chdir(os.path.expanduser(self.main_path))
                GLib.idle_add(self.update_folder)
            else:
                subprocess.run(["xdg-open", full_path])
        else:
            self.notification_block.add_toast(
                Adw.Toast(title=_("File not found"), timeout=2)
            )

    def is_excluded_format(self, file_path):
        """Check if a file is of an excluded format based on its extension."""
        excluded_extensions = ['.mp4', '.mp3', '.wav', '.avi', '.mov', '.mkv', '.docx', '.pptx', '.pdf', '.jpeg', '.jpg', '.png', '.gif', '.bmp', '.tiff', '.ico', '.webp', '.heic', '.heif']
        return any(file_path.endswith(ext) for ext in excluded_extensions)

    def create_context_menu(self, file_path, is_directory, button):
        """Create and return a context menu for files and folders"""
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()

        # Open in new tab
        if os.path.isdir(os.path.expanduser(file_path)):
            menu_model.append(_("Open in new tab"), "explorer.open_new_tab")
        elif not self.is_excluded_format(file_path):
            menu_model.append(_("Open in integrated editor"), "explorer.open_new_tab")

        # Open in file manager
        menu_model.append(_("Open in file manager"), "explorer.open_file_manager")

        # Rename
        menu_model.append(_("Rename"), "explorer.rename")

        # Delete
        menu_model.append(_("Delete"), "explorer.delete")

        # Copy full path
        menu_model.append(_("Copy full path"), "explorer.copy_path")

        menu.set_menu_model(menu_model)

        # Create action group
        action_group = Gio.SimpleActionGroup()

        # Open in new tab action
        action = Gio.SimpleAction.new("open_new_tab", None)
        action.connect("activate", self.on_open_new_tab, file_path)
        action_group.add_action(action)

        # Open in file manager action
        action = Gio.SimpleAction.new("open_file_manager", None)
        action.connect("activate", self.on_open_file_manager, file_path, is_directory)
        action_group.add_action(action)

        # Rename action
        action = Gio.SimpleAction.new("rename", None)
        action.connect("activate", self.on_rename, file_path, button)
        action_group.add_action(action)

        # Delete action
        action = Gio.SimpleAction.new("delete", None)
        action.connect("activate", self.on_delete, file_path)
        action_group.add_action(action)

        # Copy path action
        action = Gio.SimpleAction.new("copy_path", None)
        action.connect("activate", self.on_copy_path, file_path)
        action_group.add_action(action)

        menu.insert_action_group("explorer", action_group)
        return menu

    def on_open_new_tab(self, action, parameter, file_path):
        """Handler for 'Open in new tab' - emits signal for new tab creation"""
        # Emit the signal with the file path
        self.emit('new-tab-requested', file_path)

    def on_open_file_manager(self, action, parameter, file_path, is_directory):
        """Handler for 'Open in file manager'"""
        try:
            if is_directory:
                # Open the directory itself
                subprocess.run(["xdg-open", file_path])
            else:
                # Open the directory containing the file
                subprocess.run(["xdg-open", os.path.dirname(file_path)])
        except Exception as e:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Failed to open file manager"), timeout=3)
            )

    def on_rename(self, action, parameter, file_path, button):
        """Handler for 'Rename'"""
        popover = Gtk.Popover()
        popover.set_parent(button)

        # Create the content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Label and entry
        label = Gtk.Label(label=_("New name:"))
        label.set_halign(Gtk.Align.START)

        entry = Gtk.Entry()
        entry.set_text(os.path.basename(file_path))
        entry.select_region(0, -1)
        entry.set_width_chars(25)

        # Button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.add_css_class("flat")

        rename_button = Gtk.Button(label=_("Rename"))
        rename_button.add_css_class("suggested-action")

        button_box.append(cancel_button)
        button_box.append(rename_button)

        # Add widgets to content box
        content_box.append(label)
        content_box.append(entry)
        content_box.append(button_box)

        popover.set_child(content_box)

        def on_rename_clicked(button):
            new_name = entry.get_text().strip()
            if new_name and new_name != os.path.basename(file_path):
                try:
                    new_path = os.path.join(os.path.dirname(file_path), new_name)
                    os.rename(file_path, new_path)
                    self.notification_block.add_toast(
                        Adw.Toast(title=_("Renamed successfully"), timeout=2)
                    )
                    GLib.idle_add(self.update_folder)
                except Exception as e:
                    self.notification_block.add_toast(
                        Adw.Toast(title=_("Failed to rename: {}").format(str(e)), timeout=3)
                    )
            popover.popdown()

        def on_cancel_clicked(button):
            popover.popdown()

        def on_entry_activate(entry):
            on_rename_clicked(None)

        # Connect signals
        rename_button.connect("clicked", on_rename_clicked)
        cancel_button.connect("clicked", on_cancel_clicked)
        entry.connect("activate", on_entry_activate)

        # Show the popover
        popover.popup()
        entry.grab_focus()

    def on_delete(self, action, parameter, file_path):
        """Handler for 'Delete'"""
        dialog = Adw.AlertDialog.new(_("Delete File?"), None)

        dialog.set_body(_("Are you sure you want to delete \"{}\"?").format(os.path.basename(file_path)))

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))

        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dialog, response):
            if response == "delete":
                try:
                    if os.path.isdir(file_path):
                        # For directories, use rmdir (only works if empty)
                        # For recursive deletion, you might want to use shutil.rmtree
                        import shutil
                        shutil.rmtree(file_path)
                    else:
                        os.remove(file_path)

                    self.notification_block.add_toast(
                        Adw.Toast(title=_("Deleted successfully"), timeout=2)
                    )
                    GLib.idle_add(self.update_folder)
                except Exception as e:
                    self.notification_block.add_toast(
                        Adw.Toast(title=_("Failed to delete: {}").format(str(e)), timeout=3)
                    )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def on_copy_path(self, action, parameter, file_path):
        """Handler for 'Copy full path'"""
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(file_path))

            self.notification_block.add_toast(
                Adw.Toast(title=_("Path copied to clipboard"), timeout=2)
            )
        except Exception as e:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Failed to copy path"), timeout=3)
            )

    def on_right_click(self, gesture, n_press, x, y, button, file_name):
        """Handler for right-click on files/folders"""
        if n_press == 1:  # Single right-click
            file_path = os.path.join(os.path.expanduser(self.main_path), file_name)
            is_directory = os.path.isdir(file_path)

            menu = self.create_context_menu(file_path, is_directory, button)
            menu.set_parent(button)

            # Position the menu at the click location
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            menu.set_pointing_to(rect)

            menu.popup()

    def update_tab(self):
        if self.tab is not None:
            self.tab.set_title(self.get_current_path())
            self.tab.set_icon(Gio.ThemedIcon(name=File(self.main_path, ".").get_icon_name()))

    def set_tab(self, tab):
        self.tab = tab
        if self.tab is not None:
            self.update_tab()

    def create_empty_space_context_menu(self):
        """Create and return a context menu for empty spaces"""
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()

        # Create new folder
        menu_model.append(_("Create new folder"), "explorer.create_folder")

        # Create new file
        menu_model.append(_("Create new file"), "explorer.create_file")

        # Open terminal here
        menu_model.append(_("Open Terminal Here"), "explorer.open_terminal")

        # Open in file manager
        menu_model.append(_("Open in file manager"), "explorer.open_file_manager_here")

        menu.set_menu_model(menu_model)

        # Create action group
        action_group = Gio.SimpleActionGroup()

        # Create folder action
        action = Gio.SimpleAction.new("create_folder", None)
        action.connect("activate", self.on_create_folder)
        action_group.add_action(action)

        # Create file action
        action = Gio.SimpleAction.new("create_file", None)
        action.connect("activate", self.on_create_file)
        action_group.add_action(action)

        # Open terminal action
        action = Gio.SimpleAction.new("open_terminal", None)
        action.connect("activate", self.on_open_terminal_here)
        action_group.add_action(action)

        # Open file manager action
        action = Gio.SimpleAction.new("open_file_manager_here", None)
        action.connect("activate", self.on_open_file_manager_here)
        action_group.add_action(action)

        menu.insert_action_group("explorer", action_group)
        return menu

    def on_empty_space_right_click(self, gesture, n_press, x, y):
        """Handler for right-click on empty spaces"""
        if n_press == 1:  # Single right-click
            # Check if the click is on the flow_box background (not on a button)
            widget = gesture.get_widget()
            
            menu = self.create_empty_space_context_menu()
            menu.set_parent(widget)

            # Position the menu at the click location
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            menu.set_pointing_to(rect)

            menu.popup()

    def on_create_folder(self, action, parameter):
        """Handler for 'Create new folder'"""
        self.show_create_dialog(_("Create New Folder"), _("Folder name:"), "untitled", True)

    def on_create_file(self, action, parameter):
        """Handler for 'Create new file'"""
        self.show_create_dialog(_("Create New File"), _("File name:"), "untitled.txt", False)

    def on_open_terminal_here(self, action, parameter):
        """Handler for 'Open Terminal Here' - emits signal"""
        current_path = os.path.expanduser(self.main_path)
        self.emit('open-terminal-requested', current_path)

    def on_open_file_manager_here(self, action, parameter):
        """Handler for 'Open in file manager' for current directory"""
        try:
            subprocess.run(["xdg-open", os.path.expanduser(self.main_path)])
        except Exception as e:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Failed to open file manager"), timeout=3)
            )

    def show_create_dialog(self, title, label_text, default_name, is_folder):
        """Show a popover dialog for creating new files or folders"""
        # Create a temporary button to parent the popover to
        temp_button = Gtk.Button()
        temp_button.set_parent(self.folder_blocks_panel)
        
        popover = Gtk.Popover()
        popover.set_parent(temp_button)

        # Create the content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Label and entry
        label = Gtk.Label(label=label_text)
        label.set_halign(Gtk.Align.START)

        entry = Gtk.Entry()
        entry.set_text(default_name)
        entry.select_region(0, -1)
        entry.set_width_chars(25)

        # Button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.add_css_class("flat")

        create_button = Gtk.Button(label=_("Create"))
        create_button.add_css_class("suggested-action")

        button_box.append(cancel_button)
        button_box.append(create_button)

        # Add widgets to content box
        content_box.append(label)
        content_box.append(entry)
        content_box.append(button_box)

        popover.set_child(content_box)

        def on_create_clicked(button):
            name = entry.get_text().strip()
            if name:
                try:
                    new_path = os.path.join(os.path.expanduser(self.main_path), name)
                    if is_folder:
                        os.makedirs(new_path, exist_ok=False)
                        self.notification_block.add_toast(
                            Adw.Toast(title=_("Folder created successfully"), timeout=2)
                        )
                    else:
                        # Create an empty file
                        with open(new_path, 'w') as f:
                            pass
                        self.notification_block.add_toast(
                            Adw.Toast(title=_("File created successfully"), timeout=2)
                        )
                    GLib.idle_add(self.update_folder)
                except FileExistsError:
                    self.notification_block.add_toast(
                        Adw.Toast(title=_("A file or folder with that name already exists"), timeout=3)
                    )
                except Exception as e:
                    action_type = _("folder") if is_folder else _("file")
                    self.notification_block.add_toast(
                        Adw.Toast(title=_("Failed to create {}: {}").format(action_type, str(e)), timeout=3)
                    )
            popover.popdown()
            temp_button.unparent()

        def on_cancel_clicked(button):
            popover.popdown()
            temp_button.unparent()

        def on_entry_activate(entry):
            on_create_clicked(None)

        def on_popover_closed(popover):
            temp_button.unparent()

        # Connect signals
        create_button.connect("clicked", on_create_clicked)
        cancel_button.connect("clicked", on_cancel_clicked)
        entry.connect("activate", on_entry_activate)
        popover.connect("closed", on_popover_closed)

        # Show the popover
        popover.popup()
        entry.grab_focus()
