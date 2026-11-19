from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
import math
import re
import threading

from gi.repository import Gtk, GLib, Gdk


# MathText parsing is CPU-heavy and Matplotlib is not thread-safe. A single
# worker keeps that work away from GTK's main loop without running concurrent
# Matplotlib parsers. Queued work is cancelled when its widget is unmapped, so
# switching chats does not leave a long list of irrelevant equations behind.
_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="latex-render")
# Matplotlib and NumPy add ~100 ms to startup; load them with the first equation.
_MATHTEXT_PARSER = None
_RENDER_LOCK = threading.Lock()

# Keep rendered pixels, not GTK objects, in the cache. This lets repeated
# equations share the expensive result while all GTK object creation remains on
# the main thread. The byte limit prevents equation-heavy chats growing the
# cache without bound.
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_RENDER_CACHE = OrderedDict()
_RENDER_CACHE_BYTES = 0


@dataclass(frozen=True)
class _RenderedLatex:
    pixels: bytes
    pixel_width: int
    pixel_height: int
    width: int
    height: int

    @property
    def stride(self) -> int:
        return self.pixel_width * 4

    @property
    def byte_size(self) -> int:
        return len(self.pixels)


def _color_rgba8(color) -> tuple[int, int, int, int]:
    return tuple(
        max(0, min(255, round(component * 255)))
        for component in (color.red, color.green, color.blue, color.alpha)
    )


def _render_key(latex: str, size: int, color, scale: int = 1):
    return (latex, int(size), _color_rgba8(color), max(1, int(scale)))


def _estimate_dimensions(latex: str, size: int) -> tuple[int, int]:
    """Return a cheap placeholder size until the real render is available."""
    # Command names do not occupy their source-code width in the rendered
    # equation. Count each one as a single visible symbol instead.
    visible = re.sub(r"\\[A-Za-z]+", "x", latex)
    visible = re.sub(r"[{}_^]", "", visible)
    symbol_count = max(1, len(visible.strip()))
    width = max(size, math.ceil(symbol_count * size * 0.65))
    tall = any(token in latex for token in (r"\frac", r"\dfrac", r"\sum", r"\int", r"\prod"))
    height = math.ceil(size * (2.2 if tall else 1.6))
    return width, height


def _cache_render(key, rendered: _RenderedLatex) -> None:
    global _RENDER_CACHE_BYTES
    previous = _RENDER_CACHE.pop(key, None)
    if previous is not None:
        _RENDER_CACHE_BYTES -= previous.byte_size
    _RENDER_CACHE[key] = rendered
    _RENDER_CACHE_BYTES += rendered.byte_size
    while _RENDER_CACHE_BYTES > _MAX_CACHE_BYTES and len(_RENDER_CACHE) > 1:
        _, evicted = _RENDER_CACHE.popitem(last=False)
        _RENDER_CACHE_BYTES -= evicted.byte_size


def _render_latex(key) -> _RenderedLatex:
    """Render one equation to an RGBA buffer, suitable for a Gdk texture."""
    global _MATHTEXT_PARSER
    with _RENDER_LOCK:
        cached = _RENDER_CACHE.get(key)
        if cached is not None:
            _RENDER_CACHE.move_to_end(key)
            return cached

        import numpy as np
        from matplotlib.font_manager import FontProperties
        if _MATHTEXT_PARSER is None:
            from matplotlib.mathtext import MathTextParser
            _MATHTEXT_PARSER = MathTextParser("agg")

        latex, size, rgba, scale = key
        dpi = 100 * scale
        parsed = _MATHTEXT_PARSER.parse(
            f"${latex}$", dpi=dpi, prop=FontProperties(size=size)
        )
        alpha = np.asarray(parsed.image, dtype=np.uint8)
        pixel_height, pixel_width = alpha.shape
        if pixel_width <= 0 or pixel_height <= 0:
            raise ValueError("LaTeX render produced an empty image")

        pixels = np.empty((pixel_height, pixel_width, 4), dtype=np.uint8)
        pixels[..., 0] = rgba[0]
        pixels[..., 1] = rgba[1]
        pixels[..., 2] = rgba[2]
        if rgba[3] == 255:
            pixels[..., 3] = alpha
        else:
            pixels[..., 3] = (alpha.astype(np.uint16) * rgba[3] // 255).astype(np.uint8)

        rendered = _RenderedLatex(
            pixels=pixels.tobytes(),
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            width=max(1, math.ceil(pixel_width / scale)),
            height=max(1, math.ceil(pixel_height / scale)),
        )
        _cache_render(key, rendered)
        return rendered


def measure_latex(latex: str, size: int, color) -> tuple[int, int]:
    """Compatibility helper for callers that explicitly need exact dimensions.

    Widgets do not call this during construction: doing so was the source of the
    chat-loading stall. The render result is still cached for later widget use.
    """
    rendered = _render_latex(_render_key(latex, size, color))
    return rendered.width, rendered.height


class LatexCanvas(Gtk.Picture):
    """Lightweight picture backed by a pre-rendered Matplotlib RGBA texture."""

    def __init__(
        self,
        rendered: _RenderedLatex | str,
        size: int | None = None,
        color=None,
        inline: bool = False,
    ) -> None:
        # LatexCanvas is exported publicly. Preserve its historical
        # (latex, size, color, inline) constructor for extensions while the app
        # itself always supplies an already-rendered result from the worker.
        if isinstance(rendered, str):
            if size is None or color is None:
                raise TypeError("size and color are required when rendering LaTeX text")
            rendered = _render_latex(_render_key(rendered, size, color))
        texture = Gdk.MemoryTexture.new(
            rendered.pixel_width,
            rendered.pixel_height,
            Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(rendered.pixels),
            rendered.stride,
        )
        super().__init__(paintable=texture)
        self.dims = (rendered.width, rendered.height)
        self._measured_width = rendered.width
        self._measured_height = rendered.height + (
            0 if inline else math.ceil(rendered.height * 0.1)
        )
        # HiDPI renders contain more texture pixels than logical UI pixels.
        # Allow GtkPicture to scale the texture into the exact logical size
        # returned by do_measure(), retaining the additional device detail.
        self.set_can_shrink(True)
        self.set_keep_aspect_ratio(True)
        self.set_hexpand(not inline)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.START if inline else Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END if inline else Gtk.Align.CENTER)
        self.set_css_classes(["latex_renderer"])

    def do_measure(self, orientation, _for_size):
        size = (
            self._measured_width
            if orientation == Gtk.Orientation.HORIZONTAL
            else self._measured_height
        )
        return size, size, -1, -1


class _LazyLatexMixin:
    """Render equations once, off the GTK thread, when their tab is visible."""

    def _lazy_init(self, latex: str, size: int, inline: bool) -> None:
        self.latex = latex
        self.size = size
        self.inline = inline
        self.picture = None
        self.color = self.get_style_context().get_color()
        self.dims = _estimate_dimensions(latex, size)
        self._render_future = None
        self._build_id = None
        self._render_generation = 0
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def _on_map(self, *_):
        self._schedule_build()

    def _on_unmap(self, *_):
        self._cancel_build(clear_picture=False)

    def _schedule_build(self) -> None:
        if self._build_id is not None or self._render_future is not None:
            return
        # Let GTK finish parenting the message before reading its theme color.
        self._build_id = GLib.idle_add(self._start_build, priority=GLib.PRIORITY_LOW)

    def _cancel_build(self, clear_picture: bool = False) -> None:
        self._render_generation += 1
        if self._build_id is not None:
            GLib.source_remove(self._build_id)
            self._build_id = None
        if self._render_future is not None:
            self._render_future.cancel()
            self._render_future = None
        if clear_picture:
            self.picture = None
            self._detach_canvas()

    def _start_build(self) -> bool:
        self._build_id = None
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE

        self.color = self.get_style_context().get_color()
        scale = max(1, self.get_scale_factor())
        key = _render_key(self.latex, self.size, self.color, scale)
        self._render_generation += 1
        generation = self._render_generation
        future = _RENDER_EXECUTOR.submit(_render_latex, key)
        self._render_future = future

        def completed(done):
            try:
                rendered = done.result()
                error = None
            except CancelledError:
                return
            except Exception as exc:
                rendered = None
                error = exc
            GLib.idle_add(
                self._finish_build,
                rendered,
                error,
                generation,
                priority=GLib.PRIORITY_DEFAULT_IDLE,
            )

        future.add_done_callback(completed)
        return GLib.SOURCE_REMOVE

    def _finish_build(self, rendered, error, generation: int) -> bool:
        if generation != self._render_generation:
            return GLib.SOURCE_REMOVE
        self._render_future = None
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE

        if error is not None or rendered is None:
            self._show_fallback()
            return GLib.SOURCE_REMOVE

        canvas = LatexCanvas(rendered, inline=self.inline)
        self._detach_canvas()
        self.picture = canvas
        self.dims = canvas.dims
        self._attach_canvas(canvas)
        self._update_spacer()
        return GLib.SOURCE_REMOVE

    def _show_fallback(self) -> None:
        # Invalid or unsupported MathText should remain readable rather than
        # leaving an unexplained blank area.
        self._detach_canvas()
        fallback = Gtk.Label(label=self.latex, selectable=True, wrap=True, xalign=0)
        fallback.add_css_class("message-text")
        self.picture = fallback
        self._attach_canvas(fallback)

    def _update_spacer(self) -> None:
        placeholder = getattr(self, "placeholder", None)
        if placeholder is not None:
            placeholder.set_size_request(self.dims[0], self.dims[1])
        spacer = getattr(self, "_spacer", None)
        if spacer is not None:
            spacer.set_size_request(self.dims[0], self.dims[1] + 1)

    def rebuild_at_size(self, size: int) -> None:
        """Queue a replacement render while keeping the current image visible."""
        size = int(size)
        if size == self.size and self.picture is not None:
            return
        self.size = size
        cached_dims = _estimate_dimensions(self.latex, size)
        if self.picture is None:
            self.dims = cached_dims
            self._update_spacer()
        self._cancel_build(clear_picture=False)
        if self.get_mapped():
            self._schedule_build()

    def _attach_canvas(self, canvas) -> None:
        raise NotImplementedError

    def _detach_canvas(self) -> None:
        raise NotImplementedError


class InlineLatex(_LazyLatexMixin, Gtk.Box):
    def __init__(self, latex: str, size: int) -> None:
        super().__init__()
        self._lazy_init(latex, size, inline=True)
        self.placeholder = Gtk.Box()
        self.placeholder.set_size_request(*self.dims)
        self.append(self.placeholder)
        self._scroll = None

    def _attach_canvas(self, canvas) -> None:
        if self.placeholder.get_parent() is self:
            self.remove(self.placeholder)
        if self.dims[0] > 300:
            scroll = Gtk.ScrolledWindow(
                vscrollbar_policy=Gtk.PolicyType.NEVER,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                propagate_natural_width=True,
            )
            scroll.set_child(canvas)
            scroll.set_size_request(300, -1)
            scroll.set_hexpand(False)
            self.append(scroll)
            self._scroll = scroll
        else:
            self.append(canvas)

    def _detach_canvas(self) -> None:
        while self.get_first_child() is not None:
            self.remove(self.get_first_child())
        self.append(self.placeholder)
        self._scroll = None

    def update_zoom(self, size: int) -> None:
        self.rebuild_at_size(size)


class DisplayLatex(_LazyLatexMixin, Gtk.Box):
    def __init__(self, latex: str, base_size: int, cache_dir: str, inline: bool = False) -> None:
        super().__init__()
        # Kept for API compatibility; rendered data is held in the bounded
        # in-memory cache and never blocks on filesystem I/O.
        self.cachedir = cache_dir
        self.base_size = base_size
        self._manual_offset = 0
        self._lazy_init(latex, base_size, inline=inline)
        self.scroll = None
        if not inline:
            self.scroll = Gtk.ScrolledWindow(
                vscrollbar_policy=Gtk.PolicyType.NEVER,
                propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                propagate_natural_width=True,
            )
            self.placeholder = Gtk.Box()
            self.placeholder.set_size_request(self.dims[0], self.dims[1])
            self.scroll.set_child(self.placeholder)
            self.create_control_box()
            self.controller()
            overlay = Gtk.Overlay()
            overlay.set_child(self.scroll)
            overlay.add_overlay(self.control_box)
            self.overlay = overlay
            self.append(overlay)
        else:
            self.placeholder = Gtk.Box()
            self.placeholder.set_size_request(*self.dims)
            self.append(self.placeholder)

    def _attach_canvas(self, canvas) -> None:
        if self.scroll is not None:
            self.scroll.set_child(canvas)
        else:
            if self.placeholder.get_parent() is self:
                self.remove(self.placeholder)
            self.append(canvas)

    def _detach_canvas(self) -> None:
        if self.scroll is not None:
            self.scroll.set_child(self.placeholder)
        else:
            while self.get_first_child() is not None:
                self.remove(self.get_first_child())
            self.append(self.placeholder)

    def zoom_in(self, *_):
        self._manual_offset += 10
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def zoom_out(self, *_):
        if self.base_size + self._manual_offset <= 10:
            return
        self._manual_offset -= 10
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def update_zoom(self, zoom: int) -> None:
        self.base_size = max(10, int(16 * zoom / 100))
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def create_control_box(self):
        self.control_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.END,
            css_classes=["flat"],
            visible=False,
        )
        self.copy_button = Gtk.Button(
            halign=Gtk.Align.START,
            css_classes=["flat", "accent"],
            icon_name="edit-copy-symbolic",
            valign=Gtk.Align.START,
        )
        self.copy_button.connect("clicked", self.copy_button_clicked)

        self.zoom_out_button = Gtk.Button(
            halign=Gtk.Align.START,
            css_classes=["flat", "error"],
            icon_name="zoom-out-symbolic",
            valign=Gtk.Align.START,
        )
        self.zoom_out_button.connect("clicked", self.zoom_out)
        self.control_box.append(self.zoom_out_button)

        self.zoom_in_button = Gtk.Button(
            halign=Gtk.Align.START,
            css_classes=["flat", "success"],
            icon_name="zoom-in-symbolic",
            valign=Gtk.Align.START,
        )
        self.zoom_in_button.connect("clicked", self.zoom_in)
        self.control_box.append(self.zoom_in_button)
        self.control_box.append(self.copy_button)

    def controller(self):
        event = Gtk.EventControllerMotion.new()
        event.connect("enter", lambda *_: self.control_box.set_visible(True))
        event.connect("leave", lambda *_: self.control_box.set_visible(False))
        self.add_controller(event)

    def copy_button_clicked(self, _widget):
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.latex))
        self.copy_button.set_icon_name("object-select-symbolic")
