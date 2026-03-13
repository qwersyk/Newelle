import threading
from gi.repository import Gtk, GObject, GLib, Pango


class FilePermissionConfirmWidget(Gtk.Box):
    """Confirmation widget for file operations requiring user approval.

    Mimics the CopyBox execution_request UI pattern: header with Skip/Accept
    buttons, path display, spinner while running, and completed/skipped states.

    Signals:
        accepted: Emitted after the run_callback finishes successfully.
        rejected: Emitted when the user clicks Skip.
        command-complete: Emitted with the output string after the operation finishes.
    """

    __gsignals__ = {
        'accepted': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'rejected': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'command-complete': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, file_path: str, operation: str, run_callback=None):
        """
        Args:
            file_path: The absolute path being accessed.
            operation: ``"read"`` or ``"write"``.
            run_callback: Called (on a background thread) when the user accepts.
                          Signature: ``callback() -> None``.
        """
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            css_classes=["osd", "toolbar", "code"],
        )
        self.file_path = file_path
        self.operation = operation
        self.run_callback = run_callback
        self.has_responded = False

        # --- Header row ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        icon_name = "document-open-symbolic" if operation == "read" else "document-save-symbolic"
        op_icon = Gtk.Image.new_from_icon_name(icon_name)
        op_icon.set_pixel_size(16)
        op_icon.add_css_class("dim-label")
        header_box.append(op_icon)

        op_label = _("Read") if operation == "read" else _("Write")
        title_label = Gtk.Label(
            label=_("File {op} Request").format(op=op_label),
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
        )
        header_box.append(title_label)

        # Skip button
        self.skip_button = Gtk.Button(label=_("Skip"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        self.skip_button.connect("clicked", self._on_skip_clicked)
        header_box.append(self.skip_button)

        # Accept button (primary action, mirrors CopyBox Run button)
        self.accept_button = Gtk.Button(css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        accept_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        accept_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        accept_content.append(accept_icon)
        accept_content.append(Gtk.Label(label=_("Accept")))
        self.accept_button.set_child(accept_content)
        self.accept_button.connect("clicked", self._on_accept_clicked)
        header_box.append(self.accept_button)

        self.append(header_box)

        # --- Path display ---
        path_label = Gtk.Label(
            label=file_path,
            halign=Gtk.Align.START,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            css_classes=["monospace"],
            margin_start=6,
            margin_end=6,
        )
        self.append(path_label)

        # --- Output expander (hidden until operation completes) ---
        self.output_expander = Gtk.Expander(
            label=_("Output"),
            css_classes=["toolbar", "osd"],
        )
        self.output_expander.set_expanded(False)
        self.output_expander.set_visible(False)
        self.output_label = Gtk.Label(
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            halign=Gtk.Align.START,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        self.output_expander.set_child(self.output_label)
        self.append(self.output_expander)

        # --- Status label (hidden initially) ---
        self.status_label = Gtk.Label(
            halign=Gtk.Align.START,
            visible=False,
            css_classes=["dim-label"],
        )
        self.append(self.status_label)

    # --- Accept flow ---

    def _on_accept_clicked(self, _button):
        if self.has_responded:
            return
        self.has_responded = True

        self.accept_button.set_sensitive(False)
        self.skip_button.set_sensitive(False)

        # Spinner while the operation runs
        spinner = Gtk.Spinner(spinning=True)
        running_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        running_box.append(spinner)
        running_box.append(Gtk.Label(label=_("Running...")))
        self.accept_button.set_child(running_box)

        if self.run_callback is not None:
            threading.Thread(target=self._run_and_finish, daemon=True).start()
        else:
            self._show_completed(None)

    def _run_and_finish(self):
        try:
            self.run_callback()
            GLib.idle_add(self._show_completed, None)
        except Exception as exc:
            GLib.idle_add(self._show_completed, str(exc))

    def _show_completed(self, error: str | None):
        check_icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic")
        done_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        done_box.append(check_icon)
        done_box.append(Gtk.Label(label=_("Completed")))
        self.accept_button.set_child(done_box)
        self.accept_button.remove_css_class("suggested-action")
        self.accept_button.add_css_class("success")

        if error:
            self.output_label.set_text(error)
            self.output_expander.set_visible(True)
            self.output_expander.set_expanded(True)

        self.status_label.set_visible(False)
        self.emit("accepted")

    # --- Skip flow ---

    def _on_skip_clicked(self, _button):
        if self.has_responded:
            return
        self.has_responded = True

        self.accept_button.set_sensitive(False)
        self.skip_button.set_sensitive(False)

        skip_icon = Gtk.Image.new_from_icon_name("action-unavailable-symbolic")
        skip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        skip_box.append(skip_icon)
        skip_box.append(Gtk.Label(label=_("Skipped")))
        self.skip_button.set_child(skip_box)
        self.skip_button.add_css_class("dim-label")

        self.status_label.set_text(_("Operation was skipped by user"))
        self.status_label.add_css_class("dim-label")
        self.status_label.set_visible(True)

        self.emit("rejected")

    # --- External completion API ---

    def complete(self, output: str | None):
        """Mark the widget as completed externally (for restore)."""
        if output is None:
            # Treat as skipped
            self.has_responded = True
            self.accept_button.set_sensitive(False)
            self.skip_button.set_sensitive(False)
            skip_icon = Gtk.Image.new_from_icon_name("action-unavailable-symbolic")
            skip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            skip_box.append(skip_icon)
            skip_box.append(Gtk.Label(label=_("Skipped")))
            self.skip_button.set_child(skip_box)
        else:
            self.has_responded = True
            self.accept_button.set_sensitive(False)
            self.skip_button.set_sensitive(False)
            self._show_completed(None)
