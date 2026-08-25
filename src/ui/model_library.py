from __future__ import annotations

import builtins
import gettext
import inspect
import threading
from dataclasses import dataclass

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..utility.download_manager import (
    DownloadCancelled,
    DownloadKind,
    get_download_manager,
)

_ = getattr(builtins, "_", gettext.gettext)


@dataclass
class LibraryModel:
    id: str
    name: str
    description: str
    tags: list[str]
    is_installed: bool = False
    is_pinned: bool = False
    icon_name: str | None = None
    icon_color: str | None = None


class ModelLibraryWindow(Adw.Window):
    """Browse and manage the models exposed by a Newelle handler."""

    BATCH_SIZE = 50
    MAX_VISIBLE_TAGS = 10
    REFRESH_INTERVAL_MS = 500

    def __init__(self, handler, parent_window=None, **kwargs):
        super().__init__(**kwargs)
        self.handler = handler
        self.closed = False
        self.refreshing = False
        self.custom_model_pending = False
        self.filter_mode = "all"
        self.all_models = []
        self.filtered_models = []
        self.all_model_keys = []
        self.filtered_keys = []
        self.cards = {}
        self.loaded_count = 0
        self.batch_size = self.BATCH_SIZE
        self.pending_downloads = set()
        self.pending_operations = {}
        self._download_source_id = None

        self.set_title(_("Model Library"))
        self.set_default_size(960, 680)
        self.set_modal(True)
        if parent_window is not None:
            self.set_transient_for(parent_window)

        self.load_css()
        self._build_ui()
        self.connect("close-request", self._on_close_request)

        self.load_models()
        self._download_source_id = GLib.timeout_add(
            self.REFRESH_INTERVAL_MS,
            self.update_downloads,
        )

    def _build_ui(self):
        self.toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(
                title=_("Model Library"),
                subtitle=_("Discover and manage local AI models"),
            )
        )
        toolbar.add_top_bar(header)

        self.refresh_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=150,
        )
        self.refresh_stack.add_named(
            Gtk.Image(icon_name="view-refresh-symbolic"),
            "icon",
        )
        self.refresh_spinner = Gtk.Spinner()
        self.refresh_stack.add_named(self.refresh_spinner, "spinner")
        self.refresh_stack.set_visible_child_name("icon")
        self.refresh_button = Gtk.Button(
            child=self.refresh_stack,
            css_classes=["flat"],
            tooltip_text=_("Refresh model library"),
        )
        self.refresh_button.connect("clicked", self.refresh_library)
        header.pack_end(self.refresh_button)

        self.add_button = None
        if callable(getattr(self.handler, "pull_model", None)):
            self.add_button = Gtk.Button(
                icon_name="list-add-symbolic",
                css_classes=["flat"],
                tooltip_text=_("Add a custom model"),
            )
            self.add_button.connect("clicked", self.show_add_custom_model_dialog)
            header.pack_end(self.add_button)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar.set_content(page)

        controls = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=14,
            margin_bottom=14,
            margin_start=18,
            margin_end=18,
        )
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search by name, description, or tag"),
            hexpand=True,
        )
        self.search_entry.connect("search-changed", self.on_search_changed)
        controls.append(self.search_entry)

        filter_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        filter_box.add_css_class("linked")
        self.filter_buttons = {}
        first_button = None
        for mode, label in (
            ("all", _("All")),
            ("installed", _("Installed")),
            ("available", _("Available")),
        ):
            button = Gtk.ToggleButton(label=label)
            if first_button is None:
                first_button = button
                button.set_active(True)
            else:
                button.set_group(first_button)
            button.connect("toggled", self._on_filter_toggled, mode)
            filter_box.append(button)
            self.filter_buttons[mode] = button
        filter_row.append(filter_box)

        self.result_count_label = Gtk.Label(
            xalign=1,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self.result_count_label.add_css_class("caption")
        self.result_count_label.add_css_class("dim-label")
        filter_row.append(self.result_count_label)
        controls.append(filter_row)

        controls_clamp = Adw.Clamp(
            maximum_size=1120,
            tightening_threshold=760,
        )
        controls_clamp.set_child(controls)
        page.append(controls_clamp)
        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.results_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=150,
            vhomogeneous=False,
            vexpand=True,
        )
        page.append(self.results_stack)

        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.scrolled.get_vadjustment().connect("value-changed", self.on_scroll)
        self.flowbox = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=1,
            max_children_per_line=3,
            row_spacing=12,
            column_spacing=12,
            valign=Gtk.Align.START,
            hexpand=True,
        )
        results_clamp = Adw.Clamp(
            maximum_size=1120,
            tightening_threshold=760,
            margin_top=18,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        results_clamp.set_child(self.flowbox)
        self.scrolled.set_child(results_clamp)
        self.results_stack.add_named(self.scrolled, "results")

        self.empty_page = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title=_("No matching models"),
            description=_("Try a different search or change the selected filter."),
            vexpand=True,
        )
        self.clear_filters_button = Gtk.Button(
            label=_("Clear search and filters"),
            css_classes=["suggested-action"],
            halign=Gtk.Align.CENTER,
        )
        self.clear_filters_button.connect("clicked", self._clear_filters)
        self.empty_page.set_child(self.clear_filters_button)
        self.results_stack.add_named(self.empty_page, "empty")

        self.loading_page = Adw.StatusPage(
            title=_("Loading models"),
            description=_("Preparing the model catalog…"),
            vexpand=True,
        )
        self.loading_spinner = Gtk.Spinner(spinning=True)
        self.loading_page.set_child(self.loading_spinner)
        self.results_stack.add_named(self.loading_page, "loading")

        self.error_page = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title=_("Could not load the model library"),
            vexpand=True,
        )
        retry_button = Gtk.Button(
            label=_("Try Again"),
            css_classes=["suggested-action"],
            halign=Gtk.Align.CENTER,
        )
        retry_button.connect("clicked", self.refresh_library)
        self.error_page.set_child(retry_button)
        self.results_stack.add_named(self.error_page, "error")
        self.results_stack.set_visible_child_name("loading")

    def load_css(self):
        display = Gdk.Display.get_default()
        if display is None:
            return

        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .model-library-card {
                border-radius: 12px;
            }
            .model-library-icon {
                min-width: 44px;
                min-height: 44px;
                border-radius: 12px;
                background-color: alpha(@accent_bg_color, 0.14);
                color: @accent_color;
            }
            .model-library-tag {
                padding: 2px 8px;
                border-radius: 9999px;
                background-color: alpha(currentColor, 0.07);
                font-size: 0.85em;
                font-weight: bold;
            }
            .model-library-tag:hover {
                opacity: 0.82;
            }
            .tag-blue { background-color: #3584e4; color: white; }
            .tag-green { background-color: #2ec27e; color: white; }
            .tag-orange { background-color: #ff7800; color: white; }
            .tag-purple { background-color: #9141ac; color: white; }
            .tag-red { background-color: #e01b24; color: white; }
            .tag-yellow { background-color: #f6d32d; color: black; }
            .tag-gray {
                background-color: alpha(currentColor, 0.1);
                color: currentColor;
            }
            .model-library-progress trough,
            .model-library-progress progress {
                min-height: 6px;
                border-radius: 9999px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._css_provider = provider

    @staticmethod
    def _apply_model_icon_color(icon: Gtk.Image, color: str | None):
        """Apply a handler-provided brand color without allowing arbitrary CSS."""
        if not color:
            return

        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            return

        red = round(rgba.red * 255)
        green = round(rgba.green * 255)
        blue = round(rgba.blue * 255)
        foreground = f"rgba({red}, {green}, {blue}, {rgba.alpha:.3f})"
        luminance = (
            0.2126 * rgba.red
            + 0.7152 * rgba.green
            + 0.0722 * rgba.blue
        )
        if luminance < 0.06:
            background = "rgba(255, 255, 255, 0.88)"
        elif luminance > 0.94:
            background = "rgba(0, 0, 0, 0.48)"
        else:
            background = f"rgba({red}, {green}, {blue}, 0.16)"

        provider = Gtk.CssProvider()
        provider.load_from_data(
            (
                ".model-library-icon {"
                f"color: {foreground}; background-color: {background};"
                "}"
            ).encode()
        )
        icon.get_style_context().add_provider(
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        icon._model_library_color_provider = provider

    def _replace_models(self, models):
        self.all_models = list(models or [])
        self.all_model_keys = [model.id for model in self.all_models]
        self.apply_filter()

    def load_models(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.results_stack.set_visible_child_name("loading")
        self.loading_spinner.start()
        try:
            models = self.handler.fetch_models()
        except Exception as error:  # noqa: BLE001 - handler boundary
            self.loading_spinner.stop()
            self._show_load_error(error)
            return GLib.SOURCE_REMOVE

        self.loading_spinner.stop()
        self._replace_models(models)
        return GLib.SOURCE_REMOVE

    def _show_load_error(self, error):
        detail = str(error).strip()
        if detail:
            description = _("The catalog could not be loaded: {error}").format(
                error=detail
            )
        else:
            description = _("The catalog could not be loaded. Please try again.")
        self.error_page.set_description(description)
        self.results_stack.set_visible_child_name("error")

    def _model_is_installed(self, model):
        try:
            installed = bool(self.handler.model_installed(model.id))
        except Exception:  # noqa: BLE001 - handler boundary
            installed = bool(model.is_installed)
        model.is_installed = installed
        return installed

    def apply_filter(self):
        query = self.search_entry.get_text().strip().casefold()
        filtered = []
        for model in self.all_models:
            installed = self._model_is_installed(model)
            if self.filter_mode == "installed" and not installed:
                continue
            if self.filter_mode == "available" and installed:
                continue

            searchable = (
                str(model.id),
                str(model.name),
                str(model.description or ""),
                *(str(tag) for tag in model.tags or []),
            )
            if query and not any(query in value.casefold() for value in searchable):
                continue
            filtered.append(model)

        self.filtered_models = filtered
        self.filtered_keys = [model.id for model in filtered]
        self._clear_flowbox()
        self.cards = {}
        self.loaded_count = 0
        self._update_result_count()

        if not filtered:
            self._show_empty_state()
            return

        self.results_stack.set_visible_child_name("results")
        self.load_more_models()

    def _clear_flowbox(self):
        child = self.flowbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.flowbox.remove(child)
            child = next_child

    def _update_result_count(self):
        count = len(self.filtered_models)
        total = len(self.all_models)
        has_filter = bool(self.search_entry.get_text().strip()) or self.filter_mode != "all"
        if has_filter and count != total:
            label = _("{count} of {total} models").format(count=count, total=total)
        else:
            template = _("{count} model") if count == 1 else _("{count} models")
            label = template.format(count=count)
        self.result_count_label.set_label(label)

    def _show_empty_state(self):
        if not self.all_models:
            self.empty_page.set_icon_name("brain-augemnted-symbolic")
            self.empty_page.set_title(_("No models available"))
            self.empty_page.set_description(
                _("Refresh the library to check for available models.")
            )
            self.clear_filters_button.set_visible(False)
        else:
            self.empty_page.set_icon_name("system-search-symbolic")
            self.empty_page.set_title(_("No matching models"))
            self.empty_page.set_description(
                _("Try a different search or change the selected filter.")
            )
            self.clear_filters_button.set_visible(True)
        self.results_stack.set_visible_child_name("empty")

    def load_more_models(self):
        total = len(self.filtered_models)
        if self.loaded_count >= total:
            return

        end = min(self.loaded_count + self.batch_size, total)
        for model in self.filtered_models[self.loaded_count:end]:
            card = self.create_card(model)
            self.flowbox.append(card)
            self.cards[model.id] = card
        self.loaded_count = end

    def on_scroll(self, adjustment):
        if (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 100
        ):
            self.load_more_models()

    def _on_filter_toggled(self, button, mode):
        if not button.get_active():
            return
        self.filter_mode = mode
        self.apply_filter()

    def _clear_filters(self, _button):
        self.search_entry.set_text("")
        self.filter_buttons["all"].set_active(True)
        if self.filter_mode == "all":
            self.apply_filter()

    def on_search_changed(self, _entry):
        self.apply_filter()

    def _filter_by_tag(self, _button, tag):
        self.search_entry.set_text(str(tag))
        self.search_entry.grab_focus()

    def on_tag_clicked(self, _gesture, _n_press, _x, _y, tag):
        """Compatibility callback for code that used the old tag gesture."""
        self.search_entry.set_text(str(tag))

    def get_tag_class(self, tag):
        tag = str(tag).lower()
        if tag.endswith(("b", "gb", "mb")):
            return "tag-blue"
        if len(tag) == 2:
            return "tag-green"
        if tag in ("code", "math", "coding"):
            return "tag-purple"
        if tag in ("huge", "large", "small", "tiny"):
            return "tag-orange"

        colors = (
            "tag-blue",
            "tag-green",
            "tag-orange",
            "tag-purple",
            "tag-red",
            "tag-yellow",
        )
        return colors[hash(tag) % len(colors)]

    def create_card(self, model: LibraryModel):
        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            width_request=260,
            height_request=286,
            hexpand=True,
        )
        card.add_css_class("card")
        card.add_css_class("model-library-card")
        card.model = model
        card.model_key = model.id

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=11,
            margin_top=16,
            margin_bottom=14,
            margin_start=16,
            margin_end=16,
            vexpand=True,
        )
        card.append(content)

        heading = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=11,
        )
        model_icon = Gtk.Image(
            icon_name=model.icon_name or "brain-augemnted-symbolic",
            pixel_size=22,
            halign=Gtk.Align.START,
            valign=Gtk.Align.START,
            width_request=44,
            height_request=44,
        )
        model_icon.add_css_class("model-library-icon")
        self._apply_model_icon_color(model_icon, model.icon_color)
        heading.append(model_icon)

        title_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            hexpand=True,
        )
        title_label = Gtk.Label(
            label=model.name,
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            hexpand=True,
        )
        title_label.add_css_class("heading")
        title_label.set_tooltip_text(model.name)
        title_box.append(title_label)
        if model.id != model.name:
            identifier = Gtk.Label(
                label=model.id,
                xalign=0,
                ellipsize=Pango.EllipsizeMode.END,
            )
            identifier.add_css_class("caption")
            identifier.add_css_class("dim-label")
            identifier.set_tooltip_text(model.id)
            title_box.append(identifier)
        heading.append(title_box)

        if model.is_pinned:
            pinned_icon = Gtk.Image(
                icon_name="user-bookmarks-symbolic",
                tooltip_text=_("Pinned"),
                valign=Gtk.Align.START,
            )
            pinned_icon.add_css_class("accent")
            heading.append(pinned_icon)

        card.installed_icon = Gtk.Image(
            icon_name="emblem-default-symbolic",
            tooltip_text=_("Installed"),
            valign=Gtk.Align.START,
        )
        card.installed_icon.add_css_class("success")
        heading.append(card.installed_icon)
        content.append(heading)

        description = Gtk.Label(
            label=model.description or _("No description available."),
            xalign=0,
            yalign=0,
            wrap=True,
            lines=3,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=40,
            vexpand=True,
            valign=Gtk.Align.START,
        )
        description.add_css_class("dim-label")
        content.append(description)

        tags = []
        seen_tags = set()
        for raw_tag in model.tags or []:
            tag = str(raw_tag).strip()
            normalized = tag.casefold()
            if not tag or normalized in seen_tags:
                continue
            seen_tags.add(normalized)
            tags.append(tag)

        if tags:
            tags_box = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE,
                min_children_per_line=1,
                max_children_per_line=2,
                row_spacing=4,
                column_spacing=4,
                halign=Gtk.Align.START,
                valign=Gtk.Align.START,
            )
            for tag in tags[: self.MAX_VISIBLE_TAGS]:
                tag_button = Gtk.Button(
                    label=tag,
                    css_classes=[
                        "flat",
                        "model-library-tag",
                        self.get_tag_class(tag),
                    ],
                    tooltip_text=_("Show models tagged {tag}").format(tag=tag),
                )
                tag_button.connect("clicked", self._filter_by_tag, tag)
                tags_box.append(tag_button)

            hidden_tags = tags[self.MAX_VISIBLE_TAGS :]
            if hidden_tags:
                hidden_count = len(hidden_tags)
                collapsed_label = _("+{count}").format(count=hidden_count)
                collapsed_tooltip = (
                    _("Show {count} more tag")
                    if hidden_count == 1
                    else _("Show {count} more tags")
                ).format(count=hidden_count)

                popover = Gtk.Popover(autohide=True, has_arrow=True)
                popover_content = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL,
                    spacing=8,
                    margin_top=12,
                    margin_bottom=12,
                    margin_start=12,
                    margin_end=12,
                )
                popover_title = Gtk.Label(label=_("More tags"), xalign=0)
                popover_title.add_css_class("heading")
                popover_content.append(popover_title)

                hidden_tags_box = Gtk.FlowBox(
                    selection_mode=Gtk.SelectionMode.NONE,
                    min_children_per_line=1,
                    max_children_per_line=2,
                    row_spacing=4,
                    column_spacing=4,
                    halign=Gtk.Align.START,
                    valign=Gtk.Align.START,
                )

                def filter_hidden_tag(button, tag):
                    popover.popdown()
                    self._filter_by_tag(button, tag)

                for tag in hidden_tags:
                    tag_button = Gtk.Button(
                        label=tag,
                        css_classes=[
                            "flat",
                            "model-library-tag",
                            self.get_tag_class(tag),
                        ],
                        tooltip_text=_("Show models tagged {tag}").format(tag=tag),
                    )
                    tag_button.connect("clicked", filter_hidden_tag, tag)
                    hidden_tags_box.append(tag_button)

                popover_scroller = Gtk.ScrolledWindow(
                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                    vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                    min_content_width=260,
                    max_content_width=360,
                    max_content_height=240,
                    propagate_natural_width=True,
                    propagate_natural_height=True,
                )
                popover_scroller.set_child(hidden_tags_box)
                popover_content.append(popover_scroller)
                popover.set_child(popover_content)

                overflow = Gtk.MenuButton(
                    label=collapsed_label,
                    tooltip_text=collapsed_tooltip,
                    css_classes=["flat", "model-library-tag", "tag-gray"],
                    always_show_arrow=False,
                    popover=popover,
                    valign=Gtk.Align.CENTER,
                )
                overflow.add_css_class("dim-label")
                tags_box.append(overflow)
            content.append(tags_box)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        card.status_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=150,
            vhomogeneous=False,
        )
        available_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        available_status = self._build_status(
            "folder-download-symbolic",
            _("Available"),
        )
        available_row.append(available_status)
        available_row.append(
            Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        )
        card.download_button = self._build_action_button(
            _("Download"),
            "folder-download-symbolic",
            ["suggested-action"],
        )
        card.download_button.connect(
            "clicked",
            lambda _button, key=model.id: self.install_model(key),
        )
        available_row.append(card.download_button)
        card.status_stack.add_named(available_row, "available")

        installed_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        installed_status = self._build_status(
            "emblem-default-symbolic",
            _("Installed"),
            "success",
        )
        installed_row.append(installed_status)
        installed_row.append(
            Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        )
        card.remove_button = self._build_action_button(
            _("Remove"),
            "user-trash-symbolic",
            ["flat", "destructive-action"],
        )
        card.remove_button.connect(
            "clicked",
            lambda _button, selected=model: self._confirm_remove_model(selected),
        )
        installed_row.append(card.remove_button)
        card.status_stack.add_named(installed_row, "installed")

        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=7,
        )
        progress_heading = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=7,
        )
        card.progress_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        progress_heading.append(card.progress_spinner)
        card.progress_status = Gtk.Label(
            label=_("Downloading…"),
            xalign=0,
            hexpand=True,
        )
        progress_heading.append(card.progress_status)
        card.progress_label = Gtk.Label(label="", xalign=1)
        card.progress_label.add_css_class("caption")
        card.progress_label.add_css_class("dim-label")
        progress_heading.append(card.progress_label)
        progress_box.append(progress_heading)
        card.progress_bar = Gtk.ProgressBar(hexpand=True)
        card.progress_bar.add_css_class("model-library-progress")
        progress_box.append(card.progress_bar)
        card.status_stack.add_named(progress_box, "progress")
        content.append(card.status_stack)

        card.installed_state = None
        self.update_card_state(card)
        return card

    @staticmethod
    def _build_status(icon_name, label, css_class=None):
        status = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
        )
        icon = Gtk.Image(icon_name=icon_name)
        text = Gtk.Label(label=label)
        if css_class is None:
            icon.add_css_class("dim-label")
            text.add_css_class("dim-label")
        else:
            icon.add_css_class(css_class)
            text.add_css_class(css_class)
        status.append(icon)
        status.append(text)
        return status

    @staticmethod
    def _build_action_button(label, icon_name, css_classes):
        button = Gtk.Button(css_classes=css_classes, valign=Gtk.Align.CENTER)
        content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.CENTER,
        )
        content.append(Gtk.Image(icon_name=icon_name))
        content.append(Gtk.Label(label=label))
        button.set_child(content)
        return button

    def _current_progress(self, key, active_task):
        if active_task is not None and active_task.fraction is not None:
            return min(max(float(active_task.fraction), 0.0), 1.0)
        try:
            progress = float(self.handler.get_percentage(key))
        except Exception:  # noqa: BLE001 - handler polling boundary
            return None
        if progress <= 0:
            return None
        return min(progress, 1.0)

    def update_card_state(self, card):
        key = card.model_key
        is_installed = self._model_is_installed(card.model)
        previous_installed = card.installed_state
        card.installed_state = is_installed
        card.installed_icon.set_visible(is_installed)

        operation = self.pending_operations.get(key)
        active_task = get_download_manager().find_active(
            f"model:{self.handler.key}:{key}"
        )
        progress = self._current_progress(key, active_task)
        downloading = operation == "install" or active_task is not None
        removing = operation == "remove"

        if downloading or removing or (progress is not None and progress < 1):
            card.status_stack.set_visible_child_name("progress")
            card.progress_spinner.start()
            if removing:
                card.progress_status.set_label(_("Removing…"))
                card.progress_label.set_label("")
                card.progress_bar.pulse()
            else:
                card.progress_status.set_label(_("Downloading…"))
                if progress is None:
                    card.progress_label.set_label("")
                    card.progress_bar.pulse()
                else:
                    card.progress_bar.set_fraction(progress)
                    card.progress_label.set_label(
                        _("{percent}%").format(percent=round(progress * 100))
                    )
        elif is_installed:
            card.progress_spinner.stop()
            card.status_stack.set_visible_child_name("installed")
        else:
            card.progress_spinner.stop()
            card.status_stack.set_visible_child_name("available")

        return previous_installed is not None and previous_installed != is_installed

    def _confirm_remove_model(self, model):
        if model.id in self.pending_operations:
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Remove {name}?").format(name=model.name),
            body=_("The downloaded model files will be removed from this device."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance(
            "remove",
            Adw.ResponseAppearance.DESTRUCTIVE,
        )

        def on_response(message, response):
            if response == "remove":
                self.install_model(model.id)
            message.close()

        dialog.connect("response", on_response)
        dialog.present()

    def install_model(self, key):
        if key in self.pending_operations:
            return
        try:
            installed = bool(self.handler.model_installed(key))
        except Exception as error:  # noqa: BLE001 - handler boundary
            self._toast_error(error)
            return

        operation = "remove" if installed else "install"
        self.pending_operations[key] = operation
        if operation == "install":
            self.pending_downloads.add(key)
        card = self.cards.get(key)
        if card is not None:
            self.update_card_state(card)

        target = self._remove_model_worker if operation == "remove" else self._install_model_worker
        threading.Thread(target=target, args=(key,), daemon=True).start()

    def _remove_model_worker(self, key):
        error = None
        try:
            self.handler.install_model(key)
        except Exception as caught_error:  # noqa: BLE001 - handler boundary
            error = caught_error
        GLib.idle_add(self._finish_model_operation, key, "remove", error)

    def _install_model_worker(self, key):
        manager = get_download_manager()
        source_id = f"model:{self.handler.key}:{key}"
        error = None
        try:
            if manager.has_active(source_id):
                return
            title = next(
                (model.name for model in self.all_models if model.id == key),
                key,
            )
            with manager.operation(
                _("Download {name}").format(name=title),
                kind=DownloadKind.MODEL,
                source_id=source_id,
                phase=_("Starting download"),
                cancellable=False,
            ) as task:
                monitoring = threading.Event()

                def monitor():
                    while not monitoring.wait(0.4):
                        try:
                            progress = float(self.handler.get_percentage(key))
                        except Exception:  # noqa: BLE001, S112 - polling boundary
                            continue
                        if progress > 0 and task.snapshot.transferred_bytes is None:
                            task.update(
                                phase=_("Downloading model"),
                                fraction=min(progress, 1.0),
                            )

                threading.Thread(target=monitor, daemon=True).start()
                try:
                    self.handler.install_model(key)
                finally:
                    monitoring.set()
                if not self.handler.model_installed(key):
                    raise RuntimeError(_("The model download did not complete"))
        except DownloadCancelled:
            pass
        except Exception as caught_error:  # noqa: BLE001 - handler boundary
            error = caught_error
        finally:
            GLib.idle_add(self._finish_model_operation, key, "install", error)

    def _finish_model_operation(self, key, operation, error=None):
        self.pending_operations.pop(key, None)
        self.pending_downloads.discard(key)
        if self.closed:
            return GLib.SOURCE_REMOVE

        card = self.cards.get(key)
        if card is not None:
            self.update_card_state(card)
        if error is not None:
            action = _("remove") if operation == "remove" else _("download")
            self._toast(
                _("Could not {action} the model: {error}").format(
                    action=action,
                    error=str(error) or _("Unknown error"),
                )
            )
        elif operation == "remove":
            self._toast(_("Model removed"))

        if self.filter_mode != "all":
            self.apply_filter()
        return GLib.SOURCE_REMOVE

    def update_downloads(self):
        if self.closed:
            self._download_source_id = None
            return GLib.SOURCE_REMOVE

        should_refilter = False
        for card in list(self.cards.values()):
            should_refilter = self.update_card_state(card) or should_refilter
        if should_refilter and self.filter_mode != "all":
            self.apply_filter()
        return GLib.SOURCE_CONTINUE

    def _set_refreshing(self, refreshing):
        self.refreshing = refreshing
        self.refresh_button.set_sensitive(not refreshing)
        if refreshing:
            self.refresh_spinner.start()
            self.refresh_stack.set_visible_child_name("spinner")
        else:
            self.refresh_spinner.stop()
            self.refresh_stack.set_visible_child_name("icon")

    @staticmethod
    def _call_handler_refresh(refresh):
        try:
            parameters = inspect.signature(refresh).parameters.values()
        except (TypeError, ValueError):
            refresh()
            return
        supports_manual = any(
            parameter.name == "manual"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_manual:
            refresh(manual=True)
        else:
            refresh()

    def refresh_library(self, _button):
        if self.refreshing or self.closed:
            return
        self._set_refreshing(True)

        def refresh_worker():
            models = None
            error = None
            try:
                refresh = getattr(self.handler, "get_models", None)
                if callable(refresh):
                    self._call_handler_refresh(refresh)
                models = list(self.handler.fetch_models() or [])
            except Exception as caught_error:  # noqa: BLE001 - handler boundary
                error = caught_error
            GLib.idle_add(self._finish_refresh, models, error)

        threading.Thread(target=refresh_worker, daemon=True).start()

    def _finish_refresh(self, models, error):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self._set_refreshing(False)
        if error is not None:
            if not self.all_models:
                self._show_load_error(error)
            self._toast(
                _("Could not refresh the model library: {error}").format(
                    error=str(error) or _("Unknown error")
                )
            )
            return GLib.SOURCE_REMOVE

        self._replace_models(models)
        self._toast(_("Model library refreshed"))
        return GLib.SOURCE_REMOVE

    def show_add_custom_model_dialog(self, _button):
        if self.custom_model_pending:
            return
        pull_model = getattr(self.handler, "pull_model", None)
        if not callable(pull_model):
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Add a Custom Model"),
            body=_(
                "Enter a model identifier or repository path supported by this provider."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Download"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry(
            placeholder_text=_("Model name or repository path"),
            margin_start=20,
            margin_end=20,
        )
        dialog.set_extra_child(entry)
        entry.connect("activate", lambda _entry: dialog.response("add"))

        def on_response(message, response):
            if response != "add":
                message.close()
                return
            model = entry.get_text().strip()
            if not model:
                entry.add_css_class("error")
                entry.grab_focus()
                return

            self.custom_model_pending = True
            if self.add_button is not None:
                self.add_button.set_sensitive(False)
            set_setting = getattr(self.handler, "set_setting", None)
            if callable(set_setting):
                set_setting("extra_model_name", model)
            threading.Thread(
                target=self._pull_custom_model_worker,
                args=(model,),
                daemon=True,
            ).start()
            message.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _pull_custom_model_worker(self, model):
        manager = get_download_manager()
        source_id = f"model:{self.handler.key}:{model}"
        error = None
        try:
            if manager.has_active(source_id):
                raise RuntimeError(_("This model is already being downloaded"))
            with manager.operation(
                _("Download {name}").format(name=model),
                kind=DownloadKind.MODEL,
                source_id=source_id,
                phase=_("Downloading model"),
                cancellable=False,
            ):
                self.handler.pull_model(model)
                if not self.handler.model_installed(model):
                    raise RuntimeError(_("The model download did not complete"))
        except DownloadCancelled:
            pass
        except Exception as caught_error:  # noqa: BLE001 - handler boundary
            error = caught_error
        GLib.idle_add(self._finish_custom_model, model, error)

    def _finish_custom_model(self, model, error):
        self.custom_model_pending = False
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.add_button is not None:
            self.add_button.set_sensitive(True)
        if error is not None:
            self._toast(
                _("Could not add {name}: {error}").format(
                    name=model,
                    error=str(error) or _("Unknown error"),
                )
            )
            return GLib.SOURCE_REMOVE

        self.load_models()
        self._toast(_("{name} was added to the model library").format(name=model))
        return GLib.SOURCE_REMOVE

    def _toast_error(self, error):
        self._toast(str(error) or _("Unknown error"))

    def _toast(self, title):
        if not self.closed:
            self.toast_overlay.add_toast(Adw.Toast(title=title))

    def _on_close_request(self, _window):
        self.closed = True
        if self._download_source_id is not None:
            GLib.source_remove(self._download_source_id)
            self._download_source_id = None
        return False
