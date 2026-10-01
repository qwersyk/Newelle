import math
from gettext import gettext as _
from typing import Callable

from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo

_PALETTE = (
    Adw.AccentColor.BLUE,
    Adw.AccentColor.TEAL,
    Adw.AccentColor.GREEN,
    Adw.AccentColor.YELLOW,
    Adw.AccentColor.ORANGE,
    Adw.AccentColor.RED,
    Adw.AccentColor.PINK,
    Adw.AccentColor.PURPLE,
)


def series_color(index: int | None):
    """Color of a series, starting from the system accent; ``None`` is "Other"."""
    if index is None:
        return Adw.AccentColor.to_rgba(Adw.AccentColor.SLATE)
    accent = Adw.StyleManager.get_default().get_accent_color()
    order = [accent] + [color for color in _PALETTE if color != accent]
    return Adw.AccentColor.to_rgba(order[index % len(order)])


def rgba_to_hex(rgba) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
    )


def _rounded_rect(cr, x, y, width, height, radius, top_only=False):
    radius = max(0.0, min(radius, width / 2, height if top_only else height / 2))
    cr.new_path()
    cr.move_to(x, y + radius)
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.arc(x + width - radius, y + radius, radius, 1.5 * math.pi, 2 * math.pi)
    if top_only:
        cr.line_to(x + width, y + height)
        cr.line_to(x, y + height)
    else:
        cr.arc(x + width - radius, y + height - radius, radius, 0, 0.5 * math.pi)
        cr.arc(x + radius, y + height - radius, radius, 0.5 * math.pi, math.pi)
    cr.close_path()


def _nice_scale(maximum: float, integer: bool, ticks: int = 4) -> tuple[float, float]:
    if maximum <= 0:
        return 1.0, 0.25 if not integer else 1.0
    raw = maximum / ticks
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude * 10
    for multiplier in (1, 2, 2.5, 5, 10):
        if multiplier * magnitude >= raw:
            step = multiplier * magnitude
            break
    if integer:
        step = max(1.0, math.ceil(step))
    return step * math.ceil(maximum / step - 1e-9), step


class _StyleAwareArea(Gtk.DrawingArea):
    """Drawing area that redraws when the accent color or dark mode change."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._style_handlers = []
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)

    def _on_realize(self, _widget):
        manager = Adw.StyleManager.get_default()
        for signal in ("notify::accent-color", "notify::dark", "notify::high-contrast"):
            self._style_handlers.append(manager.connect(signal, lambda *_args: self.queue_draw()))

    def _on_unrealize(self, _widget):
        manager = Adw.StyleManager.get_default()
        for handler in self._style_handlers:
            manager.disconnect(handler)
        self._style_handlers = []


class ColorDot(_StyleAwareArea):
    """Small legend dot drawn with a series color."""

    def __init__(self, index: int | None, size: int = 10):
        super().__init__(content_width=size, content_height=size, valign=Gtk.Align.CENTER)
        self.index = index
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        color = series_color(self.index)
        cr.set_source_rgba(color.red, color.green, color.blue, 1)
        cr.arc(width / 2, height / 2, min(width, height) / 2, 0, 2 * math.pi)
        cr.fill()


class DashSwatch(_StyleAwareArea):
    """Legend swatch for the dashed overall line of line charts."""

    def __init__(self, width: int = 16, height: int = 10):
        super().__init__(content_width=width, content_height=height, valign=Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45 * fg.alpha)
        cr.set_line_width(1.5)
        cr.set_dash([4, 4])
        cr.move_to(0, height / 2)
        cr.line_to(width, height / 2)
        cr.stroke()


class ShareBar(_StyleAwareArea):
    """Thin rounded bar showing a fraction with a series color."""

    def __init__(self, index: int | None, fraction: float, width: int = 72, height: int = 6):
        super().__init__(content_width=width, content_height=height, valign=Gtk.Align.CENTER)
        self.index = index
        self.fraction = max(0.0, min(1.0, fraction))
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.1)
        _rounded_rect(cr, 0, 0, width, height, height / 2)
        cr.fill()
        if self.fraction <= 0:
            return
        color = series_color(self.index)
        cr.set_source_rgba(color.red, color.green, color.blue, 1)
        _rounded_rect(cr, 0, 0, max(height, width * self.fraction), height, height / 2)
        cr.fill()


class UsageChart(_StyleAwareArea):
    """Time chart styled after libadwaita.

    Series are dicts with ``label``, ``values`` (one per bucket) and
    ``color_index`` (``None`` for the neutral "Other" series). In "bar" mode
    series are stacked; in "line" mode each one is a line, values may be
    ``None`` for gaps, and an optional ``overall`` series is drawn dashed.
    """

    TICKS = 4
    MAX_BAR_WIDTH = 36
    BAR_RADIUS = 4
    LINE_WIDTH = 2.25

    def __init__(self):
        super().__init__(hexpand=True, content_height=240, has_tooltip=True)
        self.add_css_class("usage-chart")
        self._count = 0
        self._series = []
        self._overall = None
        self._mode = "bar"
        self._fixed_max = None
        self._axis_label: Callable[[int], str] = str
        self._tooltip_title: Callable[[int], str] = str
        self._value_format: Callable[[float], str] = lambda value: f"{value:g}"
        self._integer = False
        self._empty_text = ""
        self._hover = None
        self._progress = 1.0
        self._geometry = None
        self.set_draw_func(self._draw)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        self.connect("query-tooltip", self._on_query_tooltip)

        target = Adw.CallbackAnimationTarget.new(self._on_animation_value)
        self._animation = Adw.TimedAnimation.new(self, 0.0, 1.0, 450, target)
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)

    def set_data(self, count: int, series: list[dict], axis_label: Callable[[int], str],
                 tooltip_title: Callable[[int], str], value_format: Callable[[float], str],
                 integer: bool = False, empty_text: str = "", animate: bool = True,
                 mode: str = "bar", overall: list | None = None, fixed_max: float | None = None):
        self._count = count
        self._series = series
        self._overall = overall
        self._mode = mode
        self._fixed_max = fixed_max
        self._axis_label = axis_label
        self._tooltip_title = tooltip_title
        self._value_format = value_format
        self._integer = integer
        self._empty_text = empty_text
        self._hover = None
        if animate:
            self._animation.reset()
            self._animation.play()
        else:
            self._progress = 1.0
        self.queue_draw()

    def _on_animation_value(self, value):
        self._progress = value
        self.queue_draw()

    # -- Text helpers ---------------------------------------------------------

    def _layout(self, text: str):
        layout = self.create_pango_layout(text)
        description = self.get_pango_context().get_font_description()
        if description is not None:
            description = description.copy()
            size = description.get_size()
            if size > 0:
                description.set_size(int(size * 0.85))
            layout.set_font_description(description)
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attributes)
        return layout

    @staticmethod
    def _set_color(cr, rgba, alpha):
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha * rgba.alpha)

    # -- Drawing --------------------------------------------------------------

    def _totals(self) -> list[float]:
        return [
            sum(series["values"][index] or 0 for series in self._series)
            for index in range(self._count)
        ]

    def _maximum(self, totals) -> float:
        if self._mode == "bar":
            return max(totals, default=0)
        values = [value for series in self._series for value in series["values"] if value is not None]
        values += [value for value in self._overall or [] if value is not None]
        return max(values, default=None) if values else -1

    def _draw(self, _area, cr, width, height):
        fg = self.get_color()
        totals = self._totals()
        maximum = self._maximum(totals)
        # Line charts may legitimately sit at zero (e.g. a 0% hit rate), so
        # only the absence of any value counts as empty there.
        empty = self._count == 0 or (maximum <= 0 if self._mode == "bar" else maximum < 0)
        top, step = _nice_scale(self._fixed_max or max(maximum, 0), self._integer, self.TICKS)
        ticks = [step * index for index in range(round(top / step) + 1)]

        tick_layouts = [self._layout(self._value_format(value)) for value in ticks]
        label_width = max((layout.get_pixel_size()[0] for layout in tick_layouts), default=0)
        line_height = self._layout("0").get_pixel_size()[1]

        x0 = label_width + 12
        x1 = width - 2
        y0 = line_height / 2 + 2
        y1 = height - line_height - 10
        if x1 - x0 < 10 or y1 - y0 < 10:
            return
        plot_height = y1 - y0

        for value, layout in zip(ticks, tick_layouts):
            y = round(y1 - value / top * plot_height)
            self._set_color(cr, fg, 0.15 if value == 0 else 0.07)
            cr.rectangle(x0, y, x1 - x0, 1)
            cr.fill()
            text_width, text_height = layout.get_pixel_size()
            self._set_color(cr, fg, 0.55)
            cr.move_to(x0 - 8 - text_width, y - text_height / 2)
            PangoCairo.show_layout(cr, layout)

        if empty:
            self._geometry = None
            if self._empty_text:
                layout = self.create_pango_layout(self._empty_text)
                text_width, text_height = layout.get_pixel_size()
                self._set_color(cr, fg, 0.55)
                cr.move_to(x0 + (x1 - x0 - text_width) / 2, y0 + (plot_height - text_height) / 2)
                PangoCairo.show_layout(cr, layout)
            return

        slot = (x1 - x0) / self._count
        bar_width = min(slot * 0.66, self.MAX_BAR_WIDTH)
        if slot < 4:
            bar_width = max(1.0, slot - 1)
        self._geometry = (x0, x1, slot)

        if self._hover is not None:
            self._set_color(cr, fg, 0.06)
            hover_width = min(slot, bar_width + 12)
            _rounded_rect(cr, x0 + slot * self._hover + (slot - hover_width) / 2, y0 - 2,
                          hover_width, plot_height + 2, 6)
            cr.fill()

        if self._mode == "line":
            self._draw_lines(cr, fg, x0, x1, y1, slot, top, plot_height, height)
        else:
            self._draw_bars(cr, totals, x0, y1, slot, bar_width, top, plot_height)

        layouts = [self._layout(self._axis_label(index)) for index in range(self._count)]
        widest = max(layout.get_pixel_size()[0] for layout in layouts)
        every = max(1, math.ceil((widest + 16) / slot))
        # Anchor labels on the most recent bucket, which is the most relevant.
        for index in range(self._count - 1, -1, -every):
            layout = layouts[index]
            text_width, _text_height = layout.get_pixel_size()
            center = x0 + slot * (index + 0.5)
            x = min(max(center - text_width / 2, x0), width - text_width - 2)
            self._set_color(cr, fg, 0.8 if index == self._hover else 0.55)
            cr.move_to(x, y1 + 6)
            PangoCairo.show_layout(cr, layout)

    def _draw_bars(self, cr, totals, x0, y1, slot, bar_width, top, plot_height):
        colors = [series_color(series.get("color_index")) for series in self._series]
        for index, total in enumerate(totals):
            if total <= 0:
                continue
            bar_height = max(1.5, total / top * plot_height * self._progress)
            x = x0 + slot * index + (slot - bar_width) / 2
            alpha = 1.0 if self._hover is None or self._hover == index else 0.55
            cr.save()
            _rounded_rect(cr, x, y1 - bar_height, bar_width, bar_height, self.BAR_RADIUS, top_only=True)
            cr.clip()
            y = y1
            for series, color in zip(self._series, colors):
                value = series["values"][index]
                if value <= 0:
                    continue
                segment = value / total * bar_height
                self._set_color(cr, color, alpha)
                cr.rectangle(x, y - segment, bar_width, segment)
                cr.fill()
                y -= segment
            cr.restore()

    def _draw_lines(self, cr, fg, x0, x1, y1, slot, top, plot_height, height):
        def points(values):
            return [
                (index, x0 + slot * (index + 0.5), y1 - value / top * plot_height)
                for index, value in enumerate(values)
                if value is not None
            ]

        def stroke_line(line_points, rgba, alpha):
            """Stroke a line; segments bridging empty buckets are drawn faded."""
            if len(line_points) == 1:
                self._set_color(cr, rgba, alpha)
                cr.arc(line_points[0][1], line_points[0][2], self.LINE_WIDTH, 0, 2 * math.pi)
                cr.fill()
                return
            for bridged in (True, False):
                for (index_a, xa, ya), (index_b, xb, yb) in zip(line_points, line_points[1:]):
                    if (index_b - index_a > 1) == bridged:
                        cr.move_to(xa, ya)
                        cr.line_to(xb, yb)
                self._set_color(cr, rgba, alpha * (0.3 if bridged else 1.0))
                cr.stroke()

        cr.save()
        # Reveal from left to right while animating.
        cr.rectangle(0, 0, x0 + (x1 - x0 + slot) * self._progress, height)
        cr.clip()
        cr.set_line_join(1)  # round
        cr.set_line_cap(1)  # round

        if self._overall is not None and len(self._series) > 1:
            cr.set_line_width(1.5)
            cr.set_dash([4, 4])
            stroke_line(points(self._overall), fg, 0.45)
            cr.set_dash([])

        cr.set_line_width(self.LINE_WIDTH)
        for series in self._series:
            color = series_color(series.get("color_index"))
            line_points = points(series["values"])
            stroke_line(line_points, color, 1.0)
            self._set_color(cr, color, 1.0)
            if slot >= 14 or len(line_points) <= 24:
                for _index, x, y in line_points:
                    cr.arc(x, y, 2.5, 0, 2 * math.pi)
                    cr.fill()
            if self._hover is not None and series["values"][self._hover] is not None:
                x = x0 + slot * (self._hover + 0.5)
                y = y1 - series["values"][self._hover] / top * plot_height
                cr.arc(x, y, 4.5, 0, 2 * math.pi)
                cr.fill()
        cr.restore()

    # -- Interaction ----------------------------------------------------------

    def _index_at(self, x):
        if self._geometry is None:
            return None
        x0, x1, slot = self._geometry
        if x < x0 or x >= x1:
            return None
        return min(self._count - 1, int((x - x0) / slot))

    def _on_motion(self, _controller, x, _y):
        index = self._index_at(x)
        if index != self._hover:
            self._hover = index
            self.queue_draw()
            self.trigger_tooltip_query()

    def _on_leave(self, _controller):
        if self._hover is not None:
            self._hover = None
            self.queue_draw()

    def _on_query_tooltip(self, _widget, x, _y, _keyboard, tooltip):
        index = self._index_at(x)
        if index is None:
            return False
        lines = [f"<b>{GLib.markup_escape_text(self._tooltip_title(index))}</b>"]
        shown = 0
        for series in reversed(self._series):
            value = series["values"][index]
            if value is None or (self._mode == "bar" and value <= 0):
                continue
            shown += 1
            color = rgba_to_hex(series_color(series.get("color_index")))
            lines.append(
                f"<span foreground=\"{color}\">\u25cf</span> "
                f"{GLib.markup_escape_text(series['label'])}: {GLib.markup_escape_text(self._value_format(value))}"
            )
        if self._mode == "line":
            overall = self._overall[index] if self._overall is not None else None
            if overall is not None and shown > 1:
                lines.append(f"{GLib.markup_escape_text(_('Overall'))}: {GLib.markup_escape_text(self._value_format(overall))}")
            elif shown == 0:
                lines.append(GLib.markup_escape_text(_("No data")))
        else:
            total = sum(series["values"][index] for series in self._series)
            if shown > 1 or total == 0:
                lines.append(f"{GLib.markup_escape_text(_('Total'))}: {GLib.markup_escape_text(self._value_format(total))}")
        tooltip.set_markup("\n".join(lines))
        return True
