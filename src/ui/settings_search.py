"""Search across settings pages, including provider rows built on demand."""

import unicodedata
from dataclasses import dataclass
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango


def _normalize(text):
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def _plain(text, use_markup=True):
    if not text or not use_markup:
        return text or ""
    try:
        return Pango.parse_markup(text, -1, "\0")[2]
    except GLib.Error:
        return text


@dataclass
class SearchResult:
    page_key: str
    title: str
    description: str
    location: tuple
    target: Gtk.Widget
    setting_path: tuple = ()


class SettingsSearch:
    def __init__(self, window):
        self.window = window
        self.matches = []
        self.first_result = None
        self.buttons = []
        self.entry = Gtk.SearchEntry(
            placeholder_text=_("Search settings…"),
            tooltip_text=_("Search all settings and providers"),
            hexpand=True,
        )
        self.entry.connect("search-changed", self._search_changed)
        self.entry.connect("activate", self._activate_first)
        self.entry.connect("stop-search", lambda _entry: self.close())
        self.bar = Gtk.SearchBar(show_close_button=True)
        clamp = Adw.Clamp(maximum_size=650, hexpand=True)
        clamp.set_child(self.entry)
        self.bar.set_child(clamp)
        self.bar.connect_entry(self.entry)
        # Bubble-phase capture leaves typing in settings' editors untouched.
        self.bar.set_key_capture_widget(window)
        self.bar.connect("notify::search-mode-enabled", self._mode_changed)

        self.results_page = Adw.PreferencesPage()
        self.results_group = None
        self.empty = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title=_("No settings found"),
            description=_("Try a different word or a provider name."),
        )
        self.stack = Gtk.Stack(
            vexpand=True,
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=150,
        )
        self.stack.add_named(window.content_stack, "settings")
        self.stack.add_named(self.results_page, "results")
        self.stack.add_named(self.empty, "empty")
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.append(self.bar)
        self.content.append(self.stack)

        shortcuts = Gtk.EventControllerKey()
        shortcuts.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        shortcuts.connect("key-pressed", self._key_pressed)
        window.add_controller(shortcuts)
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._entry_key_pressed)
        self.entry.add_controller(entry_keys)

    def create_button(self):
        button = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text=_("Search settings (Ctrl+F)"),
            css_classes=["flat"],
        )
        button.bind_property(
            "active", self.bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self.buttons.append(button)
        return button

    def close(self):
        self.bar.set_search_mode(False)

    def _mode_changed(self, bar, _pspec):
        if bar.get_search_mode():
            self.window.split_view.set_show_content(True)
            self.entry.grab_focus()
        else:
            self.entry.set_text("")
            self.matches = []
            self.first_result = None
            self.stack.set_visible_child_name("settings")
            self._restore_title()
            for button in self.buttons:
                if button.get_mapped():
                    button.grab_focus()
                    break

    def _restore_title(self):
        key = self.window.content_stack.get_visible_child_name()
        if key in self.window.navigation_pages:
            self.window.content_navigation_page.set_title(
                self.window.navigation_pages[key][0]
            )

    def _key_pressed(self, _controller, keyval, _keycode, state):
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and modifiers == Gdk.ModifierType.CONTROL_MASK:
            self.bar.set_search_mode(True)
            self.entry.grab_focus()
            return True
        if keyval == Gdk.KEY_Escape and self.bar.get_search_mode():
            self.close()
            return True
        return False

    def _entry_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Down:
            self._search_changed(self.entry)
            if self.first_result is not None:
                self.first_result.grab_focus()
                return True
        return False

    def _walk(self, widget, location=()):
        """Yield public preference rows, excluding expander header internals."""
        if not widget.get_visible():
            return
        if isinstance(widget, Adw.PreferencesGroup):
            title = _plain(widget.get_title())
            if title:
                location += (title,)
        if isinstance(widget, Adw.PreferencesRow):
            # ExpanderRow contains an ActionRow implementing its own header.
            if widget.has_css_class("header") and widget.get_ancestor(Adw.ExpanderRow):
                return
            if isinstance(widget.get_parent(), Gtk.ListBox):
                title = _plain(widget.get_title(), widget.get_use_markup())
                if title:
                    yield widget, title, location
                    if isinstance(widget, Adw.ExpanderRow):
                        location += (title,)
            if not isinstance(widget, Adw.ExpanderRow):
                return
        child = widget.get_first_child()
        while child is not None:
            yield from self._walk(child, location)
            child = child.get_next_sibling()

    def _pending_results(self, page_key, target, settings, location, path=()):
        for setting in settings:
            title = _plain(setting.get("title", ""))
            setting_path = path + (setting.get("key"),)
            if title:
                yield SearchResult(
                    page_key, title, _plain(setting.get("description", "")),
                    location, target, setting_path,
                )
            if setting.get("type") == "nested":
                yield from self._pending_results(
                    page_key, target, setting.get("extra_settings", []),
                    location + (title,), setting_path,
                )

    def _collect_results(self):
        # Defer building less-used pages until an actual search, as navigation does.
        self.window.ensure_tools_page_initialized()
        self.window.ensure_permissions_page_initialized()
        self.window.ensure_skills_page_initialized()
        self.window.SubagentsPage.ensure_initialized()
        self.window.ensure_mcp_page_initialized()
        pending = {
            state["row"]: state["pending_extra_settings"]
            for state in self.window.settingsrows.values()
            if "row" in state and "pending_extra_settings" in state
        }
        for page_key, (page_title, page) in self.window.navigation_pages.items():
            yield SearchResult(page_key, page_title, "", (), page)
            for row, title, location in self._walk(page, (page_title,)):
                description = _plain(row.get_subtitle(), row.get_use_markup()) if isinstance(
                    row, (Adw.ActionRow, Adw.ExpanderRow)
                ) else ""
                yield SearchResult(page_key, title, description, location, row)
                if row in pending:
                    yield from self._pending_results(
                        page_key, row, pending[row], location + (title,),
                    )

    def _search_changed(self, _entry):
        query = _normalize(self.entry.get_text().strip())
        if not self.bar.get_search_mode() or not query:
            self.stack.set_visible_child_name("settings")
            self._restore_title()
            self.matches = []
            self.first_result = None
            return
        tokens = query.split()
        self.matches = [
            result for result in self._collect_results()
            if all(token in _normalize(" ".join((
                result.title, result.description, *result.location,
            ))) for token in tokens)
        ]
        # Exact titles and title matches precede matches only in descriptions/paths.
        self.matches.sort(key=lambda result: (
            _normalize(result.title) != query,
            not _normalize(result.title).startswith(query),
            not all(token in _normalize(result.title) for token in tokens),
        ))
        self.window.content_navigation_page.set_title(_("Search Settings"))
        if self.results_group is not None:
            self.results_page.remove(self.results_group)
        count = len(self.matches)
        self.results_group = Adw.PreferencesGroup(
            title=_("Search results") + f" ({count})",
        )
        self.results_page.add(self.results_group)
        self.first_result = None
        for result in self.matches:
            page = self.window.navigation_pages[result.page_key][1]
            row = Adw.ActionRow(
                title=result.title,
                subtitle=" › ".join(result.location),
                use_markup=False,
                title_lines=2,
                subtitle_lines=1,
                tooltip_text=result.description or result.title,
                activatable=True,
            )
            row.add_prefix(Gtk.Image(icon_name=page.get_icon_name()))
            row.add_suffix(Gtk.Image(
                icon_name="go-next-symbolic", css_classes=["dim-label"],
            ))
            row.connect("activated", lambda _row, item=result: self._open_result(item))
            self.results_group.add(row)
            if self.first_result is None:
                self.first_result = row
        self.stack.set_visible_child_name("results" if count else "empty")

    def _activate_first(self, _entry):
        # Enter can precede SearchEntry's debounced search-changed signal.
        self._search_changed(self.entry)
        if self.matches:
            self._open_result(self.matches[0])

    def _open_result(self, result):
        self.close()
        self.window.navigation_list.select_row(self.window.navigation_rows[result.page_key])
        self.window.split_view.set_show_content(True)
        target = result.target
        page = self.window.navigation_pages[result.page_key][1]
        if target != page and not target.is_ancestor(page):
            return  # A provider may have rebuilt its rows while results were shown.
        ancestors = []
        child = target
        parent = target.get_parent()
        while parent is not None and parent != page:
            ancestors.append((parent, child))
            child = parent
            parent = parent.get_parent()
        for parent, child in reversed(ancestors):
            if isinstance(parent, Adw.ExpanderRow):
                parent.set_expanded(True)
            elif isinstance(parent, (Gtk.Stack, Adw.ViewStack)):
                parent.set_visible_child(child)
        # Expanding a provider synchronously builds the settings indexed above.
        for setting_key in result.setting_path:
            if isinstance(target, Adw.ExpanderRow):
                target.set_expanded(True)
            match = next((
                row for row, _title, _location in self._walk(target)
                if row != target and getattr(row, "settings_search_key", None) == setting_key
            ), None)
            if match is None:
                break
            target = match
        if isinstance(target, Adw.ExpanderRow):
            target.set_expanded(True)
        # Allow stack/expander animations to finish before measuring the row.
        GLib.timeout_add(250, self._focus_result, target)

    def _focus_result(self, target):
        if target.get_root() != self.window or not target.get_mapped():
            return GLib.SOURCE_REMOVE
        target.set_focusable(True)
        target.grab_focus()
        scroll = target.get_ancestor(Gtk.ScrolledWindow)
        if scroll is not None:
            success, bounds = target.compute_bounds(scroll.get_child())
            if success:
                adjustment = scroll.get_vadjustment()
                height = min(bounds.get_height(), 72) if isinstance(
                    target, Adw.ExpanderRow
                ) else bounds.get_height()
                adjustment.clamp_page(bounds.get_y() - 12, bounds.get_y() + height + 12)
        return GLib.SOURCE_REMOVE
