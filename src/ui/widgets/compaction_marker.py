from gettext import gettext as _

from gi.repository import Gtk, Pango


class CompactionMarkerWidget(Gtk.Box):
    """Divider shown in chat where the model context was compacted.

    Messages above the marker are replaced, for the model, by a summary (or
    dropped when summarization is disabled). The visible transcript is kept.
    """

    def __init__(self, summary: str = ""):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=14,
            margin_bottom=14,
            margin_start=16,
            margin_end=16,
            hexpand=True,
        )
        self.add_css_class("compaction-marker")
        self.summary = ""

        divider = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        divider.append(self._make_line("start"))

        self.pill = Gtk.Button(valign=Gtk.Align.CENTER)
        self.pill.add_css_class("compaction-pill")
        pill_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill_content.append(Gtk.Image(icon_name="zoom-out-symbolic", pixel_size=14))
        pill_content.append(Gtk.Label(label=_("Context compacted")))
        self.chevron = Gtk.Image(icon_name="pan-down-symbolic", pixel_size=12)
        pill_content.append(self.chevron)
        self.pill.set_child(pill_content)
        self.pill.connect("clicked", self._on_pill_clicked)
        divider.append(self.pill)

        divider.append(self._make_line("end"))
        self.append(divider)

        self.caption = Gtk.Label(
            halign=Gtk.Align.CENTER,
            justify=Gtk.Justification.CENTER,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        self.caption.add_css_class("dim-label")
        self.caption.add_css_class("caption")
        self.append(self.caption)

        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=200,
        )
        summary_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4)
        summary_card.add_css_class("compaction-summary")
        heading = Gtk.Label(label=_("Summary sent to the model"), halign=Gtk.Align.START)
        heading.add_css_class("caption-heading")
        heading.add_css_class("dim-label")
        summary_card.append(heading)
        self.summary_label = Gtk.Label(
            halign=Gtk.Align.START,
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
        )
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=320,
        )
        scroll.set_child(self.summary_label)
        summary_card.append(scroll)
        self.revealer.set_child(summary_card)
        self.append(self.revealer)

        self.set_summary(summary)

    @staticmethod
    def _make_line(side: str) -> Gtk.Box:
        line = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER)
        line.set_size_request(-1, 1)
        line.add_css_class("compaction-line")
        line.add_css_class(side)
        return line

    def set_summary(self, summary: str):
        self.summary = summary or ""
        has_summary = bool(self.summary.strip())
        self.summary_label.set_label(self.summary.strip())
        self.chevron.set_visible(has_summary)
        # Avoid the insensitive styling: without a summary the pill is a plain label.
        self.pill.set_can_target(has_summary)
        self.pill.set_can_focus(has_summary)
        if not has_summary:
            self.revealer.set_reveal_child(False)
        if has_summary:
            self.caption.set_label(_("Earlier messages are summarized for the model"))
            self.pill.set_tooltip_text(_("Show the conversation summary"))
        else:
            self.caption.set_label(_("Earlier messages are no longer sent to the model"))
            self.pill.set_tooltip_text(None)

    def _on_pill_clicked(self, _button):
        reveal = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(reveal)
        self.chevron.set_from_icon_name("pan-up-symbolic" if reveal else "pan-down-symbolic")
        self.pill.set_tooltip_text(
            _("Hide the conversation summary") if reveal else _("Show the conversation summary")
        )
