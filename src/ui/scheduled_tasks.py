import datetime

from gi.repository import Gtk, Adw, Gio


class AddScheduledTaskWindow(Gtk.Window):
    def __init__(self, parent):
        super().__init__(
            title=_("Add Scheduled Task"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.parent_window = parent
        self.controller = parent.controller
        self.set_default_size(560, 400)

        header = Adw.HeaderBar(css_classes=["flat"])
        self.set_titlebar(header)

        cancel_button = Gtk.Button(label=_("Cancel"), css_classes=["flat"])
        cancel_button.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel_button)

        self.add_button = Gtk.Button(
            label=_("Add"),
            css_classes=["suggested-action"],
            sensitive=False,
        )
        self.add_button.connect("clicked", self._add_task)
        header.pack_end(self.add_button)

        page = Adw.PreferencesPage()

        task_group = Adw.PreferencesGroup(
            title=_("Task"),
            description=_("Describe what the agent should do when the task runs."),
        )
        self.task_row = Adw.EntryRow(title=_("Task description"))
        self.task_row.connect("changed", self._on_task_changed)
        self.task_row.connect("changed", self._clear_validation)
        task_group.add(self.task_row)
        page.add(task_group)

        schedule_group = Adw.PreferencesGroup(
            title=_("Schedule"),
            description=_(
                "Use YYYY-MM-DD HH:MM for one-time tasks or a five-field cron "
                "expression for recurring tasks. Times use your local time zone."
            ),
        )
        self.schedule_type_row = Adw.ComboRow(title=_("Type"))
        self.schedule_type_row.set_model(
            Gtk.StringList.new([_("One-time"), _("Recurring (Cron)")])
        )
        self.schedule_type_row.connect(
            "notify::selected", self._on_schedule_type_changed
        )
        schedule_group.add(self.schedule_type_row)

        default_run_at = datetime.datetime.now().astimezone() + datetime.timedelta(
            hours=1
        )
        default_run_at = default_run_at.replace(second=0, microsecond=0)
        self.run_at_row = Adw.EntryRow(title=_("Scheduled for"))
        self.run_at_row.set_text(default_run_at.strftime("%Y-%m-%d %H:%M"))
        self.run_at_row.connect("changed", self._clear_validation)
        schedule_group.add(self.run_at_row)

        self.cron_row = Adw.EntryRow(title=_("Cron expression"))
        self.cron_row.set_text("0 9 * * *")
        self.cron_row.set_visible(False)
        self.cron_row.connect("changed", self._clear_validation)
        schedule_group.add(self.cron_row)

        self.validation_row = Adw.ActionRow()
        self.validation_row.add_css_class("error")
        self.validation_row.set_visible(False)
        schedule_group.add(self.validation_row)
        page.add(schedule_group)

        scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scrolled_window.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scrolled_window.set_child(page)
        self.set_child(scrolled_window)

    def _on_task_changed(self, *_args):
        self.add_button.set_sensitive(bool(self.task_row.get_text().strip()))

    def _on_schedule_type_changed(self, *_args):
        recurring = self.schedule_type_row.get_selected() == 1
        self.run_at_row.set_visible(not recurring)
        self.cron_row.set_visible(recurring)
        self._clear_validation()

    def _clear_validation(self, *_args):
        self.validation_row.set_visible(False)

    def _show_validation_error(self, message):
        self.validation_row.set_title(message)
        self.validation_row.set_visible(True)

    def _add_task(self, _button):
        task = self.task_row.get_text().strip()
        recurring = self.schedule_type_row.get_selected() == 1
        run_at = None if recurring else self.run_at_row.get_text().strip()
        cron = self.cron_row.get_text().strip() if recurring else None

        try:
            if run_at is not None:
                self.controller._parse_scheduled_datetime(
                    run_at,
                    allow_past=False,
                )
            self.controller.create_scheduled_task(
                task=task,
                run_at=run_at,
                cron=cron,
            )
        except ValueError as error:
            self._show_validation_error(str(error))
            return

        self.parent_window.refresh_tasks()
        self.close()


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
        add_button = Gtk.Button(css_classes=["flat"], icon_name="list-add-symbolic")
        add_button.set_tooltip_text(_("Add scheduled task"))
        add_button.connect("clicked", self._show_add_task)
        header.pack_start(add_button)
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

    def _show_add_task(self, _button):
        dialog = AddScheduledTaskWindow(self)
        dialog.present()

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
                subtitle=_("Use the Add button or the schedule_task tool to create one."),
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
