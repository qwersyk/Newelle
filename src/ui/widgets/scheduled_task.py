from gi.repository import Gtk, Adw, GLib


class ScheduledTaskWidget(Gtk.ListBox):
    """Widget displayed in chat showing a scheduled task's details."""

    def __init__(
        self,
        task: str,
        schedule_type: str,
        run_at: str | None = None,
        cron: str | None = None,
        next_run_at: str | None = None,
        task_id: str = "",
    ):
        super().__init__()
        self.add_css_class("boxed-list")
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)

        self._task = task
        self._schedule_type = schedule_type
        self._run_at = run_at
        self._cron = cron
        self._next_run_at = next_run_at
        self._task_id = task_id

        # Main row with expander for details
        self.expander_row = Adw.ExpanderRow(
            title=self._get_title(),
            subtitle=self._get_subtitle(),
            icon_name="alarm-symbolic",
        )

        # Status badge
        self._status_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
        )

        # Status label
        self._status_label = Gtk.Label(
            label=_("Scheduled"),
            css_classes=["accent"],
        )
        self._status_box.append(self._status_label)

        self.expander_row.add_suffix(self._status_box)

        # Details content
        self._details_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        # Task description card
        task_group = Adw.PreferencesGroup(title=_("Task"))
        task_row = Adw.ActionRow(
            title=self._truncate_text(task, 100),
            subtitle=_("The agent will execute this task when scheduled"),
        )
        task_group.add(task_row)
        self._details_box.append(task_group)

        # Schedule details group
        schedule_group = Adw.PreferencesGroup(title=_("Schedule Details"))

        # Schedule type
        type_row = Adw.ActionRow(
            title=_("Type"),
            subtitle=_("One-time") if schedule_type == "once" else _("Recurring (Cron)"),
        )
        type_row.set_icon_name("appointment-new-symbolic")
        schedule_group.add(type_row)

        # Specific schedule info
        if schedule_type == "once" and run_at:
            time_row = Adw.ActionRow(
                title=_("Scheduled for"),
                subtitle=self._format_datetime(run_at),
            )
            time_row.set_icon_name("appointment-symbolic")
            schedule_group.add(time_row)
        elif schedule_type == "cron" and cron:
            cron_row = Adw.ActionRow(
                title=_("Cron expression"),
                subtitle=cron,
            )
            cron_row.set_icon_name("question-round-outline-symbolic")
            schedule_group.add(cron_row)

        # Next run
        if next_run_at:
            next_row = Adw.ActionRow(
                title=_("Next run"),
                subtitle=self._format_datetime(next_run_at),
            )
            next_row.set_icon_name("alarm-symbolic")
            schedule_group.add(next_row)

        # Task ID
        id_row = Adw.ActionRow(
            title=_("Task ID"),
            subtitle=task_id[:8] if task_id else _("Unknown"),
        )
        id_row.set_icon_name("user-bookmarks-symbolic")
        schedule_group.add(id_row)

        self._details_box.append(schedule_group)

        # Add to expander
        self.expander_row.add_row(self._details_box)
        self.append(self.expander_row)

    @staticmethod
    def _truncate_text(text: str, max_len: int = 60) -> str:
        text = text.replace("\n", " ").strip()
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text

    def _get_title(self) -> str:
        if self._schedule_type == "once":
            return _("Scheduled Task (One-time)")
        return _("Scheduled Task (Recurring)")

    def _get_subtitle(self) -> str:
        if self._next_run_at:
            return _("Next run: {0}").format(self._format_datetime(self._next_run_at))
        elif self._run_at:
            return _("Scheduled for: {0}").format(self._format_datetime(self._run_at))
        elif self._cron:
            return _("Cron: {0}").format(self._cron)
        return _("Task scheduled")

    def _format_datetime(self, value: str) -> str:
        """Format ISO datetime to human-readable format."""
        if not value:
            return _("Unknown")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(value)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(value)

    def update_status(self, status: str, status_type: str = "info"):
        """Update the status label."""
        GLib.idle_add(self._ui_update_status, status, status_type)

    def _ui_update_status(self, status: str, status_type: str):
        if not self.get_display():
            return GLib.SOURCE_REMOVE

        self._status_label.set_text(status)

        # Update CSS class based on status type
        css_classes = self._status_label.get_css_classes()
        for cls in ["accent", "success", "warning", "error"]:
            if cls in css_classes:
                self._status_label.remove_css_class(cls)

        if status_type == "success":
            self._status_label.add_css_class("success")
        elif status_type == "warning":
            self._status_label.add_css_class("warning")
        elif status_type == "error":
            self._status_label.add_css_class("error")
        else:
            self._status_label.add_css_class("accent")

        return GLib.SOURCE_REMOVE
