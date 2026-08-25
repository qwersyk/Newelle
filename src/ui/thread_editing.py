import time
from gettext import gettext as _

from gi.repository import Gtk, Adw, Gio, GLib, Pango


class ThreadEditing(Gtk.Window):
    """Inspect and control terminal work started by Newelle."""

    REFRESH_INTERVAL_MS = 1000

    def __init__(self, app, *args, **kwargs):
        super().__init__(*args, **kwargs, title=_("Thread editing"))
        self.set_default_size(600, 560)
        self.set_transient_for(app.win)
        self.set_modal(False)
        self.app = app
        self._legacy_outputs = {}
        self._refresh_source_id = None

        header = Adw.HeaderBar(css_classes=["flat"])
        self.set_titlebar(header)

        button_reload = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button_reload.set_child(icon)
        button_reload.set_tooltip_text(_("Refresh"))
        button_reload.connect("clicked", lambda *_args: self.update_window())
        header.pack_end(button_reload)

        self.connect("close-request", self._on_close_request)
        self.update_window()
        self._refresh_source_id = GLib.timeout_add(
            self.REFRESH_INTERVAL_MS,
            self._poll_activities,
        )

    def _on_close_request(self, *_args):
        if self._refresh_source_id is not None:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = None
        return False

    def _poll_activities(self):
        if self.get_visible():
            self.update_window()
        return True

    def _get_default_tools_integration(self):
        try:
            return self.app.win.controller.integrationsloader.extensionsmap.get(
                "default_tools"
            )
        except AttributeError:
            return None

    def update_window(self, *_args):
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        main = Gtk.Box(
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            valign=Gtk.Align.START,
            halign=Gtk.Align.FILL,
            hexpand=True,
            orientation=Gtk.Orientation.VERTICAL,
        )

        has_activity = self._append_legacy_streams(main)
        integration = self._get_default_tools_integration()
        activities = []
        if integration is not None:
            try:
                activities = integration.get_active_command_activities()
            except Exception:
                activities = []

        if activities:
            has_activity = True
            self._append_activity_groups(main, integration, activities)

        if not has_activity:
            main.set_opacity(0.4)
            main.set_vexpand(True)
            main.set_valign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_gicon(
                Gio.ThemedIcon(name="network-offline-symbolic")
            )
            icon.set_css_classes(["empty-folder"])
            icon.set_valign(Gtk.Align.END)
            icon.set_vexpand(True)
            main.append(icon)
            main.append(
                Gtk.Label(
                    label=_("No threads are running"),
                    vexpand=True,
                    valign=Gtk.Align.START,
                    css_classes=["empty-folder", "heading"],
                )
            )

        scrolled_window.set_child(main)
        self.set_child(scrolled_window)

    def _append_section_title(self, parent, title):
        parent.append(
            Gtk.Label(
                label=title,
                halign=Gtk.Align.START,
                margin_top=8,
                margin_start=4,
                css_classes=["title-3"],
            )
        )

    def _append_legacy_streams(self, parent) -> bool:
        streams = self.app.win.streams
        if not streams:
            return False

        self._append_section_title(parent, _("Terminal threads"))
        for index, process in enumerate(streams):
            stream_menu = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                css_classes=["card"],
                margin_top=6,
                margin_start=4,
                margin_end=4,
                margin_bottom=6,
            )
            stream_menu.set_size_request(520, -1)
            row = Gtk.Box(
                margin_top=10,
                margin_start=10,
                margin_end=10,
                margin_bottom=10,
            )
            row.append(Gtk.Label(label=_("Thread number: ") + str(index + 1)))

            button = Gtk.Button(
                margin_start=5,
                margin_end=5,
                valign=Gtk.Align.CENTER,
                halign=Gtk.Align.END,
                hexpand=True,
            )
            button.connect("clicked", self.stop_flow)
            button.set_name(str(index))
            row.append(button)
            stream_menu.append(row)

            if process.poll() is None:
                button.set_child(
                    Gtk.Image.new_from_gicon(
                        Gio.ThemedIcon(name="media-playback-stop-symbolic")
                    )
                )
            else:
                button.set_sensitive(False)
                button.set_child(
                    Gtk.Image.new_from_gicon(
                        Gio.ThemedIcon(name="emblem-ok-symbolic")
                    )
                )
                code = self._get_legacy_output(index, process)
                text_expander = Gtk.Expander(
                    label=_("Console"),
                    css_classes=["toolbar", "osd"],
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=10,
                )
                text_expander.set_child(
                    Gtk.Label(
                        wrap=True,
                        wrap_mode=Pango.WrapMode.WORD_CHAR,
                        label=code,
                        selectable=True,
                    )
                )
                stream_menu.append(text_expander)

            parent.append(stream_menu)
        return True

    def _get_legacy_output(self, index, process):
        if index in self._legacy_outputs:
            return self._legacy_outputs[index]
        try:
            stdout, _stderr = process.communicate()
            output = stdout.decode(errors="replace")
        except Exception as error:
            output = str(error)
        self._legacy_outputs[index] = output
        return output

    def _append_activity_groups(self, parent, integration, activities):
        active_count = sum(
            len(activity["sessions"]) + len(activity["executions"])
            for activity in activities
        )
        activity_section = Adw.PreferencesGroup(
            title=_("Assistant command activity"),
            description=_(
                "Active terminal sessions and commands started from your chats."
            ),
        )
        activity_section.set_margin_top(12)
        activity_section.set_margin_start(4)
        activity_section.set_margin_end(4)
        activity_section.set_margin_bottom(6)

        # Keep the count in the section description without adding another
        # visually heavy heading above the native Adwaita list.
        activity_section.set_description(
            self._active_count_label(active_count, across_chats=True)
        )

        for activity in activities:
            activity_count = len(activity["sessions"]) + len(activity["executions"])
            chat_row = Adw.ExpanderRow(
                title=activity["chat_name"],
                subtitle=self._active_count_label(activity_count),
                icon_name="chat-bubbles-text-symbolic",
                expanded=True,
            )
            chat_row.add_suffix(
                Gtk.Label(
                    label=_("Running"),
                    css_classes=["success", "caption"],
                    valign=Gtk.Align.CENTER,
                )
            )

            for session in activity["sessions"]:
                chat_row.add_row(
                    self._build_activity_row(
                        title=session.command,
                        subtitle=self._activity_subtitle(
                            _("Persistent terminal"),
                            session.pid,
                            session.working_dir,
                            session.created_at,
                            identifier=session.session_id,
                        ),
                        icon_name="gnome-terminal-symbolic",
                        actions=[
                            (
                                "gnome-terminal-symbolic",
                                _("Open in Terminal"),
                                lambda _button, sid=session.session_id, cid=activity[
                                    "chat_id"
                                ]: self._open_session(integration, sid, cid),
                            ),
                            (
                                "media-playback-stop-symbolic",
                                _("Terminate"),
                                lambda _button, sid=session.session_id, cid=activity[
                                    "chat_id"
                                ]: self._terminate_session(integration, sid, cid),
                            ),
                        ],
                    )
                )

            for execution in activity["executions"]:
                chat_row.add_row(
                    self._build_activity_row(
                        title=execution.command,
                        subtitle=self._activity_subtitle(
                            _("execute_command"),
                            execution.pid,
                            execution.working_dir,
                            execution.started_at,
                            timeout=execution.timeout_seconds,
                        ),
                        icon_name="gnome-terminal-symbolic",
                        actions=[
                            (
                                "media-playback-stop-symbolic",
                                _("Cancel"),
                                lambda _button, eid=execution.execution_id, cid=activity[
                                    "chat_id"
                                ]: self._cancel_execution(integration, eid, cid),
                            ),
                        ],
                    )
                )

            activity_section.add(chat_row)

        parent.append(activity_section)

    @staticmethod
    def _active_count_label(count, across_chats=False):
        if count == 1:
            label = _("1 active item")
        else:
            label = _("{count} active items").format(count=count)
        if across_chats:
            return _("{label} across your chats").format(label=label)
        return label

    @classmethod
    def _activity_subtitle(
        cls,
        kind,
        pid,
        working_dir,
        started_at,
        identifier=None,
        timeout=None,
    ):
        details = cls._format_details(pid, working_dir, started_at, timeout=timeout)
        if identifier:
            return f"{kind} · {identifier} · {details}"
        return f"{kind} · {details}"

    @staticmethod
    def _format_details(pid, working_dir, started_at, timeout=None):
        elapsed = max(0, int(time.monotonic() - started_at))
        details = _("PID {pid} · {seconds}s · {path}").format(
            pid=pid,
            seconds=elapsed,
            path=working_dir,
        )
        if timeout is not None:
            details += " · " + _("timeout {seconds}s").format(seconds=timeout)
        return details

    def _build_activity_row(self, title, subtitle, icon_name, actions):
        row = Adw.ActionRow(
            title=title or _("Unnamed command"),
            subtitle=subtitle,
            icon_name=icon_name,
        )
        row.set_activatable(False)
        row.set_tooltip_text(title)
        for icon_name, tooltip, callback in actions:
            button = Gtk.Button(
                icon_name=icon_name,
                css_classes=["flat", "circular"],
                valign=Gtk.Align.CENTER,
                tooltip_text=tooltip,
            )
            button.connect("clicked", callback)
            row.add_suffix(button)
        return row

    def _open_session(self, integration, session_id, chat_id):
        try:
            integration.open_session_terminal(session_id, chat_id)
        except Exception:
            pass
        self.update_window()

    def _terminate_session(self, integration, session_id, chat_id):
        try:
            integration.terminate_session(session_id, chat_id)
        except Exception:
            pass
        self.update_window()

    def _cancel_execution(self, integration, execution_id, chat_id):
        try:
            integration.cancel_command_execution(execution_id, chat_id)
        except Exception:
            pass
        self.update_window()

    def stop_flow(self, widget):
        try:
            process = self.app.win.streams[int(widget.get_name())]
            if process.poll() is None:
                process.terminate()
        except (IndexError, TypeError, ValueError, OSError):
            pass
        self.update_window()
