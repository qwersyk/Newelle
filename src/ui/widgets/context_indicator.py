import math
import gettext
from gi.repository import Gtk, GLib

_ = gettext.gettext


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


class ContextIndicator(Gtk.MenuButton):
    """Header-bar button that shows a pie chart of context usage and a stats popover."""

    RING_SIZE = 20
    STROKE_WIDTH = 2.0

    def __init__(self):
        super().__init__(css_classes=["flat"], tooltip_text=_("Context usage"))

        self._original = 0
        self._trimmed = 0
        self._suggested = 0
        self._max = 0

        self._ring = Gtk.DrawingArea()
        self._ring.set_content_width(self.RING_SIZE)
        self._ring.set_content_height(self.RING_SIZE)
        self._ring.set_draw_func(self._draw_ring)
        self.set_child(self._ring)

        # Popover with stats rows
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)

        self._row_full = self._make_stat_row(_("Full chat tokens"), "0")
        self._row_reduced = self._make_stat_row(_("Reduced context tokens"), "0")
        self._row_suggested = self._make_stat_row(_("Suggested limit"), "0")
        self._row_max = self._make_stat_row(_("Max limit"), "0")

        box.append(self._row_full["box"])
        box.append(Gtk.Separator())
        box.append(self._row_reduced["box"])
        box.append(Gtk.Separator())
        box.append(self._row_suggested["box"])
        box.append(self._row_max["box"])

        popover.set_child(box)
        self.set_popover(popover)

    @staticmethod
    def _make_stat_row(label_text: str, value_text: str) -> dict:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=label_text, xalign=0, hexpand=True)
        label.add_css_class("dim-label")
        value = Gtk.Label(label=value_text, xalign=1)
        value.add_css_class("heading")
        box.append(label)
        box.append(value)
        return {"box": box, "value": value}

    def update_stats(self, trim_result) -> None:
        """Update the indicator from a TrimResult (may be called from any thread)."""
        self._original = trim_result.original_tokens
        self._trimmed = trim_result.trimmed_tokens
        self._suggested = trim_result.suggested_tokens
        self._max = trim_result.max_tokens

        def _apply():
            self._row_full["value"].set_label(_format_tokens(self._original))
            self._row_reduced["value"].set_label(_format_tokens(self._trimmed))
            self._row_suggested["value"].set_label(_format_tokens(self._suggested))
            self._row_max["value"].set_label(_format_tokens(self._max))
            self._ring.queue_draw()
        GLib.idle_add(_apply)

    def update_from_chat(self, controller) -> None:
        """Estimate context stats from the current chat without running the full trim pipeline."""
        from ...utility.strings import count_tokens
        from ...utility.context_manager import TrimResult

        settings = controller.newelle_settings
        if settings.context_mode != "context-manager":
            self._original = 0
            self._trimmed = 0
            self._suggested = 0
            self._max = 0
            self._update_labels()
            return

        chat = controller.chat
        history = controller.get_history(chat=chat)
        total = sum(count_tokens(m.get("Message", "")) + 4 for m in history)

        self.update_stats(TrimResult(
            original_tokens=total,
            trimmed_tokens=min(total, settings.context_suggested),
            suggested_tokens=settings.context_suggested,
            max_tokens=settings.context_max,
        ))

    def _update_labels(self):
        self._row_full["value"].set_label(_format_tokens(self._original))
        self._row_reduced["value"].set_label(_format_tokens(self._trimmed))
        self._row_suggested["value"].set_label(_format_tokens(self._suggested))
        self._row_max["value"].set_label(_format_tokens(self._max))
        self._ring.queue_draw()

    # --- Cairo ring drawing --------------------------------------------------

    def _draw_ring(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = (min(width, height) - self.STROKE_WIDTH) / 2
        fraction = min(self._trimmed / self._max, 1.0) if self._max > 0 else 0.0

        cr.set_line_width(self.STROKE_WIDTH)
        cr.set_line_cap(1)  # round caps

        # Track ring
        style = area.get_style_context()
        found, color = style.lookup_color("window_fg_color")
        if found:
            cr.set_source_rgba(color.red, color.green, color.blue, 0.15)
        else:
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.15)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        # Filled arc — color depends on usage level
        if fraction > 0:
            suggested_frac = self._suggested / self._max if self._max > 0 else 0.5
            if fraction <= suggested_frac:
                r, g, b = 0.30, 0.76, 0.48
            elif fraction <= 1.0:
                r, g, b = 0.96, 0.76, 0.07
            else:
                r, g, b = 0.90, 0.29, 0.24

            cr.set_source_rgba(r, g, b, 0.9)
            start = -math.pi / 2
            end = start + 2 * math.pi * fraction
            cr.arc(cx, cy, radius, start, end)
            cr.stroke()
