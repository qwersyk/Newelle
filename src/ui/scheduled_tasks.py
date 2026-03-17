from gi.repository import Gtk, Adw, Gio


class ScheduledTasksWindow(Gtk.Window):
    def __init__(self, app, *args, **kwargs):
        super().__init__(*args, **kwargs, title=_("Scheduled Tasks"))
        self.app = app
        self.controller = app.win.controller
        self.set_default_size(700, 520)
        self.set_transient_for(app.win)
        self.set_modal(True)

        header = Adw.HeaderBar(css_classes=["flat"])
        self.set_titlebar(header)
        refresh_button = Gtk.Button(css_classes=["flat"])
        refresh_button.set_child(Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic")))
        refresh_button.connect("clicked", self.refresh_tasks)
        header.pack_end(refresh_button)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(self.scrolled_window)

        self.main = Gtk.Box(
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
            orientation=Gtk.Orientation.VERTICAL,
        )
        self.scrolled_window.set_child(self.main)

        self.tasks_group = Adw.PreferencesGroup(
            title=_("Scheduled Agent Runs"),
            description=_("These tasks only run while Newelle is open."),
        )
        self.main.append(self.tasks_group)
        self._task_rows = []

        self.refresh_tasks()

    def _format_timestamp(self, value):
        if not value:
            return _("Never")
        try:
            dt = self.controller._parse_scheduled_datetime(value)
        except ValueError:
            return value
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")

    def _get_schedule_label(self, task):
        if task["schedule_type"] == "once":
            return _("One time at {0}").format(self._format_timestamp(task["run_at"]))
        return _("Cron: {0}").format(task["cron"])

    def _get_status_label(self, task):
        if task.get("running"):
            return _("Running now")
        if task.get("enabled"):
            return _("Enabled")
        return _("Disabled")

    def _get_folder_name(self, task):
        """Get the folder name for a task, or default string."""
        folder_id = task.get("folder_id")
        if folder_id is not None and folder_id in self.controller.folders:
            return self.controller.folders[folder_id]["name"]
        return _("None")

    def _open_latest_chat(self, button, chat_id):
        if chat_id is None or chat_id not in self.app.win.chats:
            return
        self.app.win.present()
        self.app.win.chose_chat(chat_id)
        self.close()

    def _toggle_task(self, button, task_id, enabled):
        self.controller.set_scheduled_task_enabled(task_id, not enabled)
        self.refresh_tasks()

    def _delete_task(self, button, task_id):
        self.controller.delete_scheduled_task(task_id)
        self.refresh_tasks()

    def _append_detail_row(self, parent_row, title, subtitle):
        detail_row = Adw.ActionRow(title=title, subtitle=subtitle)
        detail_row.set_activatable(False)
        parent_row.add_row(detail_row)

    def refresh_tasks(self, *args):
        for row in self._task_rows:
            self.tasks_group.remove(row)
        self._task_rows = []

        tasks = self.controller.get_scheduled_tasks()
        if not tasks:
            empty_row = Adw.ActionRow(
                title=_("No scheduled tasks"),
                subtitle=_("Use the schedule_task tool to create one."),
            )
            empty_row.set_activatable(False)
            self.tasks_group.add(empty_row)
            self._task_rows.append(empty_row)
            return

        for task in tasks:
            next_run = self._format_timestamp(task.get("next_run_at"))
            subtitle = _("{0} • Next run: {1}").format(
                self._get_status_label(task),
                next_run,
            )
            row = Adw.ExpanderRow(
                title=self._get_schedule_label(task),
                subtitle=subtitle,
            )

            open_button = Gtk.Button(css_classes=["flat"], icon_name="chat-bubbles-text-symbolic")
            latest_chat_id = task.get("latest_chat_id")
            open_button.set_tooltip_text(_("Open latest chat"))
            open_button.set_sensitive(
                latest_chat_id is not None and latest_chat_id in self.app.win.chats
            )
            open_button.connect("clicked", self._open_latest_chat, latest_chat_id)
            row.add_suffix(open_button)

            toggle_icon = "media-playback-pause-symbolic" if task.get("enabled") else "media-playback-start-symbolic"
            toggle_button = Gtk.Button(css_classes=["flat"], icon_name=toggle_icon)
            toggle_button.set_tooltip_text(_("Disable task") if task.get("enabled") else _("Enable task"))
            toggle_button.connect("clicked", self._toggle_task, task["id"], task.get("enabled", False))
            row.add_suffix(toggle_button)

            delete_button = Gtk.Button(css_classes=["flat"], icon_name="user-trash-symbolic")
            delete_button.set_tooltip_text(_("Delete task"))
            delete_button.connect("clicked", self._delete_task, task["id"])
            row.add_suffix(delete_button)

            self._append_detail_row(row, _("Task"), task["task"])
            self._append_detail_row(row, _("Folder"), self._get_folder_name(task))
            self._append_detail_row(row, _("Next run"), next_run)
            self._append_detail_row(row, _("Last run"), self._format_timestamp(task.get("last_run_at")))
            self._append_detail_row(row, _("Last status"), task.get("last_run_status") or _("Not run yet"))
            if latest_chat_id is not None:
                self._append_detail_row(row, _("Latest chat"), str(latest_chat_id + 1))
            if task.get("last_error"):
                self._append_detail_row(row, _("Last error"), task["last_error"])

            self.tasks_group.add(row)
            self._task_rows.append(row)
