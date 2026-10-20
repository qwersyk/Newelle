import json
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ...constants import AVAILABLE_LLMS
from ...utility.strings import count_tokens
from ...utility.usage_tracker import (
    compute_cache_savings,
    cost_breakdown,
    get_price,
    split_cached_prefix,
)
from ..usage import (
    format_count,
    format_percent,
    format_price,
    format_tokens,
)
from .usage_chart import ColorDot, SegmentBar, series_color

# Section texts beyond this size make the dialog slow to open; the full
# prompt can still be copied.
MAX_SECTION_CHARS = 20_000

KIND_LABELS = {
    "system": _("System prompt"),
    "history": _("History"),
    "context": _("Message context"),
    "retrieved": _("Retrieved context"),
    "message": _("Message"),
}
ROLE_LABELS = {
    "User": _("User messages"),
    "Assistant": _("Assistant messages"),
    "Console": _("Tool results"),
}
EXTRA_INPUT_FIELDS = (
    ("input_audio_tokens", _("Input Audio Tokens")),
    ("tool_input_tokens", _("Tool Input Tokens")),
)
EXTRA_OUTPUT_FIELDS = (
    ("reasoning_tokens", _("Reasoning Tokens")),
    ("output_audio_tokens", _("Output Audio Tokens")),
    ("accepted_prediction_tokens", _("Accepted Prediction Tokens")),
    ("rejected_prediction_tokens", _("Rejected Prediction Tokens")),
)


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def format_cost(value: float, currency: str) -> str:
    """Format the cost of a single message, which is often a fraction of a cent."""
    if value == 0:
        return f"{currency}0"
    if value >= 1:
        return f"{currency}{value:,.2f}"
    if value < 0.000001:
        return f"<{currency}0.000001"
    return f"{currency}{value:.6f}".rstrip("0").rstrip(".")


def _cache_colors():
    return (
        Adw.AccentColor.to_rgba(Adw.AccentColor.GREEN),
        Adw.AccentColor.to_rgba(Adw.AccentColor.YELLOW),
        Adw.AccentColor.to_rgba(Adw.AccentColor.BLUE),
    )


def _value_label(text: str, heading: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=1, valign=Gtk.Align.CENTER, css_classes=["numeric"])
    if heading:
        label.add_css_class("heading")
    return label


def _text_row(text: str) -> Adw.PreferencesRow:
    if len(text) > MAX_SECTION_CHARS:
        text = text[:MAX_SECTION_CHARS] + "\n\n" + _("… (truncated, copy the full prompt to see everything)")
    row = Adw.PreferencesRow(activatable=False)
    row.set_child(Gtk.Label(
        label=text,
        wrap=True,
        wrap_mode=Pango.WrapMode.WORD_CHAR,
        selectable=True,
        xalign=0,
        halign=Gtk.Align.FILL,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
        css_classes=["monospace", "caption"],
    ))
    return row


def _lazy_text(row: Adw.ExpanderRow, text: str):
    """Build the text row of an expander only once it is first expanded."""
    def on_expanded(expander, _pspec):
        if expander.get_expanded() and not getattr(expander, "_text_built", False):
            expander._text_built = True
            expander.add_row(_text_row(text))
    row.connect("notify::expanded", on_expanded)


class PromptDetailsDialog(Adw.Dialog):
    """Token, cache and cost breakdown of the request that produced a message."""

    def __init__(self, entry: dict, settings, on_open_usage=None):
        super().__init__(title=_("Prompt Details"), content_width=580, content_height=720)
        self.entry = entry
        self.on_open_usage = on_open_usage
        self.usage = entry.get("LLMUsage") or {}
        self.prompt_text = entry.get("Prompt", "") or ""
        self.currency = settings.get_string("usage-currency")
        self.read_color, self.write_color, self.miss_color = _cache_colors()

        usage = self.usage
        self.input_reported = usage.get("input_tokens") is not None
        self.output_reported = usage.get("output_tokens") is not None
        self.input_tokens = _int(usage["input_tokens"] if self.input_reported else entry.get("InputTokens"))
        self.output_tokens = _int(usage["output_tokens"] if self.output_reported else entry.get("OutputTokens"))
        self.cache_known = self.input_reported and (
            usage.get("cache_read_tokens") is not None or usage.get("cache_write_tokens") is not None
        )
        self.cache_read = min(self.input_tokens, _int(usage.get("cache_read_tokens")))
        self.cache_write = min(self.input_tokens - self.cache_read, _int(usage.get("cache_write_tokens")))
        self.uncached = self.input_tokens - self.cache_read - self.cache_write

        self.provider = entry.get("LLMProvider") or ""
        self.model = entry.get("LLMModel") or ""
        try:
            prices = json.loads(settings.get_string("usage-prices"))
        except (json.JSONDecodeError, TypeError):
            prices = {}
        self.price = get_price(prices if isinstance(prices, dict) else {}, self.provider, self.model) \
            if self.provider else None
        self.usage_row = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
        }
        self.costs = cost_breakdown(self.usage_row, self.price)
        self.total_cost = sum(part["cost"] for part in self.costs.values()) if self.costs else None

        self.sections = self._build_sections()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow(vexpand=True, propagate_natural_height=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=640, margin_top=18, margin_bottom=24, margin_start=12, margin_end=12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        clamp.set_child(content)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        self.set_child(toolbar)

        content.append(self._build_overview())
        content.append(self._build_input_group())
        if self.sections:
            content.append(self._build_sections_group())
        content.append(self._build_cost_group())
        content.append(self._build_output_group())
        performance = self._build_performance_group()
        if performance is not None:
            content.append(performance)
        if self.prompt_text:
            content.append(self._build_prompt_group())

    # -- Data ---------------------------------------------------------------

    def _build_sections(self) -> list[dict]:
        recorded = self.entry.get("PromptSections")
        self.legacy = not recorded
        if recorded:
            sections = [dict(section) for section in recorded]
        elif self.prompt_text:
            sections = [{
                "kind": "system",
                "title": _("System prompt"),
                "tokens": count_tokens(self.prompt_text),
                "length": len(self.prompt_text),
            }]
        else:
            sections = []
        self._attach_texts(sections)

        leftover = self.input_tokens - sum(section["tokens"] for section in sections)
        if leftover > 0 and sections:
            sections.append({
                "kind": "other",
                "title": _("Conversation and message") if self.legacy else _("Not attributed"),
                "subtitle": (
                    _("Not recorded separately for this message")
                    if self.legacy
                    else _("Message formatting, tool schemas and attachments counted by the provider")
                ),
                "tokens": leftover,
            })

        if self.cache_known:
            # Recorded sections cover the whole prompt, so the unattributed
            # remainder has no position in it. Old messages only know the
            # system prompt, which is sent before everything else.
            walk = [section for section in sections if self.legacy or section["kind"] != "other"]
            splits = split_cached_prefix(
                [section["tokens"] for section in walk], self.input_tokens, self.cache_read, self.cache_write,
            )
            for section, split in zip(walk, splits):
                section["cache"] = split
        return sections

    def _attach_texts(self, sections: list[dict]):
        """Slice each system section out of the prompt, which joins them with newlines."""
        system = [section for section in sections if section["kind"] == "system"]
        lengths = [section.get("length") for section in system]
        if not system or any(not isinstance(length, int) for length in lengths):
            return
        if sum(lengths) + len(lengths) - 1 != len(self.prompt_text):
            return
        offset = 0
        for section, length in zip(system, lengths):
            section["text"] = self.prompt_text[offset:offset + length]
            offset += length + 1

    def _segments(self, tokens: int, cache) -> list:
        if cache is None:
            return [(tokens, series_color(0))]
        read, written, missed = cache
        return [(read, self.read_color), (written, self.write_color), (missed, self.miss_color)]

    @staticmethod
    def _cache_status(tokens: int, cache) -> str:
        read, written, _missed = cache
        if tokens <= 0:
            return ""
        if read >= tokens:
            return _("Cached")
        if read > 0:
            return _("{percent} cached").format(percent=format_percent(read / tokens * 100))
        if written > 0:
            return _("Written to cache")
        return _("Not cached")

    def _cache_tooltip(self, cache) -> str:
        read, written, missed = cache
        parts = [_("{count} read from cache").format(count=format_count(read))]
        if written:
            parts.append(_("{count} written to cache").format(count=format_count(written)))
        parts.append(_("{count} not cached").format(count=format_count(missed)))
        return "\n".join(parts)

    def _provider_title(self) -> str:
        descriptor = AVAILABLE_LLMS.get(self.provider)
        return descriptor["title"] if descriptor else self.provider

    # -- Overview -----------------------------------------------------------

    def _tile(self, value: str, caption: str, tooltip: str | None = None) -> Gtk.Box:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["card"])
        value_label = Gtk.Label(
            label=value, css_classes=["title-3", "numeric"],
            margin_top=12, margin_start=6, margin_end=6, ellipsize=Pango.EllipsizeMode.END,
        )
        tile.append(value_label)
        tile.append(Gtk.Label(label=caption, css_classes=["caption", "dim-label"], margin_bottom=12))
        if tooltip:
            tile.set_tooltip_text(tooltip)
        return tile

    def _build_overview(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        if self.model or self.provider:
            label = " · ".join(part for part in (self.model, self._provider_title()) if part)
            box.append(Gtk.Label(
                label=label, css_classes=["dim-label"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                tooltip_text=label,
            ))
        tiles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True)
        approx_in = "" if self.input_reported else "~"
        approx_out = "" if self.output_reported else "~"
        tiles.append(self._tile(
            approx_in + format_tokens(self.input_tokens), _("Input"),
            _("{count} input tokens").format(count=format_count(self.input_tokens)),
        ))
        tiles.append(self._tile(
            approx_out + format_tokens(self.output_tokens), _("Output"),
            _("{count} output tokens").format(count=format_count(self.output_tokens)),
        ))
        if self.cache_known and self.input_tokens > 0:
            cached = format_percent(self.cache_read / self.input_tokens * 100)
            tiles.append(self._tile(
                cached, _("Cached"),
                _("{count} input tokens read from cache").format(count=format_count(self.cache_read)),
            ))
        else:
            tiles.append(self._tile("—", _("Cached"), _("The provider did not report cache usage")))
        if self.total_cost is not None:
            tiles.append(self._tile(format_cost(self.total_cost, self.currency), _("Cost")))
        else:
            tiles.append(self._tile("—", _("Cost"), _("No price set for this model")))
        box.append(tiles)
        return box

    # -- Input --------------------------------------------------------------

    def _build_input_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=_("Input Tokens"),
            description=(
                _("Reported by the provider")
                if self.input_reported
                else _("Estimated locally, the provider did not report usage")
            ),
        )
        if self.cache_known:
            bar_row = Adw.PreferencesRow(activatable=False)
            bar = SegmentBar(
                [(self.cache_read, self.read_color), (self.cache_write, self.write_color),
                 (self.uncached, self.miss_color)],
                width=200, height=12, hexpand=True,
            )
            bar.set_margin_top(14)
            bar.set_margin_bottom(14)
            bar.set_margin_start(12)
            bar.set_margin_end(12)
            bar_row.set_child(bar)
            group.add(bar_row)
            rows = [
                (_("Read from cache"), _("Reused from an earlier request at the cache read price"),
                 self.cache_read, self.read_color),
            ]
            if self.cache_write or self.usage.get("cache_write_tokens") is not None:
                rows.append((_("Written to cache"), _("Stored for later requests at the cache write price"),
                             self.cache_write, self.write_color))
            rows.append((_("Not cached"), _("Processed in full at the input price"), self.uncached, self.miss_color))
            for title, subtitle, tokens, color in rows:
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                row.add_prefix(ColorDot(None, rgba=color))
                share = tokens / self.input_tokens * 100 if self.input_tokens else 0
                row.add_suffix(_value_label(f"{format_count(tokens)}  ·  {format_percent(share)}"))
                group.add(row)
        else:
            row = Adw.ActionRow(title=_("Total Input"))
            if self.input_reported:
                row.set_subtitle(_("The provider did not report cache usage"))
            row.add_suffix(_value_label(format_count(self.input_tokens)))
            group.add(row)
        for key, title in EXTRA_INPUT_FIELDS:
            if self.usage.get(key) is not None:
                row = Adw.ActionRow(title=title)
                row.add_suffix(_value_label(format_count(_int(self.usage[key]))))
                group.add(row)
        return group

    # -- Sections -----------------------------------------------------------

    def _build_sections_group(self) -> Adw.PreferencesGroup:
        description = _("Sizes are estimated locally and listed in the order they are sent.")
        if self.cache_known:
            description += " " + _(
                "Providers cache the beginning of the prompt, so the cache status of each section "
                "is inferred from that order."
            )
        if self.legacy:
            description += " " + _("This message was generated before sections were recorded.")
        group = Adw.PreferencesGroup(title=_("Prompt Sections"), description=description)
        if self.cache_known:
            legend = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
            for title, color in ((_("Cached"), self.read_color), (_("Written"), self.write_color),
                                 (_("Not cached"), self.miss_color)):
                item = Gtk.Box(spacing=6)
                item.append(ColorDot(None, size=8, rgba=color))
                item.append(Gtk.Label(label=title, css_classes=["caption", "dim-label"]))
                legend.append(item)
            group.set_header_suffix(legend)

        total = max(1, sum(section["tokens"] for section in self.sections))
        for section in self.sections:
            group.add(self._section_row(section, total))
        return group

    def _section_row(self, section: dict, total: int) -> Adw.PreferencesRow:
        kind = section.get("kind", "")
        tokens = _int(section.get("tokens"))
        cache = section.get("cache")
        parts = []
        if section.get("subtitle"):
            parts.append(section["subtitle"])
        elif kind in KIND_LABELS:
            parts.append(KIND_LABELS[kind])
        if kind == "history" and section.get("messages"):
            count = section["messages"]
            parts.append(ngettext("{count} message", "{count} messages", count).format(count=count))
        parts.append(format_percent(tokens / total * 100))
        if cache is not None:
            status = self._cache_status(tokens, cache)
            if status:
                parts.append(status)
        subtitle = "  ·  ".join(parts)

        text = section.get("text")
        roles = section.get("roles") or {}
        expandable = bool(text) or (kind == "history" and roles)
        title = section.get("title") or KIND_LABELS.get(kind, "")
        if expandable:
            row = Adw.ExpanderRow(title=title, subtitle=subtitle, use_markup=False)
        else:
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)

        suffix = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        label = _value_label(format_count(tokens))
        label.set_width_chars(7)
        suffix.append(label)
        if kind == "other" and cache is None:
            bar = SegmentBar([(tokens, Adw.AccentColor.to_rgba(Adw.AccentColor.SLATE))], total=total, width=80)
        else:
            bar = SegmentBar(self._segments(tokens, cache), total=total, width=80)
        if cache is not None:
            bar.set_tooltip_text(self._cache_tooltip(cache))
        suffix.append(bar)
        row.add_suffix(suffix)

        if text:
            _lazy_text(row, text)
        elif expandable:
            history_tokens = max(1, sum(_int(value) for value in roles.values()))
            for role, role_tokens in sorted(roles.items(), key=lambda item: -_int(item[1])):
                child = Adw.ActionRow(
                    title=ROLE_LABELS.get(role, role or _("Other")),
                    subtitle=format_percent(_int(role_tokens) / history_tokens * 100),
                    use_markup=False,
                )
                child.add_suffix(_value_label(format_count(_int(role_tokens))))
                row.add_row(child)
        return row

    # -- Cost ---------------------------------------------------------------

    def _build_cost_group(self) -> Adw.PreferencesGroup:
        if self.costs is None:
            group = Adw.PreferencesGroup(title=_("Cost"))
            if self.provider:
                row = Adw.ActionRow(
                    title=_("No price set for this model"),
                    subtitle=_("Set the prices of {model} in the Usage settings to see the cost of each message").format(
                        model=self.model or self._provider_title()
                    ),
                    use_markup=False,
                )
                if self.on_open_usage is not None:
                    button = Gtk.Button(label=_("Set Prices"), valign=Gtk.Align.CENTER)
                    button.connect("clicked", self._on_open_usage)
                    row.add_suffix(button)
            else:
                row = Adw.ActionRow(
                    title=_("Cost unavailable"),
                    subtitle=_("The model used for this message was not recorded"),
                )
            group.add(row)
            return group

        description = _("Estimated from the prices per million tokens set in the Usage settings")
        if not (self.input_reported and self.output_reported):
            description += " " + _("and locally estimated token counts")
        group = Adw.PreferencesGroup(title=_("Cost"), description=description)
        currency = self.currency
        for field, title in (
            ("input", _("Input (not cached)")),
            ("cache_read", _("Cache Reads")),
            ("cache_write", _("Cache Writes")),
            ("output", _("Output")),
        ):
            part = self.costs[field]
            if field.startswith("cache") and part["tokens"] == 0:
                continue
            row = Adw.ActionRow(
                title=title,
                subtitle=_("{tokens} tokens × {price} per million").format(
                    tokens=format_count(part["tokens"]), price=currency + format_price(part["price"]),
                ),
            )
            row.add_suffix(_value_label(format_cost(part["cost"], currency)))
            group.add(row)

        savings = compute_cache_savings(self.usage_row, self.price)
        if savings:
            row = Adw.ActionRow(
                title=_("Saved by Caching") if savings > 0 else _("Extra Cost of Cache Writes"),
                subtitle=_("Compared with paying the input price for every input token"),
            )
            row.add_suffix(_value_label(format_cost(abs(savings), currency)))
            group.add(row)

        total_row = Adw.ActionRow(title=_("Total"))
        total_row.add_suffix(_value_label(format_cost(self.total_cost, currency), heading=True))
        group.add(total_row)
        return group

    def _on_open_usage(self, _button):
        self.close()
        self.on_open_usage()

    # -- Output and performance ---------------------------------------------

    def _build_output_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Output Tokens"))
        row = Adw.ActionRow(
            title=_("Output Tokens"),
            subtitle=_("Reported by the provider") if self.output_reported else _("Estimated locally"),
        )
        row.add_suffix(_value_label(format_count(self.output_tokens)))
        group.add(row)
        for key, title in EXTRA_OUTPUT_FIELDS:
            if self.usage.get(key) is not None:
                row = Adw.ActionRow(title=title)
                row.add_suffix(_value_label(format_count(_int(self.usage[key]))))
                group.add(row)
        total = self.usage.get("total_tokens")
        if total is not None:
            row = Adw.ActionRow(title=_("Total Tokens"), subtitle=_("Input and output"))
            row.add_suffix(_value_label(format_count(_int(total))))
            group.add(row)
        return group

    def _build_performance_group(self) -> Adw.PreferencesGroup | None:
        elapsed = self.entry.get("enlapsed") or 0.0
        rows = []
        if elapsed > 0:
            rows.append((_("Generation Time"), f"{elapsed:.2f} s"))
        for key, title in (
            ("TimeToFirstToken", _("Time to First Token")),
            ("TimeToFirstTokenNoThinking", _("Time to First Token (without thinking)")),
        ):
            value = self.entry.get(key)
            if value is not None:
                rows.append((title, f"{value:.2f} s"))
        if elapsed > 0:
            rows.append((_("Generation Speed"), f"{self.output_tokens / elapsed:.2f} tokens/s"))
        if not rows:
            return None
        group = Adw.PreferencesGroup(title=_("Performance"))
        for title, value in rows:
            row = Adw.ActionRow(title=title)
            row.add_suffix(_value_label(value))
            group.add(row)
        return group

    # -- Prompt -------------------------------------------------------------

    def _build_prompt_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("System Prompt"))
        copy_button = Gtk.Button(
            icon_name="edit-copy-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
            tooltip_text=_("Copy"),
        )
        copy_button.connect("clicked", self._on_copy_prompt)
        group.set_header_suffix(copy_button)
        row = Adw.ExpanderRow(
            title=_("Full Prompt"),
            subtitle=ngettext("{count} character", "{count} characters", len(self.prompt_text)).format(
                count=format_count(len(self.prompt_text))
            ),
        )
        _lazy_text(row, self.prompt_text)
        group.add(row)
        return group

    def _on_copy_prompt(self, button):
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(self.prompt_text))
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))
