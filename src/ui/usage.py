import datetime
import json
import threading
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..constants import AVAILABLE_LLMS
from ..utility.usage_tracker import (
    OTHER_KEY,
    PRICE_FIELDS,
    aggregate,
    get_price,
    get_usage_tracker,
    range_start,
)
from .widgets.usage_chart import ColorDot, DashSwatch, ShareBar, UsageChart

RANGE_OPTIONS = (
    ("24h", _("Last 24 Hours")),
    ("7d", _("Last 7 Days")),
    ("30d", _("Last 30 Days")),
    ("90d", _("Last 90 Days")),
    ("365d", _("Last 12 Months")),
    ("all", _("All Time")),
)
INTERVAL_OPTIONS = (
    ("auto", _("Automatic")),
    ("hour", _("Hourly")),
    ("day", _("Daily")),
    ("week", _("Weekly")),
    ("month", _("Monthly")),
)
DIMENSION_OPTIONS = (
    ("model", _("Model")),
    ("provider", _("Provider")),
    ("pair", _("Model and Provider")),
    ("workspace", _("Workspace")),
)
DIMENSION_DESCRIPTIONS = {
    "model": _("Usage grouped by model"),
    "provider": _("Usage grouped by provider"),
    "pair": _("Usage grouped by model and provider"),
    "workspace": _("Usage grouped by workspace"),
}
METRIC_OPTIONS = (
    ("tokens", _("Tokens")),
    ("requests", _("Requests")),
    ("cost", _("Cost")),
    ("cache", _("Cache Hits")),
)
PRICE_LABELS = {
    "input": _("Input"),
    "output": _("Output"),
    "cache_read": _("Cache Read"),
    "cache_write": _("Cache Write"),
}


def format_tokens(value: float) -> str:
    value = int(round(value))
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def format_count(value: float) -> str:
    return f"{int(round(value)):,}"


def format_cost(value: float, currency: str) -> str:
    if value == 0:
        return f"{currency}0"
    if value < 0.0001:
        return f"<{currency}0.0001"
    if value < 0.01:
        return f"{currency}{value:.4f}".rstrip("0")
    return f"{currency}{value:,.2f}"


def format_percent(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def format_price(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


class UsagePage(Adw.PreferencesPage):
    """Usage statistics for every LLM generation, with optional cost estimation."""

    def __init__(self, controller):
        super().__init__(icon_name="chart-bars-symbolic", title=_("Usage"))
        self.controller = controller
        self.settings = controller.settings
        self.tracker = get_usage_tracker()
        self.built = False
        self._generation = 0
        self._refresh_source = None
        self._breakdown_rows = []
        self._price_rows = {}
        self._range = "30d"
        self._interval = "auto"
        self._dimension = "model"
        self._metric = "tokens"

    # -- Building -----------------------------------------------------------

    def show_page(self):
        """Build on first display, then refresh so statistics are current."""
        if self.built:
            self.refresh()
        else:
            self.ensure_built()

    def ensure_built(self):
        if self.built:
            return
        self.built = True
        self._build_summary()
        self._build_chart()
        self._build_breakdown()
        self._build_pricing()
        self._build_data()
        self.tracker.add_listener(self._on_usage_recorded)
        self.refresh()

    def _build_summary(self):
        group = Adw.PreferencesGroup()
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=3,
            column_spacing=12,
            row_spacing=12,
        )
        self._summary = {}
        for key, title in (
            ("requests", _("Requests")),
            ("input", _("Input")),
            ("output", _("Output")),
            ("cache", _("Cache Hits")),
            ("savings", _("Cache Savings")),
            ("cost", _("Cost")),
        ):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["card"])
            inner = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2,
                margin_top=12, margin_bottom=12, margin_start=14, margin_end=14,
            )
            caption = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption-heading", "dim-label"])
            value = Gtk.Label(label="0", xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["title-2", "numeric"])
            detail = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption", "dim-label"])
            inner.append(caption)
            inner.append(value)
            inner.append(detail)
            card.append(inner)
            child = Gtk.FlowBoxChild(child=card, focusable=False)
            flow.append(child)
            self._summary[key] = (value, detail)
        group.add(flow)
        self.add(group)

    def _build_chart(self):
        group = Adw.PreferencesGroup(title=_("Usage Over Time"))
        toggles = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        toggles.add_css_class("round")
        for key, label in METRIC_OPTIONS:
            toggle = Adw.Toggle(name=key, label=label)
            toggles.add(toggle)
        toggles.set_active_name(self._metric)
        toggles.connect("notify::active-name", self._on_metric_changed)
        group.set_header_suffix(toggles)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["card"])
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=14, margin_start=14, margin_end=18,
        )
        self.chart = UsageChart()
        inner.append(self.chart)
        self.legend = Adw.WrapBox(child_spacing=16, line_spacing=6, margin_start=4)
        inner.append(self.legend)
        card.append(inner)
        box.append(card)

        controls = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.range_row = self._combo_row(_("Time Range"), RANGE_OPTIONS, self._range, "_range")
        self.interval_row = self._combo_row(_("Interval"), INTERVAL_OPTIONS, self._interval, "_interval")
        self.dimension_row = self._combo_row(_("Group By"), DIMENSION_OPTIONS, self._dimension, "_dimension")
        for row in (self.range_row, self.interval_row, self.dimension_row):
            controls.append(row)
        box.append(controls)
        group.add(box)
        self.add(group)

    def _combo_row(self, title, options, selected, attribute):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _key, label in options]))
        row.set_selected([key for key, _label in options].index(selected))

        def on_selected(combo, _param):
            index = combo.get_selected()
            if 0 <= index < len(options):
                setattr(self, attribute, options[index][0])
                self.refresh()
        row.connect("notify::selected", on_selected)
        return row

    def _build_breakdown(self):
        self.breakdown_group = Adw.PreferencesGroup(title=_("Breakdown"))
        self.add(self.breakdown_group)

    def _build_pricing(self):
        self.pricing_group = Adw.PreferencesGroup(
            title=_("Pricing"),
            description=_("Prices per million tokens, used to estimate costs. Empty cache prices use the input price."),
        )
        currency = Adw.EntryRow(title=_("Currency Symbol"), text=self.settings.get_string("usage-currency"))
        currency.connect("changed", self._on_currency_changed)
        self.pricing_group.add(currency)
        self.add(self.pricing_group)
        pairs = []
        for handler in (getattr(self.controller.handlers, "llm", None), getattr(self.controller.handlers, "secondary_llm", None)):
            if handler is None:
                continue
            try:
                pairs.append((handler.key, str(handler.get_selected_model() or "")))
            except Exception:
                pass
        try:
            pairs.extend(self.tracker.known_models())
        except Exception as error:
            print(f"Error loading usage models: {error}")
        for provider, model in pairs:
            self._ensure_price_row(provider, model)

    def _build_data(self):
        group = Adw.PreferencesGroup(title=_("Data"))
        tracking = Adw.SwitchRow(
            title=_("Track Usage"),
            subtitle=_("Record token usage of every request. Data is stored only on this device."),
        )
        self.settings.bind("usage-tracking", tracking, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(tracking)
        clear_row = Adw.ActionRow(title=_("Clear Usage History"), subtitle=_("Delete all recorded usage. Prices are kept."))
        clear_button = Gtk.Button(label=_("Clear"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        clear_button.connect("clicked", self._on_clear_clicked)
        clear_row.add_suffix(clear_button)
        group.add(clear_row)
        self.add(group)

    # -- Labels -------------------------------------------------------------

    @staticmethod
    def _provider_title(provider: str) -> str:
        descriptor = AVAILABLE_LLMS.get(provider)
        return descriptor["title"] if descriptor else (provider or _("Unknown Provider"))

    def _group_label(self, group: dict, dimension: str) -> str:
        if group["key"] == OTHER_KEY:
            return _("Other")
        model = group["model"] or _("Unknown Model")
        if dimension == "model":
            return model
        if dimension == "provider":
            return self._provider_title(group["provider"])
        if dimension == "workspace":
            workspace = getattr(self.controller, "workspaces", {}).get(group["key"])
            if workspace:
                return workspace.get("name") or group["key"]
            return group["workspace_name"] or group["key"] or _("Unknown Workspace")
        return f"{model} · {self._provider_title(group['provider'])}"

    @staticmethod
    def _axis_label(moment: datetime.datetime, interval: str) -> str:
        if interval == "hour":
            return moment.strftime("%H:%M")
        if interval == "month":
            return moment.strftime("%b %Y") if moment.month == 1 else moment.strftime("%b")
        return moment.strftime("%d %b")

    @staticmethod
    def _tooltip_title(moment: datetime.datetime, interval: str) -> str:
        if interval == "hour":
            return moment.strftime("%a %d %b, %H:%M")
        if interval == "day":
            return moment.strftime("%A %d %B %Y")
        if interval == "week":
            return _("Week of {}").format(moment.strftime("%d %B %Y"))
        return moment.strftime("%B %Y")

    # -- Data ---------------------------------------------------------------

    def _load_prices(self) -> dict:
        try:
            prices = json.loads(self.settings.get_string("usage-prices"))
        except (json.JSONDecodeError, TypeError):
            return {}
        return prices if isinstance(prices, dict) else {}

    def _currency(self) -> str:
        return self.settings.get_string("usage-currency")

    def refresh(self):
        if not self.built:
            return
        self._generation += 1
        generation = self._generation
        options = (self._range, self._interval, self._dimension, self._metric, self._load_prices())

        def worker():
            data = None
            try:
                now = datetime.datetime.now()
                start = range_start(options[0], now)
                rows = self.tracker.query(start.timestamp() if start is not None else None)
                data = aggregate(rows, *options, now=now)
            except Exception as error:
                print(f"Error loading usage statistics: {error}")
            GLib.idle_add(self._apply, generation, data)

        threading.Thread(target=worker, daemon=True).start()

    def schedule_refresh(self, delay: int = 400):
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)

        def run():
            self._refresh_source = None
            self.refresh()
            return False
        self._refresh_source = GLib.timeout_add(delay, run)

    def _on_usage_recorded(self):
        if self.built and self.get_mapped():
            self.schedule_refresh(1000)

    def _apply(self, generation, data):
        if generation != self._generation or data is None:
            return False
        self._apply_summary(data["totals"])
        self._apply_chart(data)
        self._apply_breakdown(data)
        for group in data["groups"]:
            self._ensure_price_row(group["provider"], group["model"])
        return False

    def _value_formatter(self, metric: str):
        if metric == "cost":
            currency = self._currency()
            return lambda value: format_cost(value, currency)
        if metric == "requests":
            return format_count
        if metric == "cache":
            return format_percent
        return format_tokens

    def _apply_summary(self, totals):
        currency = self._currency()
        requests_value, requests_detail = self._summary["requests"]
        requests_value.set_label(format_count(totals["requests"]))
        requests_detail.set_label(_("Partly estimated") if totals["estimated"] else "")
        requests_detail.set_tooltip_text(
            _("Some providers do not report usage, so their token counts are estimated.") if totals["estimated"] else None
        )

        input_value, input_detail = self._summary["input"]
        input_value.set_label(format_tokens(totals["input_tokens"]))
        cached = totals["cache_read_tokens"] + totals["cache_write_tokens"]
        input_detail.set_label(_("{} cached").format(format_tokens(cached)) if cached else "")

        output_value, output_detail = self._summary["output"]
        output_value.set_label(format_tokens(totals["output_tokens"]))
        reasoning = totals["reasoning_tokens"]
        output_detail.set_label(_("{} reasoning").format(format_tokens(reasoning)) if reasoning else "")

        cache_value, cache_detail = self._summary["cache"]
        rate = totals["cache_hit_rate"]
        cache_value.set_label(format_percent(rate) if rate is not None else "—")
        cache_detail.set_label(
            _("{} from cache").format(format_tokens(totals["cache_read_tokens"])) if rate is not None else ""
        )
        cache_value.set_tooltip_text(
            _("Share of input tokens read from the provider's prompt cache. Providers that do not report usage are excluded.")
        )

        savings_value, savings_detail = self._summary["savings"]
        savings = totals["cache_savings"]
        savings_detail.set_tooltip_text(None)
        if not cached:
            savings_value.set_label("—")
            savings_detail.set_label(_("No cached tokens"))
        elif not totals["cache_savings_priced"]:
            savings_value.set_label("—")
            savings_detail.set_label(_("Set cache prices below"))
        else:
            savings_value.set_label(format_cost(savings, currency) if savings >= 0 else "-" + format_cost(-savings, currency))
            uncached_cost = totals["cost"] + savings
            if savings > 0 and uncached_cost > 0:
                savings_detail.set_label(_("{} less than uncached").format(format_percent(savings / uncached_cost * 100)))
            elif savings < 0:
                savings_detail.set_label(_("Cache writes cost more"))
            else:
                savings_detail.set_label("")
        savings_value.set_tooltip_text(
            _("Money saved by reading input from the prompt cache instead of paying the full input price, minus any extra cost of cache writes.")
        )

        cost_value, cost_detail = self._summary["cost"]
        cost_value.set_label(format_cost(totals["cost"], currency) if totals["priced"] else "—")
        if totals["unpriced"]:
            cost_detail.set_label(_("{} without pricing").format(totals["unpriced"]))
        elif not totals["priced"]:
            cost_detail.set_label(_("Set prices below"))
        else:
            cost_detail.set_label("")

    def _apply_chart(self, data):
        buckets = data["buckets"]
        interval = data["interval"]
        series = []
        color_index = 0
        for group in data["series"]:
            index = None
            if group["key"] != OTHER_KEY:
                index = color_index
                color_index += 1
            group["color_index"] = index
            series.append({
                "label": self._group_label(group, data["dimension"]),
                "values": group["values"],
                "color_index": index,
            })

        if self._interval == "auto" or self._interval != interval:
            self.interval_row.set_subtitle(dict(INTERVAL_OPTIONS)[interval])
        else:
            self.interval_row.set_subtitle("")

        metric = data["metric"]
        totals = data["totals"]
        if metric == "cost" and totals["requests"] and not totals["priced"]:
            empty_text = _("Set prices below to see costs")
        elif metric == "cache" and totals["requests"]:
            empty_text = _("No provider reported cache usage in this period")
        else:
            empty_text = _("No usage in this period")
        cache = metric == "cache"
        self.chart.set_data(
            len(buckets),
            series,
            lambda index: self._axis_label(buckets[index], interval),
            lambda index: self._tooltip_title(buckets[index], interval),
            self._value_formatter(metric),
            integer=metric in ("tokens", "requests"),
            empty_text=empty_text,
            mode="line" if cache else "bar",
            overall=data["overall"],
            fixed_max=100 if cache else None,
        )

        child = self.legend.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.legend.remove(child)
            child = next_child
        for item in series:
            entry = Gtk.Box(spacing=6)
            entry.append(ColorDot(item["color_index"]))
            entry.append(Gtk.Label(label=item["label"], css_classes=["caption"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=32))
            self.legend.append(entry)
        if cache and len(series) > 1:
            entry = Gtk.Box(spacing=6)
            entry.append(DashSwatch())
            entry.append(Gtk.Label(label=_("Overall"), css_classes=["caption"]))
            self.legend.append(entry)
        self.legend.set_visible(bool(series))

    def _apply_breakdown(self, data):
        for row in self._breakdown_rows:
            self.breakdown_group.remove(row)
        self._breakdown_rows = []
        dimension = data["dimension"]
        metric = data["metric"]
        self.breakdown_group.set_description(DIMENSION_DESCRIPTIONS[dimension])
        groups = data["groups"]
        if not groups:
            row = Adw.ActionRow(title=_("No usage recorded in this period"), css_classes=["dim-label"])
            self.breakdown_group.add(row)
            self._breakdown_rows.append(row)
            return

        currency = self._currency()
        formatter = self._value_formatter(metric)
        color_indexes = {id(group): group.get("color_index") for group in data["series"]}

        def metric_value(group):
            if metric == "requests":
                return group["requests"]
            if metric == "cost":
                return group["cost"]
            if metric == "cache":
                return group["cache_hit_rate"]
            return group["input_tokens"] + group["output_tokens"]

        total = sum(metric_value(group) or 0 for group in groups) or 1
        for group in groups:
            details = [
                _("{} requests").format(format_count(group["requests"])),
                _("{} input").format(format_tokens(group["input_tokens"])),
                _("{} output").format(format_tokens(group["output_tokens"])),
            ]
            cached = group["cache_read_tokens"] + group["cache_write_tokens"]
            if cached:
                details.append(_("{} cached").format(format_tokens(cached)))
            if group["cache_hit_rate"]:
                details.append(_("{} cache hits").format(format_percent(group["cache_hit_rate"])))
            if group["priced"]:
                cost = format_cost(group["cost"], currency)
                details.append(cost if not group["unpriced"] else _("{} (partial)").format(cost))
            row = Adw.ActionRow(
                title=self._group_label(group, dimension),
                subtitle=" · ".join(details),
                use_markup=False,
                subtitle_lines=2,
            )
            if group["estimated"]:
                row.set_tooltip_text(_("Token counts are partly estimated because the provider did not report usage."))
            # Groups folded into "Other" in the chart use its neutral color.
            index = color_indexes.get(id(group))
            row.add_prefix(ColorDot(index))
            suffix = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
            value = metric_value(group)
            if value is None or (metric == "cost" and not group["priced"]):
                value_text = "—"
            else:
                value_text = formatter(value)
            suffix.append(Gtk.Label(label=value_text, xalign=1, css_classes=["numeric", "heading"]))
            # A hit rate is already a fraction, not a share of the total.
            fraction = (value or 0) / 100 if metric == "cache" else value / total
            suffix.append(ShareBar(index, fraction))
            row.add_suffix(suffix)
            self.breakdown_group.add(row)
            self._breakdown_rows.append(row)

    # -- Pricing ------------------------------------------------------------

    def _ensure_price_row(self, provider: str, model: str):
        if not provider or (provider, model) in self._price_rows:
            return
        row = Adw.ExpanderRow(
            title=model or _("Unknown Model"),
            subtitle=self._provider_title(provider),
            use_markup=False,
        )
        summary = Gtk.Label(css_classes=["dim-label", "numeric"], valign=Gtk.Align.CENTER)
        row.add_suffix(summary)
        price = self._load_prices().get(provider, {})
        price = price.get(model, {}) if isinstance(price, dict) else {}
        for field in PRICE_FIELDS:
            value = price.get(field) if isinstance(price, dict) else None
            entry = Adw.EntryRow(
                title=PRICE_LABELS[field],
                text=format_price(value) if value is not None else "",
                input_purpose=Gtk.InputPurpose.NUMBER,
            )
            entry.connect("changed", self._on_price_changed, provider, model, field)
            row.add_row(entry)
        self._price_rows[(provider, model)] = (row, summary)
        self._update_price_summary(provider, model)
        self.pricing_group.add(row)

    def _update_price_summary(self, provider: str, model: str):
        _row, summary = self._price_rows[(provider, model)]
        price = get_price(self._load_prices(), provider, model)
        if price is None:
            summary.set_label(_("Not set"))
            return
        currency = self._currency()
        summary.set_label(
            f"{currency}{format_price(price.get('input') or 0)} / {currency}{format_price(price.get('output') or 0)}"
        )
        summary.set_tooltip_text(_("Input / output price per million tokens"))

    def _on_price_changed(self, entry, provider, model, field):
        text = entry.get_text().strip().replace(",", ".")
        value = None
        if text:
            try:
                value = float(text)
            except ValueError:
                value = -1
            if value < 0:
                entry.add_css_class("error")
                return
        entry.remove_css_class("error")
        prices = self._load_prices()
        provider_prices = prices.setdefault(provider, {})
        if not isinstance(provider_prices, dict):
            provider_prices = prices[provider] = {}
        model_prices = provider_prices.setdefault(model, {})
        if value is None:
            model_prices.pop(field, None)
        else:
            model_prices[field] = value
        if not model_prices:
            provider_prices.pop(model, None)
        if not provider_prices:
            prices.pop(provider, None)
        self.settings.set_string("usage-prices", json.dumps(prices))
        self._update_price_summary(provider, model)
        self.schedule_refresh()

    def _on_currency_changed(self, entry):
        self.settings.set_string("usage-currency", entry.get_text().strip())
        for provider, model in self._price_rows:
            self._update_price_summary(provider, model)
        self.schedule_refresh()

    # -- Actions ------------------------------------------------------------

    def _on_metric_changed(self, toggles, _param):
        name = toggles.get_active_name()
        if name:
            self._metric = name
            self.refresh()

    def _on_clear_clicked(self, _button):
        dialog = Adw.AlertDialog(
            heading=_("Clear Usage History?"),
            body=_("All recorded usage statistics will be permanently deleted."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "clear":
                return

            def worker():
                try:
                    self.tracker.clear()
                except Exception as error:
                    print(f"Error clearing usage history: {error}")
                GLib.idle_add(lambda: self.refresh() or False)
            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())
