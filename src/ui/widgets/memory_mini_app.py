"""Canvas mini app to inspect and manage the long term memory."""

import threading
from datetime import datetime

from gi.repository import Adw, GLib, Gtk, Pango

from ...handlers.memory.memory_store import KIND_CONVERSATION, KIND_FACT

MAX_ROWS = 200
REFRESH_DEBOUNCE_MS = 300
SEARCH_DEBOUNCE_MS = 400


class MemoryMiniApp(Gtk.Box):
    """Browse, search, edit and delete memories and the user summary"""

    def __init__(self, handler, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.handler = handler
        self._generation = 0
        self._refresh_source = None
        self._search_source = None
        self._settings_handlers = []
        self._summary_loading = False
        self._summary_dirty = False
        self._scope = handler.get_scope()
        self.filters = [
            (KIND_FACT, False, _("Facts")),
            (KIND_CONVERSATION, False, _("Conversations")),
            (None, True, _("Archived")),
        ]

        self.toast_overlay = Adw.ToastOverlay(vexpand=True)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=600,
                          margin_start=18, margin_end=18, margin_top=18, margin_bottom=24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        clamp.set_child(content)
        scroller.set_child(clamp)
        self.toast_overlay.set_child(scroller)
        self.append(self.toast_overlay)

        # Overview
        self.overview_group = Adw.PreferencesGroup(title=_("Long Term Memory"))
        header_buttons = Gtk.Box(spacing=6)
        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"],
                                    tooltip_text=_("Refresh"), valign=Gtk.Align.CENTER)
        refresh_button.connect("clicked", lambda _b: self.refresh())
        rebuild_button = Gtk.Button(icon_name="update-symbolic", css_classes=["flat"],
                                    tooltip_text=_("Rebuild the search index"), valign=Gtk.Align.CENTER)
        rebuild_button.connect("clicked", self._on_rebuild)
        header_buttons.append(refresh_button)
        header_buttons.append(rebuild_button)
        self.overview_group.set_header_suffix(header_buttons)
        self.scope_row = Adw.ActionRow(title=handler.get_scope_label(), subtitle="")
        self.scope_row.add_prefix(Gtk.Image(icon_name="user-bookmarks-symbolic"))
        self.overview_group.add(self.scope_row)
        content.append(self.overview_group)

        # Summary
        self.summary_group = Adw.PreferencesGroup(
            title=_("User Summary"),
            description=_("Short description of the user, updated automatically and added to the prompt"))
        self.summary_buffer = Gtk.TextBuffer()
        self.summary_buffer.connect("changed", self._on_summary_changed)
        summary_view = Gtk.TextView(buffer=self.summary_buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        summary_scroller = Gtk.ScrolledWindow(child=summary_view, min_content_height=120,
                                              max_content_height=320, propagate_natural_height=True,
                                              hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card"])
        self.summary_group.add(summary_scroller)
        summary_buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        self.regenerate_button = Gtk.Button(label=_("Regenerate"), tooltip_text=_("Rewrite the summary from the saved facts"))
        self.regenerate_button.connect("clicked", self._on_regenerate)
        self.save_summary_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"], sensitive=False)
        self.save_summary_button.connect("clicked", self._on_save_summary)
        summary_buttons.append(self.regenerate_button)
        summary_buttons.append(self.save_summary_button)
        self.summary_group.add(summary_buttons)
        content.append(self.summary_group)

        # Memories
        self.memories_group = Adw.PreferencesGroup(title=_("Memories"))
        add_button = Gtk.Button(icon_name="plus-symbolic", css_classes=["flat"],
                                tooltip_text=_("Add a memory"), valign=Gtk.Align.CENTER)
        add_button.connect("clicked", self._on_add)
        self.memories_group.set_header_suffix(add_button)
        toolbar = Gtk.Box(spacing=6, margin_bottom=12)
        self.search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Search memories"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.filter_dropdown = Gtk.DropDown.new_from_strings([label for _k, _a, label in self.filters])
        self.filter_dropdown.connect("notify::selected", lambda *_a: self.refresh())
        toolbar.append(self.search_entry)
        toolbar.append(self.filter_dropdown)
        self.memories_group.add(toolbar)
        self.list_box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.placeholder = Gtk.Label(label=_("No memories yet"), css_classes=["dim-label"],
                                     margin_top=24, margin_bottom=24)
        self.list_box.set_placeholder(self.placeholder)
        self.memories_group.add(self.list_box)
        content.append(self.memories_group)

        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)

    # Lifecycle
    def _on_realize(self, *_args):
        self.handler.add_change_listener(self._on_handler_changed)
        for key in ("changed::current-profile", "changed::memory-settings"):
            self._settings_handlers.append(self.handler.settings.connect(key, lambda *_a: self._on_handler_changed()))
        self.refresh()

    def _on_unrealize(self, *_args):
        self.handler.remove_change_listener(self._on_handler_changed)
        for handler_id in self._settings_handlers:
            self.handler.settings.disconnect(handler_id)
        self._settings_handlers = []

    def _on_handler_changed(self):
        # May be called from any thread
        GLib.idle_add(self._schedule_refresh)

    def _schedule_refresh(self):
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
        self._refresh_source = GLib.timeout_add(REFRESH_DEBOUNCE_MS, self._debounced_refresh)
        return False

    def _debounced_refresh(self):
        self._refresh_source = None
        self.refresh()
        return False

    # Data loading
    def _current_filter(self):
        index = self.filter_dropdown.get_selected()
        if index >= len(self.filters):
            index = 0
        return self.filters[index]

    def refresh(self):
        self._generation += 1
        generation = self._generation
        scope = self.handler.get_scope()
        kind, archived, _label = self._current_filter()
        query = self.search_entry.get_text()

        def load():
            try:
                records = self.handler.list_memories(scope, kind, archived, query)[:MAX_ROWS]
                strengths = [self.handler.get_strength(record) for record in records]
                summary, _updated = self.handler.store.get_summary(scope)
                counts = self.handler.store.counts(scope)
            except Exception as e:
                GLib.idle_add(self._toast, _("Could not load memories: {error}").format(error=e))
                return
            GLib.idle_add(self._apply, generation, scope, records, strengths, summary, counts)
        threading.Thread(target=load, daemon=True).start()

    def _apply(self, generation, scope, records, strengths, summary, counts):
        if generation != self._generation:
            return False
        scope_changed = scope != self._scope
        self._scope = scope
        self.scope_row.set_title(self.handler.get_scope_label(scope))
        facts, conversations = counts[KIND_FACT], counts[KIND_CONVERSATION]
        self.scope_row.set_subtitle(
            _("{facts} facts, {conversations} conversations, {archived} archived").format(
                facts=facts["active"], conversations=conversations["active"],
                archived=facts["archived"] + conversations["archived"]))
        if scope_changed or not self._summary_dirty:
            self._set_summary_text(summary)

        self.list_box.remove_all()
        self.placeholder.set_label(_("No matching memories") if self.search_entry.get_text().strip() else _("No memories yet"))
        for record, strength in zip(records, strengths, strict=True):
            self.list_box.append(self._build_row(record, strength))
        return False

    def _set_summary_text(self, text):
        self._summary_loading = True
        self.summary_buffer.set_text(text or "")
        self._summary_loading = False
        self._summary_dirty = False
        self.save_summary_button.set_sensitive(False)

    # Rows
    def _build_row(self, record, strength):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=12, margin_start=12, margin_end=6, margin_top=10, margin_bottom=10)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        text_label = Gtk.Label(label=record.text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                               xalign=0, selectable=True, lines=8, ellipsize=Pango.EllipsizeMode.END)
        text_box.append(text_label)

        meta = [_("Fact") if record.kind == KIND_FACT else _("Conversation"),
                datetime.fromtimestamp(record.created_at).strftime("%Y-%m-%d %H:%M")]
        if record.access_count:
            meta.append(_("recalled {count} times").format(count=record.access_count))
        if record.pinned:
            meta.append(_("pinned"))
        meta_box = Gtk.Box(spacing=8)
        meta_box.append(Gtk.Label(label=" · ".join(meta), xalign=0, css_classes=["dim-label", "caption"]))
        level = Gtk.LevelBar(min_value=0, max_value=1, value=max(0.0, min(1.0, strength)),
                             width_request=80, valign=Gtk.Align.CENTER)
        level.set_tooltip_text(_("Strength: {value}%").format(value=round(strength * 100)))
        meta_box.append(level)
        text_box.append(meta_box)
        box.append(text_box)

        buttons = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        pin = Gtk.ToggleButton(icon_name="view-pin-symbolic", active=record.pinned, css_classes=["flat"],
                               tooltip_text=_("Pinned memories never fade"))
        pin.connect("toggled", lambda b: self._run(self.handler.set_pinned, record.id, b.get_active()))
        buttons.append(pin)
        edit = Gtk.Button(icon_name="document-edit-symbolic", css_classes=["flat"], tooltip_text=_("Edit"))
        edit.connect("clicked", lambda _b: self._edit_dialog(_("Edit Memory"), record.text,
                                                             lambda text: self.handler.edit_memory(record.id, text)))
        buttons.append(edit)
        if record.archived:
            restore = Gtk.Button(icon_name="edit-undo-symbolic", css_classes=["flat"], tooltip_text=_("Restore"))
            restore.connect("clicked", lambda _b: self._run(self.handler.set_archived, record.id, False, toast=_("Memory restored")))
            buttons.append(restore)
        delete = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"], tooltip_text=_("Delete"))
        delete.connect("clicked", lambda _b: self._confirm_delete(record))
        buttons.append(delete)
        box.append(buttons)
        row.set_child(box)
        return row

    # Actions
    def _run(self, func, *args, toast=None):
        def run():
            try:
                func(*args)
            except Exception as e:
                GLib.idle_add(self._toast, str(e))
                return
            if toast:
                GLib.idle_add(self._toast, toast)
        threading.Thread(target=run, daemon=True).start()

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))
        return False

    def _on_search_changed(self, _entry):
        if self._search_source is not None:
            GLib.source_remove(self._search_source)

        def search():
            self._search_source = None
            self.refresh()
            return False
        self._search_source = GLib.timeout_add(SEARCH_DEBOUNCE_MS, search)

    def _on_rebuild(self, _button):
        self._run(self.handler.rebuild_index, toast=_("Rebuilding the memory index in background"))

    def _on_summary_changed(self, _buffer):
        if self._summary_loading:
            return
        self._summary_dirty = True
        self.save_summary_button.set_sensitive(True)

    def _on_save_summary(self, _button):
        start, end = self.summary_buffer.get_bounds()
        text = self.summary_buffer.get_text(start, end, False)
        self._summary_dirty = False
        self.save_summary_button.set_sensitive(False)
        self._run(self.handler.set_summary, text, self._scope, toast=_("Summary saved"))

    def _on_regenerate(self, button):
        button.set_sensitive(False)
        scope = self._scope

        def run():
            try:
                self.handler.regenerate_summary(scope)
                GLib.idle_add(self._toast, _("Summary regenerated"))
            except Exception as e:
                GLib.idle_add(self._toast, _("Could not regenerate the summary: {error}").format(error=e))
            GLib.idle_add(button.set_sensitive, True)
            self._summary_dirty = False
            GLib.idle_add(self._schedule_refresh)
        threading.Thread(target=run, daemon=True).start()

    def _on_add(self, _button):
        scope = self._scope
        self._edit_dialog(_("Add Memory"), "", lambda text: self.handler.add_memory(text, KIND_FACT, scope),
                          toast=_("Memory saved"))

    def _edit_dialog(self, heading, text, callback, toast=None):
        dialog = Adw.AlertDialog(heading=heading)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        buffer = Gtk.TextBuffer(text=text)
        view = Gtk.TextView(buffer=buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        dialog.set_extra_child(Gtk.ScrolledWindow(child=view, min_content_height=160, min_content_width=360,
                                                  hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card"]))

        def on_response(_dialog, response):
            if response != "save":
                return
            start, end = buffer.get_bounds()
            new_text = buffer.get_text(start, end, False).strip()
            if new_text and new_text != text.strip():
                self._run(callback, new_text, toast=toast)
        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _confirm_delete(self, record):
        dialog = Adw.AlertDialog(heading=_("Delete Memory?"), body=_("This memory will be permanently deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "delete" and self._run(
            self.handler.delete_memory, record.id, toast=_("Memory deleted")))
        dialog.present(self.get_root())
