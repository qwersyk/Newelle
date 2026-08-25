import time
from gettext import gettext as _
from typing import ClassVar

from gi.repository import Adw, GLib, Gtk

from ..utility.download_manager import (
    DownloadKind,
    DownloadStatus,
    get_download_manager,
)


class DownloadsWindow(Adw.Window):
    """Inspect active and recently finished downloads and installations."""

    REFRESH_INTERVAL_MS = 400

    _KIND_ICONS: ClassVar[dict[DownloadKind, str]] = {
        DownloadKind.DEPENDENCY: "application-x-addon-symbolic",
        DownloadKind.MODEL: "brain-augemnted-symbolic",
        DownloadKind.RUNTIME: "system-run-symbolic",
        DownloadKind.EXTENSION: "extension-symbolic",
        DownloadKind.SKILL: "skills-symbolic",
        DownloadKind.MCP: "network-server-symbolic",
    }
    _STATUS_ICONS: ClassVar[dict[DownloadStatus, str]] = {
        DownloadStatus.COMPLETED: "emblem-ok-symbolic",
        DownloadStatus.FAILED: "dialog-error-symbolic",
        DownloadStatus.CANCELLED: "process-stop-symbolic",
    }

    def __init__(self, app, *args, **kwargs):
        super().__init__(*args, **kwargs, title=_("Downloads"))
        self.app = app
        self.manager = get_download_manager()
        self.set_default_size(700, 560)
        self.set_transient_for(app.win)
        self.set_modal(False)
        self._fingerprint = None
        self._indeterminate_bars = []

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.clear_button = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            css_classes=["flat"],
            tooltip_text=_("Clear finished downloads"),
        )
        self.clear_button.connect("clicked", self._clear_finished)
        header.pack_end(self.clear_button)
        toolbar.add_top_bar(header)

        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        toolbar.set_content(self.scrolled)
        self.set_content(toolbar)

        self.connect("close-request", self._on_close_request)
        self._refresh()
        self._refresh_source_id = GLib.timeout_add(
            self.REFRESH_INTERVAL_MS, self._poll
        )

    def _on_close_request(self, *_args):
        if self._refresh_source_id is not None:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = None
        return False

    def _poll(self):
        active = tuple(self.manager.list(active=True))
        finished = tuple(self.manager.list(active=False))
        fingerprint = (active, finished)
        if fingerprint != self._fingerprint:
            self._refresh(active, finished)
        else:
            for progress in self._indeterminate_bars:
                progress.pulse()
        return True

    def _refresh(self, active=None, finished=None):
        active = tuple(active if active is not None else self.manager.list(active=True))
        finished = tuple(
            finished if finished is not None else self.manager.list(active=False)
        )
        self._fingerprint = (active, finished)
        self._indeterminate_bars = []
        self.clear_button.set_sensitive(bool(finished))

        main = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
            valign=Gtk.Align.START,
        )
        if active:
            group = Adw.PreferencesGroup(
                title=_("Active"),
                description=self._active_description(len(active)),
            )
            for task in active:
                group.add(self._build_task_row(task))
            main.append(group)

        if finished:
            group = Adw.PreferencesGroup(title=_("Recent"))
            for task in finished:
                group.add(self._build_task_row(task))
            main.append(group)

        if not active and not finished:
            empty = Adw.StatusPage(
                icon_name="folder-download-symbolic",
                title=_("No downloads yet"),
                description=_(
                    "Dependency, model, extension, skill, and runtime installs "
                    "will appear here."
                ),
                vexpand=True,
            )
            main.set_valign(Gtk.Align.FILL)
            main.set_vexpand(True)
            main.append(empty)
        self.scrolled.set_child(main)

    @staticmethod
    def _active_description(count):
        if count == 1:
            return _("1 active download or installation")
        return _("{count} active downloads or installations").format(count=count)

    def _build_task_row(self, task):
        row = Adw.ActionRow(
            title=task.title,
            subtitle=self._task_subtitle(task),
            icon_name=self._STATUS_ICONS.get(
                task.status, self._KIND_ICONS.get(task.kind, "folder-download-symbolic")
            ),
        )
        row.set_activatable(False)
        if task.error:
            row.set_tooltip_text(task.error)

        if task.status in (DownloadStatus.QUEUED, DownloadStatus.RUNNING):
            progress = Gtk.ProgressBar(
                valign=Gtk.Align.CENTER,
                hexpand=False,
                width_request=150,
            )
            if task.fraction is None:
                progress.set_pulse_step(0.08)
                progress.pulse()
                self._indeterminate_bars.append(progress)
            else:
                progress.set_fraction(task.fraction)
            row.add_suffix(progress)

        if task.cancellable:
            cancel = Gtk.Button(
                icon_name="process-stop-symbolic",
                css_classes=["flat", "circular"],
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Cancel"),
            )
            cancel.connect("clicked", self._cancel, task.task_id)
            row.add_suffix(cancel)
        return row

    def _task_subtitle(self, task):
        details = []
        if task.phase:
            details.append(_(task.phase))
        if task.transferred_bytes is not None:
            transferred = self._format_bytes(task.transferred_bytes)
            if task.total_bytes:
                details.append(
                    _("{current} of {total}").format(
                        current=transferred,
                        total=self._format_bytes(task.total_bytes),
                    )
                )
            else:
                details.append(transferred)
        if task.bytes_per_second:
            details.append(
                _("{speed}/s").format(speed=self._format_bytes(task.bytes_per_second))
            )
        started = task.started_at or task.created_at
        ended = task.finished_at or time.time()
        details.append(_("{seconds}s").format(seconds=max(0, int(ended - started))))
        if task.error:
            details.append(task.error)
        return " · ".join(details)

    @staticmethod
    def _format_bytes(value):
        size = float(value)
        for unit in (_("B"), _("KiB"), _("MiB"), _("GiB"), _("TiB")):
            if abs(size) < 1024 or unit == _("TiB"):
                return f"{size:.0f} {unit}" if unit == _("B") else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TiB"

    def _cancel(self, _button, task_id):
        self.manager.cancel(task_id)
        self._refresh()

    def _clear_finished(self, _button):
        self.manager.clear_finished()
        self._refresh()
