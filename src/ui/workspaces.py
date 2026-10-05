"""Native workspace controls and per-workspace chat tab sessions."""
import gettext
import os

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from ..workspaces import DEFAULT_WORKSPACE

_ = gettext.gettext


class WorkspaceWindow:
    def build_workspace_picker(self):
        self._workspace_views = {}
        self._workspace_ui_switching = False
        self.workspace_button = Gtk.MenuButton(
            margin_start=12, margin_end=12, margin_top=6, margin_bottom=6,
            tooltip_text=_("Switch workspace"), css_classes=["workspace-switcher"],
        )
        self.chats_secondary_box.append(self.workspace_button)
        self.settings.bind("hide-workspaces", self.workspace_button, "visible",
                           Gio.SettingsBindFlags.DEFAULT | Gio.SettingsBindFlags.INVERT_BOOLEAN)
        self.refresh_workspace_picker()
        self.connect("close-request", self._save_workspace_on_close)

    def _workspace_path_label(self, workspace):
        path = os.path.normpath(os.path.expanduser(workspace.get("path") or "~"))
        home = os.path.expanduser("~")
        if path == home:
            return "~"
        if path.startswith(home + os.sep):
            return "~" + path[len(home):]
        return path

    def _workspace_popover_width(self):
        # Match the sidebar button, allowing for content margins and popover padding.
        button_width = self.workspace_button.get_width() or 352
        return max(240, min(320, button_width - 32))

    def _apply_workspace_avatar(self, avatar, workspace):
        avatar.set_custom_image(None)
        icon = workspace.get("icon")
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        if icon and not theme.has_icon(icon):
            icon = None
        avatar.set_icon_name(icon or "folder-visiting-symbolic")
        avatar.set_show_initials(not bool(icon))
        picture = workspace.get("picture")
        if picture:
            try:
                avatar.set_custom_image(Gdk.Texture.new_from_bytes(GLib.Bytes.new(picture)))
            except (GLib.Error, TypeError, ValueError):
                # A missing theme icon or unreadable old image still has initials.
                pass

    def _workspace_avatar(self, workspace, size=32):
        avatar = Adw.Avatar(
            size=size, text=workspace.get("name", ""), valign=Gtk.Align.CENTER,
        )
        self._apply_workspace_avatar(avatar, workspace)
        return avatar

    def _workspace_avatar_editor(self, workspace):
        # Keep edits local to the dialog until Save. Store a small PNG in the
        # workspace record so portal access and the source file are not needed later.
        appearance = {"icon": workspace.get("icon"), "picture": workspace.get("picture")}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        avatar = self._workspace_avatar(workspace, size=64)
        avatar.set_halign(Gtk.Align.CENTER)
        overlay = Gtk.Overlay()
        overlay.set_child(avatar)
        badge = Gtk.Image(
            icon_name="document-edit-symbolic", pixel_size=12,
            halign=Gtk.Align.END, valign=Gtk.Align.END,
            css_classes=["workspace-avatar-badge"], can_target=False,
        )
        overlay.add_overlay(badge)
        chooser_button = Gtk.MenuButton(
            halign=Gtk.Align.CENTER, css_classes=["flat", "workspace-avatar-button"],
            tooltip_text=_("Change workspace image"),
        )
        chooser_button.set_child(overlay)
        box.append(chooser_button)
        error = Gtk.Label(wrap=True, css_classes=["error", "caption"], visible=False)
        box.append(error)
        popover = Gtk.Popover()
        chooser_button.set_popover(popover)
        menu = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
        )
        photo = Gtk.Button(css_classes=["flat"])
        photo.set_child(Adw.ButtonContent(
            icon_name="folder-open-symbolic", label=_("Choose Image…"),
        ))
        menu.append(photo)
        menu.append(Gtk.Separator())
        menu.append(Gtk.Label(
            label=_("Choose an icon"), xalign=0, margin_start=6,
            css_classes=["caption", "dim-label"],
        ))
        initials = Gtk.Button(label=_("Restore Initials"), css_classes=["flat"])
        grid = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            min_children_per_line=5, max_children_per_line=6,
            column_spacing=4, row_spacing=4,
        )
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        candidates = [
            workspace.get("icon"), "chat-bubbles-text-symbolic", "skills-symbolic",
            "tools-symbolic", "brain-augemnted-symbolic", "gnome-terminal-symbolic",
        ] + self.FOLDER_ICON_CANDIDATES
        icon_buttons = {}

        def refresh():
            self._apply_workspace_avatar(avatar, appearance)
            for icon_name, button in icon_buttons.items():
                button.set_active(appearance["icon"] == icon_name and not appearance["picture"])
            initials.set_sensitive(bool(appearance["icon"] or appearance["picture"]))
            error.set_visible(False)

        def select_icon(_button, icon_name):
            appearance.update(icon=icon_name, picture=None)
            refresh()
            popover.popdown()

        for icon_name in dict.fromkeys(candidates):
            if not icon_name or not theme.has_icon(icon_name):
                continue
            button = Gtk.ToggleButton(
                icon_name=icon_name, css_classes=["flat", "circular"],
                tooltip_text=icon_name.removesuffix("-symbolic").replace("-", " ").title(),
            )
            button.connect("clicked", select_icon, icon_name)
            icon_buttons[icon_name] = button
            grid.append(button)
        scroll = Gtk.ScrolledWindow(
            width_request=260, max_content_height=180, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroll.set_child(grid)
        menu.append(scroll)
        menu.append(Gtk.Separator())
        menu.append(initials)
        popover.set_child(menu)
        initials.connect("clicked", lambda _button: (appearance.update(icon=None, picture=None), refresh(), popover.popdown()))

        def choose_image(_button):
            popover.popdown()
            image_filter = Gtk.FileFilter()
            image_filter.set_name(_("Images"))
            image_filter.add_pixbuf_formats()
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(image_filter)
            chooser = Gtk.FileDialog(title=_("Choose workspace image"), filters=filters)

            def chosen(source, result):
                try:
                    image_file = source.open_finish(result)
                except GLib.Error:
                    return  # Dismissing the system file chooser is not an error.
                if image_file is None:
                    return
                try:
                    filename = image_file.get_path()
                    if filename is None:
                        raise ValueError("Image must be a local file")
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, 256, 256, True)
                    pixbuf = pixbuf.apply_embedded_orientation()
                    success, image_bytes = pixbuf.save_to_bufferv("png", [], [])
                    if not success:
                        raise ValueError("Could not encode workspace image")
                    appearance.update(icon=None, picture=bytes(image_bytes))
                    refresh()
                except (GLib.Error, OSError, TypeError, ValueError):
                    error.set_label(_("This image could not be opened. Choose another image."))
                    error.set_visible(True)
            chooser.open(self, None, chosen)
        photo.connect("clicked", choose_image)
        refresh()
        return box, avatar, appearance

    def _workspace_row(self, workspace, active=False):
        path = self._workspace_path_label(workspace)
        profile = workspace.get("profile")
        row = Adw.ActionRow(
            title=workspace["name"], subtitle=f"{profile} · {path}" if profile else path,
            use_markup=False, activatable=True, title_lines=1, subtitle_lines=1, hexpand=True,
        )
        row.set_tooltip_text(path)
        row.add_prefix(self._workspace_avatar(workspace))
        if active:
            row.add_css_class("workspace-active")
            row.add_suffix(Gtk.Image(
                icon_name="check-plain-symbolic", css_classes=["accent"],
                tooltip_text=_("Current workspace"),
            ))
        return row

    def refresh_workspace_picker(self):
        workspace = self.controller.active_workspace
        self._workspace_configuration = (workspace.get("profile"), workspace["path"], workspace["mode"])
        self.set_title(_("Newelle — {workspace}").format(workspace=workspace["name"]))
        content = Gtk.Box(spacing=10)
        content.append(self._workspace_avatar(workspace))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        labels.append(Gtk.Label(
            label=workspace["name"], xalign=0, ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=20, css_classes=["heading"],
        ))
        labels.append(Gtk.Label(
            label=self._workspace_path_label(workspace), xalign=0,
            ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=24,
            css_classes=["caption", "dim-label"],
        ))
        content.append(labels)
        content.append(Gtk.Image(icon_name="pan-down-symbolic", css_classes=["dim-label"]))
        self.workspace_button.set_child(content)
        self.workspace_button.set_tooltip_text(
            _("Switch workspace") + "\n" + self._workspace_path_label(workspace)
        )
        popover = Gtk.Popover(css_classes=["workspace-popover"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      width_request=self._workspace_popover_width(),
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        box.append(Gtk.Label(
            label=_("Workspaces"), xalign=0, margin_start=6,
            css_classes=["heading"],
        ))
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        for wid, entry in self.controller.workspaces.items():
            row = self._workspace_row(entry, active=wid == self.controller.active_workspace_id)
            row.connect("activated", lambda _row, key=wid: (popover.popdown(), self.switch_workspace(key)))
            open_window = Gtk.Button(
                icon_name="window-new-symbolic", valign=Gtk.Align.CENTER,
                css_classes=["flat", "circular"], tooltip_text=_("Open in New Window"),
            )
            open_window.connect("clicked", lambda _button, key=wid: (popover.popdown(), self.app.open_workspace_window(key, self)))
            row.add_suffix(open_window)
            edit = Gtk.Button(
                icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                css_classes=["flat", "circular"], tooltip_text=_("Edit workspace"),
            )
            edit.connect("clicked", lambda _button, key=wid: (popover.popdown(), self.edit_workspace_dialog(key)))
            row.add_suffix(edit)
            rows.append(row)
        scroll = Gtk.ScrolledWindow(
            max_content_height=360, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroll.set_child(rows)
        box.append(scroll)
        create = Gtk.Button(css_classes=["flat"])
        create.set_child(Adw.ButtonContent(icon_name="plus-symbolic", label=_("New Workspace")))
        create.connect("clicked", lambda _button: (popover.popdown(), self.edit_workspace_dialog()))
        box.append(create)
        popover.connect("map", lambda _popover: box.set_size_request(self._workspace_popover_width(), -1))
        popover.set_child(box)
        self.workspace_button.set_popover(popover)

    def workspace_toast(self, message):
        self.notification_block.add_toast(Adw.Toast(title=message))

    def workspace_ui_busy(self):
        # A chat generation is owned by its ChatTab and can continue while a
        # different workspace is selected.  Keep this predicate focused on
        # operations that truly require the visible workspace to be idle.
        if self._workspace_ui_switching or self.controller.workspace_switching:
            return True
        for i in range(self.chat_tabs.get_n_pages()):
            tab = self.chat_tabs.get_nth_page(i).get_child()
            if tab.recording:
                return True
        voice = getattr(self.app, "voice_win", None)
        if voice is not None and (voice.main_window is self or voice.controller is self.controller):
            return True
        for i in range(self.canvas_tabs.get_n_pages()):
            if getattr(self.canvas_tabs.get_nth_page(i).get_child(), "call_active", False):
                return True
        # Detached call panels still belong to the application.
        for window in self.app.get_windows():
            view = getattr(window, "tab_view", None)
            if view is not None:
                for i in range(view.get_n_pages()):
                    panel = view.get_nth_page(i).get_child()
                    if getattr(panel, "call_active", False) and getattr(panel, "controller", None) is self.controller:
                        return True
        return False

    def save_workspace_tabs(self):
        if getattr(self, "_workspace_ui_switching", True) or not hasattr(self, "chat_tabs"):
            return
        workspace = self.controller.active_workspace
        open_chats = [self.chat_tabs.get_nth_page(i).get_child().chat_id
                      for i in range(self.chat_tabs.get_n_pages())]
        tab = self.get_active_chat_tab()
        selected = tab.chat_id if tab else None
        if workspace["open_chats"] == open_chats and workspace["selected_chat"] == selected:
            return
        workspace["open_chats"] = open_chats
        workspace["selected_chat"] = selected
        if tab:
            self.settings.set_int("chat", tab.chat_id)
        self.controller.save_chats()

    def _save_workspace_on_close(self, *_args):
        self.save_workspace_tabs()
        return False

    def remember_workspace_drafts(self, view=None):
        views = (view,) if view is not None else (self.chat_tabs, *self._workspace_views.values())
        for tabs in views:
            for index in range(tabs.get_n_pages()):
                tab = tabs.get_nth_page(index).get_child()
                tab.remember_draft()

    def release_workspace_tabs(self, workspace_id):
        """Save parked drafts before another window takes over this workspace."""
        cached = getattr(self, "_workspace_views", {}).get(workspace_id)
        if cached is None:
            return True
        if self.controller.workspace_requests_by_workspace.get(workspace_id):
            return False
        for index in range(cached.get_n_pages()):
            tab = cached.get_nth_page(index).get_child()
            if not tab.status or tab.recording:
                return False
        self.remember_workspace_drafts(cached)
        self._workspace_views.pop(workspace_id)
        while cached.get_n_pages():
            cached.close_page(cached.get_nth_page(0))
        return True

    def restore_workspace_tabs(self):
        self._workspace_ui_switching = True
        try:
            workspace = self.controller.active_workspace
            selected = self.controller.ensure_workspace_chat()
            cached = self._workspace_views.pop(self.controller.active_workspace_id, None)
            if cached is not None:
                while cached.get_n_pages():
                    page = cached.get_nth_page(0)
                    cid = page.get_child().chat_id
                    if cid in self.controller.workspace_chats():
                        cached.transfer_page(page, self.chat_tabs, self.chat_tabs.get_n_pages())
                    else:
                        cached.close_page(page)
            for cid in list(workspace["open_chats"]):
                if self.get_tab_for_chat(cid) is None:
                    self.add_chat_tab(cid)
            page = self.get_tab_for_chat(selected) or self.add_chat_tab(selected)
            if page is not None:
                self.chat_tabs.set_selected_page(page)
            self.chat_id = selected
        finally:
            self._workspace_ui_switching = False
        self.update_history()
        self.refresh_mode_buttons()

    def switch_workspace(self, workspace_id, force=False):
        if workspace_id == self.controller.active_workspace_id and not force:
            return True
        existing = self.app.workspace_window(workspace_id, exclude=self)
        if existing is not None:
            existing.present()
            return True
        for window in self.app.main_windows:
            if window is not self and not window.release_workspace_tabs(workspace_id):
                self.workspace_toast(_("Finish or stop active work before opening this workspace in another window."))
                return False
        if self.workspace_ui_busy():
            self.workspace_toast(_("Finish or stop active work before switching workspaces."))
            return False
        self.save_workspace_tabs()
        old_id = self.controller.active_workspace_id
        try:
            # Controller reserves the switch before any settings change, and prevents
            # workers from starting until the handlers have been reloaded.
            self._workspace_ui_switching = True
            reloads = self.controller.switch_workspace(workspace_id)
        except (ValueError, RuntimeError, OSError) as error:
            self.workspace_toast(str(error))
            return False
        finally:
            self._workspace_ui_switching = False
        self._workspace_ui_switching = True
        try:
            cached = Adw.TabView()
            self._workspace_views[old_id] = cached
            while self.chat_tabs.get_n_pages():
                page = self.chat_tabs.get_nth_page(0)
                self.chat_tabs.transfer_page(page, cached, cached.get_n_pages())
            self.update_settings(reloads)
            self.main_path = self.controller.newelle_settings.main_path
            explorer = self.get_current_explorer_panel()
            if explorer is None:
                for i in range(self.canvas_tabs.get_n_pages()):
                    candidate = self.canvas_tabs.get_nth_page(i).get_child()
                    if hasattr(candidate, "set_main_path"):
                        explorer = candidate
                        break
            if explorer is not None:
                explorer.set_main_path(self.main_path)
                explorer.update_folder()
            self.refresh_profiles_box()
            self.refresh_workspace_picker()
        finally:
            self._workspace_ui_switching = False
        self.restore_workspace_tabs()
        self._update_chat_tab_llm_buttons()
        self._refresh_compact_mode()
        self._refresh_compact_input_bar()
        self.show_workspace_path_notice()
        return True

    def show_workspace_path_notice(self):
        notice = self.controller.workspace_path_notice
        if notice:
            self.workspace_toast(notice)
            self.controller.workspace_path_notice = None

    def remember_chat_profile(self, chat_id):
        if self._workspace_ui_switching or self.controller.workspace_switching:
            return False
        if self.controller.active_workspace.get("profile") or self.chat_id != chat_id:
            return False
        chat = self.controller.workspace_chats().get(chat_id)
        if chat and self.controller.newelle_settings.remember_profile:
            self.switch_profile(chat.get("profile"))
        return False

    def edit_workspace_dialog(self, workspace_id=None):
        workspace = self.controller.workspaces.get(workspace_id, {})
        dialog = Adw.AlertDialog(
            heading=_("Edit Workspace") if workspace_id else _("New Workspace"),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save") if workspace_id else _("Create"))
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        avatar_editor, avatar, appearance = self._workspace_avatar_editor(workspace)
        box.append(avatar_editor)
        name = Adw.EntryRow(title=_("Name"), text=workspace.get("name", ""))
        identity = Adw.PreferencesGroup()
        identity.add(name)
        box.append(identity)
        profiles = [None] + list(self.controller.newelle_settings.profile_settings)
        profile = Adw.ComboRow(title=_("Profile"), model=Gtk.StringList.new([_("No linked profile")] + profiles[1:]))
        profile.set_selected(profiles.index(workspace.get("profile")) if workspace.get("profile") in profiles else 0)
        profile_group = Adw.PreferencesGroup(
            description=_("Applied when you switch to this workspace."),
        )
        profile_group.add(profile)
        box.append(profile_group)
        path = Adw.EntryRow(title=_("Main path"), text=workspace.get("path", self.settings.get_string("path")))
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=_("Choose directory"))
        path.add_suffix(browse)
        path_group = Adw.PreferencesGroup(
            description=_("The starting directory for files, tools, and project skills."),
        )
        path_group.add(path)
        box.append(path_group)
        error = Gtk.Label(wrap=True, xalign=0, css_classes=["error", "caption"], visible=False)
        box.append(error)
        if workspace_id and workspace_id != DEFAULT_WORKSPACE:
            delete = Gtk.Button(label=_("Delete Workspace"), css_classes=["flat", "destructive-action"])
            delete.connect("clicked", lambda _button: (dialog.close(), self.delete_workspace_dialog(workspace_id)))
            box.append(delete)
        dialog.set_extra_child(box)

        def validate(*_args):
            valid_path = os.path.isdir(os.path.expanduser(path.get_text()))
            dialog.set_response_enabled("save", bool(name.get_text().strip()) and valid_path)
            error.set_label("" if valid_path else _("Choose an existing directory."))
            error.set_visible(not valid_path)
            if valid_path:
                path.remove_css_class("error")
            else:
                path.add_css_class("error")
            avatar.set_text(name.get_text())
        name.connect("changed", validate)
        path.connect("changed", validate)
        validate()

        def choose(_button):
            chooser = Gtk.FileDialog(title=_("Choose directory"))
            def chosen(source, result):
                try:
                    folder = source.select_folder_finish(result)
                    if folder and folder.get_path():
                        path.set_text(folder.get_path())
                except GLib.Error:
                    pass
            chooser.select_folder(self, None, chosen)
        browse.connect("clicked", choose)

        def respond(_dialog, response):
            if response != "save":
                return
            if self.workspace_ui_busy() or self.controller.workspace_requests:
                self.workspace_toast(_("Finish or stop active work before editing workspaces."))
                return
            linked_profile = profiles[profile.get_selected()]
            directory = os.path.abspath(os.path.expanduser(path.get_text()))
            if workspace_id:
                try:
                    self.controller.edit_workspace(workspace_id, name.get_text(), linked_profile, directory, appearance=appearance)
                except ValueError as error:
                    self.workspace_toast(str(error))
                    return
                if workspace_id == self.controller.active_workspace_id:
                    self.switch_workspace(workspace_id, force=True)
            else:
                wid = self.controller.create_workspace(name.get_text(), linked_profile, directory, appearance=appearance)
                self.switch_workspace(wid)
            self.refresh_workspace_picker()
        dialog.connect("response", respond)
        dialog.present(self)

    def delete_workspace_dialog(self, workspace_id):
        dialog = Adw.AlertDialog(heading=_("Delete Workspace?"), body=_("Its chats and folders will be moved to Default."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        def respond(_dialog, response):
            if response != "delete":
                return
            if self.workspace_ui_busy() or self.controller.workspace_requests:
                self.workspace_toast(_("Finish or stop active work before deleting workspaces."))
                return
            if workspace_id == self.controller.active_workspace_id and not self.switch_workspace(DEFAULT_WORKSPACE):
                return
            try:
                self.controller.delete_workspace(workspace_id)
            except ValueError as error:
                self.workspace_toast(str(error))
                return
            cached = self._workspace_views.pop(workspace_id, None)
            if cached is not None:
                target = self._workspace_views.setdefault(DEFAULT_WORKSPACE, Adw.TabView())
                if self.controller.active_workspace_id == DEFAULT_WORKSPACE:
                    target = self.chat_tabs
                while cached.get_n_pages():
                    cached.transfer_page(cached.get_nth_page(0), target, target.get_n_pages())
            self.refresh_workspace_picker()
            self.update_history()
            self.controller.save_chats()
        dialog.connect("response", respond)
        dialog.present(self)

    def add_workspace_chat_action(self, row, chat_id):
        button = Gtk.Button(
            icon_name="folder-visiting-symbolic", css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER, tooltip_text=_("Move to Workspace"),
        )
        button.connect("clicked", lambda _button: self.move_chat_workspace_dialog(chat_id))
        button.set_sensitive(len(self.controller.workspaces) > 1)
        row.actions_box.prepend(button)

    def move_chat_workspace_dialog(self, chat_id):
        chat = self.controller.workspace_chats().get(chat_id)
        if chat is None:
            return
        dialog = Adw.AlertDialog(
            heading=_("Move to Workspace"),
            body=_("Choose a workspace for “{name}” and its branches.").format(name=chat["name"]),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.set_close_response("cancel")
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])

        def select_destination(_row, workspace_id):
            dialog.close()
            if chat_id in self.controller.workspace_chats():
                self.move_chat_to_workspace(chat_id, workspace_id)

        for wid, workspace in self.controller.workspaces.items():
            if wid == self.controller.active_workspace_id:
                continue
            destination = self._workspace_row(workspace)
            destination.connect("activated", select_destination, wid)
            rows.append(destination)
        scroll = Gtk.ScrolledWindow(
            width_request=320, max_content_height=320, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroll.set_child(rows)
        dialog.set_extra_child(scroll)
        dialog.present(self)

    def move_chat_to_workspace(self, chat_id, workspace_id):
        if workspace_id == self.controller.active_workspace_id:
            return True
        if self.workspace_ui_busy() or self.controller.workspace_requests:
            self.workspace_toast(_("Finish or stop active work before moving chats."))
            return
        self.save_workspace_tabs()
        try:
            moving = self.controller.move_chat_to_workspace(chat_id, workspace_id)
        except ValueError as error:
            self.workspace_toast(str(error))
            return
        target = self._workspace_views.setdefault(workspace_id, Adw.TabView())
        self._workspace_ui_switching = True
        try:
            for cid in moving:
                page = self.get_tab_for_chat(cid)
                if page is not None:
                    self.chat_tabs.transfer_page(page, target, target.get_n_pages())
                    self.controller.workspaces[workspace_id]["open_chats"].append(cid)
            selected = self.controller.ensure_workspace_chat()
            self.add_chat_tab(selected)
            self.chat_id = selected
        finally:
            self._workspace_ui_switching = False
        destination = self.app.workspace_window(workspace_id, exclude=self)
        if destination is not None:
            self.release_workspace_tabs(workspace_id)
            selected_page = destination.chat_tabs.get_selected_page()
            for cid in self.controller.workspaces[workspace_id]["open_chats"]:
                destination.add_chat_tab(cid)
            if selected_page is not None:
                destination.chat_tabs.set_selected_page(selected_page)
            destination.save_workspace_tabs()
        self.update_history()
        return True
