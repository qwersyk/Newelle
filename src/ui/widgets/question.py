import threading
from gettext import gettext as _

from gi.repository import Gtk, Adw, GLib, Pango, GObject


class QuestionWidget(Gtk.Box):
    """Widget that displays a question with selectable options for the user."""

    __gsignals__ = {
        "answered": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, question: str, options: list[str], mode: str = "open", multiple: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)

        self._answered = False
        self._answer_value = None
        self._event = threading.Event()
        self._options = options
        self._multiple = multiple
        self._selected_options = set()
        self._interactive_widgets = []

        if mode in ("choice", "choice_with_custom") and not options:
            mode = "open"
        self._mode = mode

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .question-header {
                padding: 12px 16px;
                background: alpha(@accent_bg_color, 0.08);
            }
            .question-body {
                padding: 12px 16px;
            }
            .question-option {
                padding: 8px 16px;
                border-top: 1px solid alpha(@borders, 0.3);
            }
            .question-answer {
                padding: 10px 16px;
                border-top: 1px solid alpha(@borders, 0.3);
                background: alpha(@accent_bg_color, 0.06);
            }
        """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("question-header")

        icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icon.set_pixel_size(18)
        icon.add_css_class("accent")
        header.append(icon)

        title_label = Gtk.Label(label=_("Question"), xalign=0, hexpand=True)
        title_label.add_css_class("heading")
        header.append(title_label)

        self._status_label = Gtk.Label(label=_("Waiting…"))
        self._status_label.add_css_class("caption")
        self._status_label.add_css_class("dim-label")
        header.append(self._status_label)

        self.append(header)

        # Body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("question-body")
        question_label = Gtk.Label(
            label=question,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            xalign=0,
            hexpand=True,
        )
        body.append(question_label)
        self.append(body)

        # Options
        if options and mode in ("choice", "choice_with_custom"):
            radio_group = None
            for option in options:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.add_css_class("question-option")

                if multiple:
                    check = Gtk.CheckButton(label=option)
                    check.connect("toggled", self._on_option_toggled, option)
                else:
                    check = Gtk.CheckButton(label=option)
                    if radio_group is not None:
                        check.set_group(radio_group)
                    else:
                        radio_group = check
                    check.connect("toggled", self._on_radio_toggled, option)

                row.append(check)
                self._interactive_widgets.append(check)
                self.append(row)

        # Entry
        if mode in ("open", "choice_with_custom"):
            entry_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                margin_top=8,
                margin_bottom=8,
                margin_start=16,
                margin_end=16,
            )
            self.entry = Gtk.Entry(
                placeholder_text=_("Type your answer…"),
                hexpand=True,
            )
            self.entry.connect("activate", self._on_entry_activate)
            entry_row.append(self.entry)
            self._interactive_widgets.append(self.entry)

            send_btn = Gtk.Button(label=_("Send"), css_classes=["suggested-action"])
            send_btn.connect("clicked", self._on_entry_submit)
            entry_row.append(send_btn)
            self._interactive_widgets.append(send_btn)
            self._send_btn = send_btn

            self.append(entry_row)

        # Confirm for multi-select
        if multiple and mode in ("choice", "choice_with_custom"):
            confirm_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                margin_top=4,
                margin_bottom=8,
                margin_start=16,
                margin_end=16,
            )
            confirm_btn = Gtk.Button(label=_("Confirm"), css_classes=["suggested-action"])
            confirm_btn.connect("clicked", self._on_confirm_selection)
            confirm_row.append(confirm_btn)
            self._interactive_widgets.append(confirm_btn)
            self._confirm_btn = confirm_btn
            self.append(confirm_row)

    def _on_radio_toggled(self, check_btn, option):
        if check_btn.get_active() and not self._answered:
            self._set_answer(option)

    def _on_option_toggled(self, check_btn, option):
        if check_btn.get_active():
            self._selected_options.add(option)
        else:
            self._selected_options.discard(option)

    def _on_entry_activate(self, entry):
        text = entry.get_text().strip()
        if not text or self._answered:
            return
        self._set_answer(text)

    def _on_entry_submit(self, button):
        text = self.entry.get_text().strip()
        if not text or self._answered:
            return
        self._set_answer(text)

    def _on_confirm_selection(self, button):
        if self._answered:
            return
        parts = list(self._selected_options)
        if hasattr(self, "entry"):
            custom = self.entry.get_text().strip()
            if custom:
                parts.append(custom)
        if not parts:
            return
        self._set_answer(", ".join(parts))

    def _set_answer(self, answer: str):
        self._answered = True
        self._answer_value = answer

        for w in self._interactive_widgets:
            w.set_sensitive(False)

        self._status_label.set_label(_("Answered"))

        answer_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        answer_row.add_css_class("question-answer")
        answer_label = Gtk.Label(
            label=answer,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            xalign=0,
            hexpand=True,
            css_classes=["heading"],
        )
        answer_row.append(answer_label)
        self.append(answer_row)

        self.emit("answered", answer)
        self._event.set()

    def wait_for_answer(self, timeout=None) -> str | None:
        self._event.wait(timeout=timeout)
        return self._answer_value

    def get_answer(self) -> str | None:
        return self._answer_value

    def is_answered(self) -> bool:
        return self._answered


class RestoredQuestionWidget(Gtk.Box):
    """Non-interactive version of QuestionWidget used when restoring from history."""

    def __init__(self, question: str, options: list[str], answer: str, mode: str = "open", multiple: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)

        if mode in ("choice", "choice_with_custom") and not options:
            mode = "open"

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .question-header {
                padding: 12px 16px;
                background: alpha(@accent_bg_color, 0.08);
            }
            .question-body {
                padding: 12px 16px;
            }
            .question-option {
                padding: 8px 16px;
                border-top: 1px solid alpha(@borders, 0.3);
            }
            .question-option-selected {
                background: alpha(@accent_bg_color, 0.06);
            }
            .question-answer {
                padding: 10px 16px;
                border-top: 1px solid alpha(@borders, 0.3);
                background: alpha(@accent_bg_color, 0.06);
            }
        """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("question-header")

        icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icon.set_pixel_size(18)
        icon.add_css_class("accent")
        header.append(icon)

        title_label = Gtk.Label(label=_("Question"), xalign=0, hexpand=True)
        title_label.add_css_class("heading")
        header.append(title_label)

        status_label = Gtk.Label(label=_("Answered"))
        status_label.add_css_class("caption")
        status_label.add_css_class("dim-label")
        header.append(status_label)

        self.append(header)

        # Body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("question-body")
        question_label = Gtk.Label(
            label=question,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            xalign=0,
            hexpand=True,
        )
        body.append(question_label)
        self.append(body)

        # Options with selections
        if options and mode in ("choice", "choice_with_custom"):
            options_set = set(options)
            answer_parts = [p.strip() for p in answer.split(",")] if answer else []
            selected = set()
            for part in answer_parts:
                if part in options_set:
                    selected.add(part)

            radio_group = None
            for option in options:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.add_css_class("question-option")
                if option in selected:
                    row.add_css_class("question-option-selected")

                if multiple:
                    check = Gtk.CheckButton(label=option)
                    check.set_active(option in selected)
                else:
                    check = Gtk.CheckButton(label=option)
                    check.set_active(option in selected)
                    if radio_group is not None:
                        check.set_group(radio_group)
                    else:
                        radio_group = check

                check.set_sensitive(False)
                row.append(check)
                self.append(row)

        # Answer
        if answer:
            answer_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            answer_row.add_css_class("question-answer")
            answer_label = Gtk.Label(
                label=answer,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                xalign=0,
                hexpand=True,
                css_classes=["heading"],
            )
            answer_row.append(answer_label)
            self.append(answer_row)
