"""Native settings UI for named subagents."""

import gettext

from gi.repository import Adw, GLib, Gtk

from ..constants import AVAILABLE_LLMS
from ..modes import ENABLE, REMOVE
from .widgets import ComboRowHelper, MultilineEntry

_ = gettext.gettext

_CUSTOM_MODEL = "__newelle_custom_model__"
_SECONDARY_MODEL = "__newelle_secondary_model__"


def _definition_value(definition, key, default=None):
    """Read a subagent field from either a mapping or a small data object."""
    if isinstance(definition, dict):
        return definition.get(key, default)
    return getattr(definition, key, default)


def _iter_definitions(manager):
    definitions = manager.get_subagents() or {}
    if isinstance(definitions, dict):
        items = definitions.items()
    else:
        items = (
            (_definition_value(definition, "id", ""), definition)
            for definition in definitions
        )
    return sorted(
        ((identifier, definition) for identifier, definition in items if identifier),
        key=lambda item: str(_definition_value(item[1], "name", item[0])).casefold(),
    )


def _is_enabled(manager, identifier, mode_aware):
    return manager.is_enabled(identifier, mode_aware=mode_aware)


class SubagentsPage(Adw.PreferencesPage):
    """Lazy-built Settings page for subagent CRUD and enablement."""

    def __init__(self, controller, settings_window):
        super().__init__(icon_name="system-users-symbolic", title=_("Subagents"))
        self.controller = controller
        self.settings_window = settings_window
        self.manager = getattr(controller, "subagent_manager", None)
        self.initialized = False
        self.rows = []

    def ensure_initialized(self):
        if self.initialized:
            return
        self.initialized = True

        self.group = Adw.PreferencesGroup(
            title=_("Subagents"),
            description=_(
                "Create focused agents with their own prompt, tools, skills, and model"
            ),
        )
        self.add_button = Gtk.Button(
            icon_name="list-add-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Create subagent"),
        )
        self.add_button.connect("clicked", self._on_create)
        self.group.set_header_suffix(self.add_button)
        self.add(self.group)
        self.controller.settings.connect(
            "changed::subagents-settings", lambda *args: GLib.idle_add(self.refresh)
        )
        self.refresh()

    def refresh(self):
        if not self.initialized:
            return
        self.manager = getattr(self.controller, "subagent_manager", None)
        self.add_button.set_sensitive(self.manager is not None)
        for row in self.rows:
            self.group.remove(row)
        self.rows = []

        if self.manager is None:
            self._add_row(
                Adw.ActionRow(
                    title=_("Subagents are unavailable"),
                    subtitle=_("The subagent manager could not be initialized"),
                )
            )
            return

        definitions = _iter_definitions(self.manager)
        if not definitions:
            self._add_row(
                Adw.ActionRow(
                    title=_("No subagents yet"),
                    subtitle=_("Create one to delegate focused tasks from a chat"),
                )
            )
            return

        for identifier, definition in definitions:
            self._add_row(self._build_row(identifier, definition))

    def _add_row(self, row):
        self.group.add(row)
        self.rows.append(row)

    def _build_row(self, identifier, definition):
        name = _definition_value(definition, "name", identifier)
        description = _definition_value(definition, "description", "")
        tools = _definition_value(definition, "tools", []) or []
        skills = _definition_value(definition, "skills", []) or []
        provider = _definition_value(definition, "provider")
        model = _definition_value(definition, "model")
        use_secondary_model = bool(
            _definition_value(definition, "use_secondary_model", False)
        )
        extension = _definition_value(
            definition,
            "source_extension",
            _definition_value(definition, "extension_id"),
        )
        read_only = bool(_definition_value(definition, "read_only", False))

        provider_title = _("Main model")
        if use_secondary_model:
            provider_title = _("Secondary LLM")
        elif provider:
            provider_title = AVAILABLE_LLMS.get(provider, {}).get("title", provider)
        model_title = model or (
            _("resolved at launch")
            if not provider and not use_secondary_model
            else _("provider default")
        )
        summary = _("{provider} · {tools} tools · {skills} skills").format(
            provider=provider_title,
            tools=len(tools),
            skills=len(skills),
        )

        subtitle = f"{description}\n{summary}" if description else summary
        row = Adw.ExpanderRow(title=name, subtitle=subtitle)
        row.add_prefix(
            Gtk.Image(icon_name="system-users-symbolic", css_classes=["dim-label"])
        )

        warning = self._mode_warning(identifier)
        if warning:
            icon = Gtk.Image(
                icon_name="warning-outline-symbolic", css_classes=["warning"]
            )
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_tooltip_text(warning)
            row.add_suffix(icon)

        enabled = _is_enabled(self.manager, identifier, False)
        toggle = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        toggle.connect("state-set", self._on_toggled, identifier)
        row.add_suffix(toggle)

        edit_button = Gtk.Button(
            icon_name=(
                "document-edit-symbolic" if not read_only else "view-reveal-symbolic"
            ),
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Edit subagent") if not read_only else _("View subagent"),
        )
        edit_button.connect("clicked", self._on_edit, identifier)
        row.add_suffix(edit_button)

        row.add_row(
            Adw.ActionRow(
                title=_("Model"), subtitle=f"{provider_title} · {model_title}"
            )
        )
        row.add_row(
            Adw.ActionRow(
                title=_("Capabilities"),
                subtitle=_("{tools} tools, {skills} skills").format(
                    tools=len(tools), skills=len(skills)
                ),
            )
        )
        row.add_row(
            Adw.ActionRow(
                title=_("Origin"),
                subtitle=str(extension) if extension else _("User"),
            )
        )
        if not read_only:
            delete_row = Adw.ActionRow(
                title=_("Delete subagent"),
                subtitle=_("Existing sessions can still be resumed"),
            )
            delete_button = Gtk.Button(
                label=_("Delete"),
                css_classes=["destructive-action"],
                valign=Gtk.Align.CENTER,
            )
            delete_button.connect("clicked", self._confirm_delete, identifier, name)
            delete_row.add_suffix(delete_button)
            row.add_row(delete_row)
        return row

    def _mode_warning(self, identifier):
        mode_manager = getattr(self.controller, "mode_manager", None)
        if mode_manager is None:
            return None
        mode_name = mode_manager.get_active_mode_name()
        state = mode_manager.get_active_mode().get("subagents", {}).get(identifier)
        if state not in (ENABLE, REMOVE):
            return None
        base_enabled = _is_enabled(self.manager, identifier, False)
        resolved_enabled = _is_enabled(self.manager, identifier, True)
        if base_enabled == resolved_enabled:
            return None
        detail = _("forced on") if resolved_enabled else _("forced off")
        return _('The "{}" mode overrides this subagent: {}').format(
            mode_name, detail
        )

    def _on_toggled(self, _switch, state, identifier):
        self.manager.set_enabled(identifier, bool(state))
        GLib.idle_add(self.refresh)
        return False

    def _on_create(self, _button):
        self._open_editor(None)

    def _on_edit(self, _button, identifier):
        self._open_editor(identifier)

    def _open_editor(self, identifier):
        dialog = SubagentEditorDialog(
            self.controller,
            identifier=identifier,
            on_saved=self.refresh,
        )
        # Settings is modal. Presenting this dialog without its Settings
        # parent creates a separate toplevel that is blocked by Settings'
        # modal input grab, so the editor appears completely frozen.
        dialog.present(self.settings_window)

    def _confirm_delete(self, _button, identifier, name):
        dialog = Adw.MessageDialog(
            transient_for=self.settings_window,
            modal=True,
            heading=_("Delete subagent?"),
            body=_("Delete \"{}\"? Existing session history will be kept.").format(
                name
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(current, response):
            if response == "delete":
                self.manager.delete(identifier)
                self.refresh()
            current.destroy()

        dialog.connect("response", on_response)
        dialog.present()


class SubagentEditorDialog(Adw.PreferencesDialog):
    """Create, edit, or inspect one named subagent."""

    def __init__(self, controller, identifier=None, on_saved=None):
        super().__init__()
        self.controller = controller
        self.manager = controller.subagent_manager
        self.identifier = identifier
        self.on_saved = on_saved
        self.existing = self.manager.get_subagent(identifier) if identifier else None
        self.read_only = bool(_definition_value(self.existing, "read_only", False))
        self._model_rows = []
        self._combo_helpers = []
        self._tool_switches = {}
        self._tool_groups = {}
        self._tool_to_group = {}
        self._syncing_tool_groups = False
        self._selected_model_choice = None
        self._custom_model_value = (
            _definition_value(self.existing, "model", "") or ""
        )

        self.working = {
            "name": _definition_value(self.existing, "name", ""),
            "description": _definition_value(self.existing, "description", ""),
            "system_prompt": _definition_value(self.existing, "system_prompt", ""),
            "include_agents_md": bool(
                _definition_value(self.existing, "include_agents_md", False)
            ),
            "tools": set(_definition_value(self.existing, "tools", []) or []),
            "skills": set(_definition_value(self.existing, "skills", []) or []),
            "provider": _definition_value(self.existing, "provider"),
            "model": _definition_value(self.existing, "model"),
            "use_secondary_model": bool(
                _definition_value(
                    self.existing, "use_secondary_model", False
                )
            ),
            "default_on": bool(_definition_value(self.existing, "default_on", True)),
        }

        if self.read_only:
            self.set_title(_("Subagent details"))
        elif identifier:
            self.set_title(_("Edit Subagent"))
        else:
            self.set_title(_("New Subagent"))
        self.set_search_enabled(False)
        self.set_content_width(760)
        self.set_content_height(820)

        self.general_page = self._add_page(
            _("General"), "settings-symbolic", "general"
        )
        self.prompt_page = self._add_page(
            _("Prompt"), "question-round-outline-symbolic", "prompt"
        )
        self.tools_page = self._add_page(_("Tools"), "tools-symbolic", "tools")
        self.skills_page = self._add_page(_("Skills"), "skills-symbolic", "skills")
        self.model_page = self._add_page(
            _("Model"), "brain-augemnted-symbolic", "model"
        )

        self._build_general()
        self._build_prompt()
        self._build_tools()
        self._build_skills()
        self._build_model()
        if not self.read_only:
            for page in (
                self.general_page,
                self.prompt_page,
                self.tools_page,
                self.skills_page,
                self.model_page,
            ):
                self._build_actions(page)

    def _add_page(self, title, icon_name, name):
        page = Adw.PreferencesPage(title=title, icon_name=icon_name, name=name)
        self.add(page)
        return page

    def _build_general(self):
        group = Adw.PreferencesGroup(
            title=_("Subagent details"),
            description=_("Give the subagent a clear role and purpose"),
        )
        self.general_page.add(group)

        self.name_row = Adw.EntryRow(title=_("Name"), text=self.working["name"])
        self.name_row.set_editable(not self.read_only)
        self.name_row.connect("changed", self._on_name_changed)
        group.add(self.name_row)

        self.description_row = Adw.EntryRow(
            title=_("Description"), text=self.working["description"]
        )
        self.description_row.set_editable(not self.read_only)
        self.description_row.connect("changed", self._on_description_changed)
        group.add(self.description_row)

        self.error_row = Adw.ActionRow()
        self.error_row.add_css_class("error")
        self.error_row.set_visible(False)
        group.add(self.error_row)

    def _build_prompt(self):
        group = Adw.PreferencesGroup(
            title=_("System prompt"),
            description=_(
                "Define the subagent's role, constraints, and expected output"
            ),
        )
        self.prompt_page.add(group)
        editor_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=6,
            margin_end=6,
            margin_top=8,
            margin_bottom=12,
        )
        self.prompt_entry = MultilineEntry()
        self.prompt_entry.set_editor_height(480)
        self.prompt_entry.set_text(self.working["system_prompt"])
        self.prompt_entry.set_sensitive(not self.read_only)
        self.prompt_entry.set_on_change(self._on_prompt_changed)
        editor_box.append(self.prompt_entry)
        group.add(editor_box)

        self.agents_md_row = Adw.SwitchRow(
            title=_("Include AGENTS.md"),
            subtitle=_(
                "Add the content of the AGENTS.md file in the current "
                "directory to the subagent's prompt"
            ),
        )
        self.agents_md_row.set_active(self.working["include_agents_md"])
        self.agents_md_row.set_sensitive(not self.read_only)
        self.agents_md_row.connect("notify::active", self._on_agents_md_toggled)
        group.add(self.agents_md_row)

    def _build_tools(self):
        group = Adw.PreferencesGroup(
            title=_("Tools"),
            description=_("Choose which tools this subagent can call"),
        )
        self.tools_page.add(group)
        tools = [
            tool
            for tool in self.controller.tools.get_all_tools()
            if tool.name != "run_subagent"
        ]
        available_names = {tool.name for tool in tools}
        if not tools:
            group.add(Adw.ActionRow(title=_("No tools available")))
        else:
            grouped = {}
            ungrouped = []
            for tool in tools:
                if tool.tools_group:
                    grouped.setdefault(tool.tools_group, []).append(tool)
                else:
                    ungrouped.append(tool)

            for group_name, group_tools in grouped.items():
                expander = Adw.ExpanderRow(
                    title=group_name,
                    subtitle=_("{} tools").format(len(group_tools)),
                )
                expander.add_prefix(
                    Gtk.Image(icon_name="folder-symbolic", css_classes=["dim-label"])
                )
                tool_names = [tool.name for tool in group_tools]
                for tool in group_tools:
                    self._tool_to_group[tool.name] = group_name
                    expander.add_row(self._capability_row(tool, "tools"))
                group_toggle = Gtk.Switch(
                    active=all(
                        name in self.working["tools"] for name in tool_names
                    ),
                    sensitive=not self.read_only,
                    valign=Gtk.Align.CENTER,
                    tooltip_text=_("Enable or disable all tools in this group"),
                )
                group_toggle.connect(
                    "state-set",
                    self._on_tool_group_toggled,
                    tuple(tool_names),
                )
                expander.add_suffix(group_toggle)
                self._tool_groups[group_name] = (tuple(tool_names), group_toggle)
                group.add(expander)
            for tool in ungrouped:
                group.add(self._capability_row(tool, "tools"))

        for name in sorted(self.working["tools"] - available_names):
            group.add(self._missing_capability_row(name, "tools"))

    def _build_skills(self):
        group = Adw.PreferencesGroup(
            title=_("Skills"),
            description=_("Choose which skill instructions are available"),
        )
        self.skills_page.add(group)
        skill_manager = getattr(self.controller, "skill_manager", None)
        skills = list(skill_manager.skills.values()) if skill_manager else []
        available_names = {skill.name for skill in skills}
        if not skills:
            group.add(Adw.ActionRow(title=_("No skills available")))
        else:
            for skill in skills:
                group.add(self._capability_row(skill, "skills"))
        for name in sorted(self.working["skills"] - available_names):
            group.add(self._missing_capability_row(name, "skills"))

    def _capability_row(self, item, target):
        key = item.name
        if target == "tools":
            title = item.title
            icon_name = item.icon_name or "tools-symbolic"
        else:
            title = item.name
            icon_name = "skills-symbolic"
        row = Adw.ActionRow(title=title, subtitle=getattr(item, "description", ""))
        row.add_prefix(Gtk.Image(icon_name=icon_name))
        toggle = Gtk.Switch(
            active=key in self.working[target],
            sensitive=not self.read_only,
            valign=Gtk.Align.CENTER,
        )
        toggle.connect("state-set", self._on_capability_toggled, target, key)
        row.add_suffix(toggle)
        if target == "tools":
            self._tool_switches[key] = toggle
        return row

    def _missing_capability_row(self, name, target):
        kind = _("tool") if target == "tools" else _("skill")
        row = Adw.ActionRow(
            title=_("{} (unavailable)").format(name),
            subtitle=_(
                "This saved {kind} is no longer installed. Disable it to repair the subagent."
            ).format(kind=kind),
        )
        row.add_prefix(Gtk.Image(icon_name="warning-outline-symbolic"))
        toggle = Gtk.Switch(
            active=True,
            sensitive=not self.read_only,
            valign=Gtk.Align.CENTER,
        )
        toggle.connect("state-set", self._on_capability_toggled, target, name)
        row.add_suffix(toggle)
        return row

    def _build_model(self):
        provider_group = Adw.PreferencesGroup(
            title=_("Model provider"),
            description=_(
                "Subagents share the selected provider's credentials and connection settings"
            ),
        )
        self.model_page.add(provider_group)

        options = [
            (_("Main model"), ""),
            (_("Secondary LLM"), _SECONDARY_MODEL),
        ]
        options.extend(
            (descriptor.get("title", key), key)
            for key, descriptor in AVAILABLE_LLMS.items()
        )
        provider = (
            _SECONDARY_MODEL
            if self.working["use_secondary_model"]
            else self.working["provider"] or ""
        )
        if (
            provider
            and provider != _SECONDARY_MODEL
            and provider not in AVAILABLE_LLMS
        ):
            options.append((provider, provider))
        self.provider_row = Adw.ComboRow(
            title=_("Provider"),
            subtitle=_(
                "Use the main model, secondary model, or a dedicated provider"
            ),
            sensitive=not self.read_only,
        )
        helper = ComboRowHelper(self.provider_row, tuple(options), provider)
        helper.connect("changed", self._on_provider_changed)
        self._combo_helpers.append(helper)
        provider_group.add(self.provider_row)

        self.model_group = Adw.PreferencesGroup(title=_("Model"))
        self.model_page.add(self.model_group)
        self._rebuild_model_rows()

    def _rebuild_model_rows(self):
        for row in self._model_rows:
            self.model_group.remove(row)
        self._model_rows = []

        provider = self.working["provider"]
        use_secondary_model = self.working["use_secondary_model"]
        resolved_provider = (
            self.controller.newelle_settings.secondary_language_model
            if use_secondary_model
            else provider or self.controller.newelle_settings.language_model
        )
        if not provider:
            source_title = (
                _("Uses the secondary model")
                if use_secondary_model
                else _("Uses the main model")
            )
            info_row = Adw.ActionRow(
                title=source_title,
                subtitle=_(
                    "The provider is resolved at launch; an optional model override can be kept"
                ),
            )
            self.model_group.add(info_row)
            self._model_rows.append(info_row)

        models = self._get_provider_models(
            resolved_provider,
            secondary=use_secondary_model,
        )
        current = self.working["model"] or ""
        known_values = {value for _label, value in models}
        selected = current if current in known_values else (_CUSTOM_MODEL if current else "")
        options = [
            (
                _("Secondary LLM default")
                if use_secondary_model
                else _("Main model default")
                if not provider
                else _("Provider default"),
                "",
            ),
            *models,
            (_("Custom model ID"), _CUSTOM_MODEL),
        ]
        model_row = Adw.ComboRow(
            title=_("Model"),
            subtitle=_("Use the provider default, a cached model, or a custom ID"),
            sensitive=not self.read_only,
        )
        helper = ComboRowHelper(model_row, tuple(options), selected)
        helper.connect("changed", self._on_model_changed)
        self._combo_helpers.append(helper)
        self.model_group.add(model_row)
        self._model_rows.append(model_row)

        custom_row = Adw.EntryRow(
            title=_("Custom model ID"),
            text=self._custom_model_value,
            sensitive=not self.read_only,
        )
        custom_row.connect("changed", self._on_custom_model_changed)
        custom_row.set_visible(selected == _CUSTOM_MODEL)
        self.model_group.add(custom_row)
        self._model_rows.append(custom_row)
        self.custom_model_row = custom_row
        self._selected_model_choice = selected

    def _get_provider_models(self, provider, secondary=False):
        if provider not in AVAILABLE_LLMS:
            return []
        try:
            handler = self.controller.handlers.get_object(
                AVAILABLE_LLMS,
                provider,
                secondary,
            )
            raw_models = handler.get_models_list() or ()
        except Exception:
            return []
        models = []
        seen = set()
        for model in raw_models:
            if isinstance(model, (tuple, list)) and len(model) >= 2:
                label, value = str(model[0]), str(model[1])
            else:
                label = value = str(model)
            if value and value not in seen:
                models.append((label, value))
                seen.add(value)
        return models

    def _build_actions(self, page):
        group = Adw.PreferencesGroup()
        page.add(group)
        button = Gtk.Button(
            label=_("Save Subagent"),
            css_classes=["suggested-action"],
            hexpand=True,
        )
        button.connect("clicked", self._on_save)
        group.add(button)

    def _on_name_changed(self, row):
        self.working["name"] = row.get_text().strip()
        self.name_row.remove_css_class("error")
        self.error_row.set_visible(False)

    def _on_description_changed(self, row):
        self.working["description"] = row.get_text()

    def _on_prompt_changed(self, entry):
        self.working["system_prompt"] = entry.get_text()

    def _on_agents_md_toggled(self, row, _param):
        self.working["include_agents_md"] = bool(row.get_active())

    def _on_capability_toggled(self, _switch, state, target, key):
        if state:
            self.working[target].add(key)
        else:
            self.working[target].discard(key)
        if target == "tools":
            self._sync_tool_group(key)

    def _on_tool_group_toggled(self, _switch, state, tool_names):
        if self._syncing_tool_groups:
            return False
        self._syncing_tool_groups = True
        try:
            for name in tool_names:
                if state:
                    self.working["tools"].add(name)
                else:
                    self.working["tools"].discard(name)
                child_switch = self._tool_switches.get(name)
                if child_switch is not None and child_switch.get_active() != state:
                    child_switch.set_active(state)
        finally:
            self._syncing_tool_groups = False
        return False

    def _sync_tool_group(self, tool_name):
        if self._syncing_tool_groups:
            return
        group_name = self._tool_to_group.get(tool_name)
        group = self._tool_groups.get(group_name)
        if group is None:
            return
        tool_names, group_toggle = group
        all_enabled = all(
            name in self.working["tools"] for name in tool_names
        )
        if group_toggle.get_active() == all_enabled:
            return
        self._syncing_tool_groups = True
        try:
            group_toggle.set_active(all_enabled)
        finally:
            self._syncing_tool_groups = False

    def _on_provider_changed(self, _helper, provider):
        self.working["use_secondary_model"] = provider == _SECONDARY_MODEL
        self.working["provider"] = (
            provider
            if provider and provider != _SECONDARY_MODEL
            else None
        )
        self.working["model"] = None
        self._custom_model_value = ""
        self._rebuild_model_rows()

    def _on_model_changed(self, _helper, model):
        self._selected_model_choice = model
        self.custom_model_row.set_visible(model == _CUSTOM_MODEL)
        if model == _CUSTOM_MODEL:
            self.working["model"] = self._custom_model_value or None
        else:
            self.working["model"] = model or None

    def _on_custom_model_changed(self, row):
        self._custom_model_value = row.get_text().strip()
        if self._selected_model_choice == _CUSTOM_MODEL:
            self.working["model"] = self._custom_model_value or None

    def _show_error(self, message):
        self.error_row.set_title(str(message))
        self.error_row.set_visible(True)
        self.name_row.add_css_class("error")
        self.set_visible_page(self.general_page)

    def _on_save(self, _button):
        if not self.working["name"]:
            self._show_error(_("Enter a subagent name."))
            return
        definition = {
            "name": self.working["name"],
            "description": self.working["description"],
            "system_prompt": self.working["system_prompt"],
            "include_agents_md": self.working["include_agents_md"],
            "tools": sorted(self.working["tools"]),
            "skills": sorted(self.working["skills"]),
            "provider": self.working["provider"],
            "model": self.working["model"],
            "use_secondary_model": self.working["use_secondary_model"],
            "default_on": self.working["default_on"],
        }
        try:
            if self.identifier:
                self.manager.update(self.identifier, definition)
            else:
                self.manager.create(definition)
        except (TypeError, ValueError, KeyError) as error:
            self._show_error(error)
            return
        if self.on_saved is not None:
            self.on_saved()
        self.close()
