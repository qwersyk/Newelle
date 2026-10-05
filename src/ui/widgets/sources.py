import gettext

from gi.repository import Gtk, Pango, GdkPixbuf

from ...ui import load_image_with_callback
from ...utility.source_attribution import CitationSource
from ...utility.website_scraper import resolve_favicon_url

_ = gettext.gettext

_favicon_cache = {}
_favicon_waiters = {}


def source_icon_name(source: CitationSource) -> str:
    return {
        "web": "internet-symbolic",
        "file": "text-x-generic-symbolic",
        "user": "avatar-default-symbolic",
        "memory": "document-open-recent-symbolic",
        "tool": "applications-utilities-symbolic",
    }.get(source.kind, "dialog-information-symbolic")


def _set_favicon(image: Gtk.Image, pixbuf, size: int):
    if pixbuf is None:
        return
    scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    if scaled is not None:
        image.set_from_pixbuf(scaled)


def _load_source_favicon(image: Gtk.Image, source: CitationSource, size: int):
    if source.kind != "web" or not source.target:
        return

    favicon_url = resolve_favicon_url(source.target)
    cached = _favicon_cache.get(favicon_url)
    if cached is not None:
        _set_favicon(image, cached, size)
        return

    waiter = (image, size)
    if favicon_url in _favicon_waiters:
        _favicon_waiters[favicon_url].append(waiter)
        return
    _favicon_waiters[favicon_url] = [waiter]

    def loaded(loader):
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            _favicon_waiters.pop(favicon_url, None)
            return
        _favicon_cache[favicon_url] = pixbuf
        for waiting_image, waiting_size in _favicon_waiters.pop(favicon_url, []):
            _set_favicon(waiting_image, pixbuf, waiting_size)

    def failed(_error):
        _favicon_waiters.pop(favicon_url, None)

    load_image_with_callback(favicon_url, loaded, failed)


def create_source_icon(source: CitationSource, size: int) -> Gtk.Image:
    icon = Gtk.Image(icon_name=source_icon_name(source), pixel_size=size)
    _load_source_favicon(icon, source, size)
    return icon


class SourceChip(Gtk.Button):
    """Compact inline button representing one cited source."""

    def __init__(self, source: CitationSource, on_open):
        super().__init__(css_classes=["flat", "pill", "source-chip"], valign=Gtk.Align.BASELINE)
        self.source = source
        self.set_tooltip_text(source.label)

        content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            valign=Gtk.Align.BASELINE,
        )
        icon = create_source_icon(source, 14)
        icon.set_valign(Gtk.Align.CENTER)
        content.append(icon)
        label_width = min(max(len(source.label), 10), 20)
        content.append(Gtk.Label(
            label=source.label,
            width_chars=label_width,
            max_width_chars=20,
            ellipsize=Pango.EllipsizeMode.END,
            valign=Gtk.Align.BASELINE,
        ))
        self.set_child(content)
        self.connect("clicked", lambda _button: on_open(source))


class SourcesButton(Gtk.MenuButton):
    """Bottom-of-message button whose popover lists every cited source."""

    def __init__(self, sources: list[CitationSource], on_open):
        super().__init__(
            css_classes=["flat", "pill", "sources-button"],
            halign=Gtk.Align.START,
            margin_top=12,
        )
        self.sources = sources
        self.on_open = on_open
        self.set_always_show_arrow(False)
        self.set_tooltip_text(_("Show sources"))

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for source in sources[:3]:
            content.append(create_source_icon(source, 14))
        content.append(Gtk.Label(label=_("Sources")))
        content.append(Gtk.Image(icon_name="pan-down-symbolic", pixel_size=12))
        self.set_child(content)

        source_list = self._build_source_list()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_width(True)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_width(320)
        scroll.set_max_content_height(420)
        scroll.set_child(source_list)

        popover = Gtk.Popover()
        popover.set_child(scroll)
        self.set_popover(popover)
        self.popover = popover

    def _build_source_list(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        heading = Gtk.Label(
            label=_("Sources"),
            css_classes=["heading"],
            halign=Gtk.Align.START,
            margin_start=6,
            margin_end=6,
            margin_bottom=4,
        )
        box.append(heading)

        for source in self.sources:
            row_content = self._build_source_row(source)
            if source.target:
                row = Gtk.Button(css_classes=["flat"])
                row.set_child(row_content)
                row.connect("clicked", self._on_source_clicked, source)
            else:
                row = row_content
                row.set_margin_start(8)
                row.set_margin_end(8)
                row.set_margin_top(6)
                row.set_margin_bottom(6)
            box.append(row)
        return box

    def _build_source_row(self, source: CitationSource) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(create_source_icon(source, 18))

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(
            label=source.label,
            halign=Gtk.Align.START,
            xalign=0,
            max_width_chars=42,
            ellipsize=Pango.EllipsizeMode.END,
        )
        labels.append(title)
        if source.target:
            subtitle = Gtk.Label(
                label=source.target,
                css_classes=["dim-label", "caption"],
                halign=Gtk.Align.START,
                xalign=0,
                max_width_chars=48,
                ellipsize=Pango.EllipsizeMode.MIDDLE,
            )
            labels.append(subtitle)
        row.append(labels)
        return row

    def _on_source_clicked(self, _button, source: CitationSource):
        self.popover.popdown()
        self.on_open(source)
